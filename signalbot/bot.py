"""Bot Telegram : commandes + tâches automatiques (alertes, signaux, news, rapports)."""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime, time, timedelta, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from . import ai, data, formatting as fmt
from .config import INSTRUMENTS, Instrument, settings
from .moves import current_move, detect_move, explain
from .news import events_between, fetch_calendar, fetch_news, relevant_news
from .regime import Regime, classify
from .signals import Signal, evaluate
from .state import State
from .stocks import scan

log = logging.getLogger(__name__)
state = State(settings.state_file)

MOVE_COOLDOWN = 45 * 60
WEEKDAYS = (1, 2, 3, 4, 5)  # python-telegram-bot : 0 = dimanche

HELP = """<b>SignalBot — Nasdaq & Or</b>

<b>Marché</b>
/marche — type de marché (tendance, range, indécis…) pour NQ et l'or
/signaux — meilleurs setups d'achat/vente du moment
/pourquoi nq | or — pourquoi le prix bouge en ce moment
/brief — plan de match du jour

<b>News</b>
/news — news importantes des dernières heures
/calendrier — annonces économiques US à fort impact cette semaine

<b>Actions (moyen/long terme, CELI)</b>
/stocks — opportunités : earnings à venir + grosses baisses
/stock AAPL — analyse d'une action
/watchlist — liste suivie · /ajouter TICKER · /retirer TICKER

<b>Automatique</b>
🚨 Alerte + explication dès qu'un gros mouvement arrive
🎯 Signaux de qualité (score ≥ {min_score}/100)
📰 News à fort impact · ⏰ Rappel {reminder} min avant les annonces
☀️ Brief chaque matin ({brief}) · 📈 Rapport actions ({stocks})"""


# ------------------------------------------------------------------ utilitaires

def _authorized(chat_id: int) -> bool:
    if settings.allowed_chat_ids:
        return chat_id in settings.allowed_chat_ids
    return not state.chats or chat_id in state.chats


def restricted(handler):
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat is None or not _authorized(chat.id):
            log.warning("Accès refusé pour le chat %s", chat.id if chat else "?")
            return
        try:
            await handler(update, context)
        except Exception:
            log.exception("Erreur dans %s", handler.__name__)
            await chat.send_message("❌ Oups, une erreur est survenue. Réessaie dans un instant.")
    return wrapper


def _chunks(text: str, limit: int = 3900) -> list[str]:
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit and current:
            parts.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        parts.append(current)
    return parts


async def send(bot, chat_id: int, text: str) -> None:
    for part in _chunks(text):
        await bot.send_message(chat_id, part, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)


async def reply(update: Update, text: str) -> None:
    await send(update.get_bot(), update.effective_chat.id, text)


async def broadcast(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    for chat_id in settings.allowed_chat_ids or state.chats:
        try:
            await send(context.bot, chat_id, text)
        except Exception:
            log.exception("Envoi impossible vers %s", chat_id)


def _analyze(inst: Instrument) -> tuple[dict, dict[str, Regime]]:
    frames = data.multi_timeframe(inst.ticker)
    regimes = {tf: r for tf, df in frames.items() if (r := classify(df, tf))}
    return frames, regimes


def _upcoming_event_titles(minutes: int = 60) -> list[str]:
    now = datetime.now(timezone.utc)
    events = events_between(fetch_calendar(), now, now + timedelta(minutes=minutes))
    return [f"{ev.title} ({ev.time.astimezone(settings.timezone):%H:%M})" for ev in events]


def _find_instrument(arg: str | None) -> list[Instrument]:
    if not arg:
        return list(INSTRUMENTS.values())
    a = arg.lower()
    if a in ("or", "gold", "xau", "xauusd", "gc"):
        return [INSTRUMENTS["XAU"]]
    if a in ("nq", "nasdaq", "ndx", "us100", "nas100"):
        return [INSTRUMENTS["NQ"]]
    return list(INSTRUMENTS.values())


# ------------------------------------------------------------------ commandes

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _authorized(chat_id):
        await update.effective_chat.send_message("⛔ Ce bot est privé.")
        return
    state.add_chat(chat_id)
    await reply(update, HELP.format(min_score=settings.min_signal_score,
                                    reminder=settings.calendar_reminder_minutes,
                                    brief=settings.morning_brief_time,
                                    stocks=settings.stocks_report_time)
                + f"\n\n✅ Tu es abonné aux alertes (chat id <code>{chat_id}</code>).")


@restricted
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, HELP.format(min_score=settings.min_signal_score,
                                    reminder=settings.calendar_reminder_minutes,
                                    brief=settings.morning_brief_time,
                                    stocks=settings.stocks_report_time))


@restricted
async def cmd_marche(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    insts = _find_instrument(context.args[0] if context.args else None)
    blocks = []
    for inst in insts:
        _, regimes = await asyncio.to_thread(_analyze, inst)
        blocks.append(fmt.regime_block(inst, regimes))
    await reply(update, f"🧭 <b>Type de marché</b> · {fmt.now_str(settings.timezone)}\n\n"
                + "\n\n".join(blocks))


@restricted
async def cmd_signaux(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    upcoming = await asyncio.to_thread(_upcoming_event_titles)
    msgs = []
    for inst in _find_instrument(context.args[0] if context.args else None):
        frames, regimes = await asyncio.to_thread(_analyze, inst)
        sigs = evaluate(inst.key, frames, regimes, upcoming)
        if sigs:
            msgs.append(fmt.signal_message(sigs[0], inst))
        else:
            msgs.append(f"⏳ <b>{fmt.e(inst.name)}</b> : aucun setup propre en ce moment. "
                        "Pas de signal = pas de trade, c'est aussi une décision 😉")
    await reply(update, "\n\n—————\n\n".join(msgs))


@restricted
async def cmd_pourquoi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_chat.send_message("🔍 J'analyse ce qui se passe…")
    for inst in _find_instrument(context.args[0] if context.args else None):
        move = await asyncio.to_thread(current_move, inst)
        if not move:
            await reply(update, f"{fmt.e(inst.name)} : données indisponibles.")
            continue
        await reply(update, await explain(move, alert=False))


@restricted
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = await asyncio.to_thread(fetch_news, 12)
    top = relevant_news(items, None, min_score=5)[:10]
    if not top:
        await reply(update, "Aucune news importante trouvée dans les 12 dernières heures.")
        return
    await reply(update, "📰 <b>News importantes (12 h)</b>\n\n"
                + "\n".join(fmt.news_line(n, settings.timezone) for n in top))


@restricted
async def cmd_calendrier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    events = await asyncio.to_thread(fetch_calendar)
    now = datetime.now(timezone.utc)
    upcoming = [ev for ev in events if ev.time >= now - timedelta(hours=2)]
    if not upcoming:
        await reply(update, "Aucune annonce US à fort impact à venir cette semaine "
                            "(ou calendrier indisponible).")
        return
    await reply(update, "🗓️ <b>Annonces US à fort impact</b>\n"
                "<i>Ces annonces font souvent bouger fort le Nasdaq ET l'or.</i>\n\n"
                + "\n".join(fmt.event_line(ev, settings.timezone) for ev in upcoming))


async def _stocks_report(tickers: list[str], top_n: int = 5, min_score: int = 0) -> str:
    reports = await asyncio.to_thread(scan, tickers, settings.dip_threshold_pct,
                                      settings.earnings_lookahead_days)
    earnings = sorted((r for r in reports if r.earnings_score > min_score),
                      key=lambda r: r.earnings_score, reverse=True)[:top_n]
    dips = sorted((r for r in reports if r.dip_score > min_score),
                  key=lambda r: r.dip_score, reverse=True)[:top_n]
    lines = [f"📈 <b>Opportunités actions</b> · {len(reports)} titres analysés", ""]
    lines.append("<b>📅 Résultats (earnings) à venir — historique positif</b>")
    lines += [fmt.stock_card(r, "earnings") for r in earnings] or ["   Rien d'intéressant."]
    lines += ["", f"<b>📉 Grosses baisses (≥ {settings.dip_threshold_pct:.0f}% du sommet) "
                  "sur de bonnes compagnies</b>"]
    lines += [fmt.stock_card(r, "dip") for r in dips] or ["   Rien d'intéressant."]
    lines += ["", "💡 Long terme : acheter en plusieurs fois (DCA) réduit le risque de mal "
                  "timer le creux.", fmt.DISCLAIMER]
    return "\n".join(lines)


@restricted
async def cmd_stocks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tickers = state.stocks(settings.stocks)
    await update.effective_chat.send_message(
        f"⏳ Analyse de {len(tickers)} actions, ça peut prendre une minute…")
    await reply(update, await _stocks_report(tickers))


@restricted
async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await reply(update, "Utilisation : /stock AAPL (ou RY.TO pour la bourse de Toronto)")
        return
    from .stocks import analyze
    ticker = context.args[0].upper()
    rep = await asyncio.to_thread(analyze, ticker, settings.dip_threshold_pct,
                                  settings.earnings_lookahead_days)
    if not rep:
        await reply(update, f"Impossible d'analyser {fmt.e(ticker)} (ticker invalide ?).")
        return
    await reply(update, fmt.stock_detail(rep) + "\n\n" + fmt.DISCLAIMER)


@restricted
async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tickers = state.stocks(settings.stocks)
    await reply(update, f"👀 <b>Watchlist ({len(tickers)})</b>\n" + fmt.e(", ".join(tickers)))


@restricted
async def cmd_ajouter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tickers = state.stocks(settings.stocks)
    added = [t.upper() for t in context.args if t.upper() not in tickers]
    state.set_stocks(tickers + added)
    await reply(update, f"✅ Ajouté : {fmt.e(', '.join(added)) or 'rien'}")


@restricted
async def cmd_retirer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remove = {t.upper() for t in context.args}
    state.set_stocks([t for t in state.stocks(settings.stocks) if t not in remove])
    await reply(update, f"🗑️ Retiré : {fmt.e(', '.join(sorted(remove))) or 'rien'}")


async def _brief_text() -> str:
    tz = settings.timezone
    now = datetime.now(timezone.utc)
    blocks = []
    for inst in INSTRUMENTS.values():
        _, regimes = await asyncio.to_thread(_analyze, inst)
        blocks.append(fmt.regime_block(inst, regimes))
    events = await asyncio.to_thread(fetch_calendar)
    local_today = datetime.now(tz).date()
    today = [ev for ev in events if ev.time.astimezone(tz).date() == local_today]
    items = await asyncio.to_thread(fetch_news, 14)
    top = relevant_news(items, None, min_score=6)[:6]

    lines = [f"☀️ <b>Brief du {fmt.fr_date(datetime.now(tz), '%A %d %B')}</b>", "", *blocks, ""]
    lines.append("🗓️ <b>Annonces du jour</b>")
    lines += [fmt.event_line(ev, tz) for ev in today] or ["• Aucune annonce US majeure."]
    lines += ["", "📰 <b>À retenir</b>"]
    lines += [fmt.news_line(n, tz) for n in top] or ["• Rien de majeur."]

    summary = await ai.ask(
        f"Nous sommes le {now:%Y-%m-%d}. Fais-moi un plan de match TRÈS court (6 puces max) "
        "pour le Nasdaq 100 et l'or aujourd'hui : le sentiment général, les annonces/événements "
        "à surveiller et les heures clés (heure de l'Est), et le risque principal.\n\n"
        f"Annonces du jour : {', '.join(f'{e.title} {e.time.astimezone(tz):%H:%M}' for e in today) or 'aucune'}\n"
        f"Titres récents : {' | '.join(n.title for n in top) or 'aucun'}"
    )
    if summary:
        lines += ["", "🧠 <b>Plan de match</b>", fmt.e(summary)]
    lines += ["", fmt.DISCLAIMER]
    return "\n".join(lines)


@restricted
async def cmd_brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_chat.send_message("⏳ Je prépare le brief…")
    await reply(update, await _brief_text())


# ------------------------------------------------------------------ tâches automatiques

async def job_moves(context: ContextTypes.DEFAULT_TYPE) -> None:
    for inst in INSTRUMENTS.values():
        move = await asyncio.to_thread(detect_move, inst)
        if not move:
            continue
        key = f"move:{inst.key}:{move.direction}"
        if state.cooling_down(key, MOVE_COOLDOWN):
            continue
        state.touch(key)
        log.info("Gros mouvement %s %+.2f%% (%s)", inst.key, move.change, move.window)
        await broadcast(context, await explain(move))


async def job_signals(context: ContextTypes.DEFAULT_TYPE) -> None:
    upcoming = await asyncio.to_thread(_upcoming_event_titles)
    regimes_state = state.data.setdefault("regimes", {})
    for inst in INSTRUMENTS.values():
        frames, regimes = await asyncio.to_thread(_analyze, inst)

        # Changement de régime H4 (ex. range -> tendance)
        h4 = regimes.get("H4")
        if h4:
            previous = regimes_state.get(inst.key)
            regimes_state[inst.key] = h4.code
            state.save()
            if previous and previous != h4.code:
                await broadcast(context, "🔄 <b>Changement de régime</b>\n\n"
                                + fmt.regime_block(inst, regimes))

        sigs: list[Signal] = evaluate(inst.key, frames, regimes, upcoming)
        for sig in sigs:
            if sig.score < settings.min_signal_score:
                break
            key = f"sig:{inst.key}:{sig.direction}"
            if state.cooling_down(key, settings.signal_cooldown_hours * 3600):
                continue
            state.touch(key)
            await broadcast(context, fmt.signal_message(sig, inst))
            break


async def job_news(context: ContextTypes.DEFAULT_TYPE) -> None:
    items = await asyncio.to_thread(fetch_news, 3)
    first_run = not any(k.startswith("news:") for k in state.data["seen"])
    fresh = [n for n in relevant_news(items, None, min_score=settings.min_news_score)
             if not state.seen(f"news:{n.id}")]
    to_send = fresh[:3] if first_run else fresh[:5]
    for n in items:  # tout marquer comme vu pour ne pas renvoyer plus tard
        if not state.seen(f"news:{n.id}"):
            state.mark_seen(f"news:{n.id}")
    if to_send:
        await broadcast(context, "📰 <b>News à fort impact</b>\n\n"
                        + "\n".join(fmt.news_line(n, settings.timezone) for n in to_send))


async def job_calendar(context: ContextTypes.DEFAULT_TYPE) -> None:
    events = await asyncio.to_thread(fetch_calendar)
    now = datetime.now(timezone.utc)
    soon = events_between(events, now, now + timedelta(minutes=settings.calendar_reminder_minutes))
    for ev in soon:
        key = f"cal:{ev.id}"
        if state.seen(key):
            continue
        state.mark_seen(key)
        minutes = max(1, int((ev.time - now).total_seconds() // 60))
        await broadcast(context,
                        f"⏰ <b>Annonce à fort impact dans {minutes} min</b>\n\n"
                        + fmt.event_line(ev, settings.timezone)
                        + "\n\nAttends-toi à de la volatilité sur le Nasdaq et l'or. "
                          "Les spreads s'élargissent et les stops sautent facilement : "
                          "prudence avec les positions ouvertes.")


async def job_brief(context: ContextTypes.DEFAULT_TYPE) -> None:
    await broadcast(context, await _brief_text())


async def job_stocks(context: ContextTypes.DEFAULT_TYPE) -> None:
    await broadcast(context, await _stocks_report(state.stocks(settings.stocks), min_score=55))


def _parse_time(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m), tzinfo=settings.timezone)


def build_app() -> Application:
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant (voir .env.example)")
    app = Application.builder().token(settings.telegram_token).build()

    for name, fn in [("start", cmd_start), ("aide", cmd_help), ("help", cmd_help),
                     ("marche", cmd_marche), ("signaux", cmd_signaux), ("pourquoi", cmd_pourquoi),
                     ("news", cmd_news), ("calendrier", cmd_calendrier), ("stocks", cmd_stocks),
                     ("stock", cmd_stock), ("watchlist", cmd_watchlist), ("ajouter", cmd_ajouter),
                     ("retirer", cmd_retirer), ("brief", cmd_brief)]:
        app.add_handler(CommandHandler(name, fn))

    jq = app.job_queue
    jq.run_repeating(job_moves, interval=settings.move_check_seconds, first=20)
    jq.run_repeating(job_signals, interval=settings.signal_check_seconds, first=40)
    jq.run_repeating(job_news, interval=settings.news_check_seconds, first=60)
    jq.run_repeating(job_calendar, interval=settings.calendar_check_seconds, first=30)
    jq.run_daily(job_brief, time=_parse_time(settings.morning_brief_time), days=WEEKDAYS)
    jq.run_daily(job_stocks, time=_parse_time(settings.stocks_report_time), days=WEEKDAYS)

    if ai.enabled():
        log.info("Explications IA activées (%s)", settings.claude_model)
    else:
        log.info("ANTHROPIC_API_KEY absent : explications basées sur des règles")
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)
