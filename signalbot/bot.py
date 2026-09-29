"""Bot Telegram : commandes + tâches automatiques (alertes, signaux, news, rapports)."""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime, time, timedelta, timezone

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from . import ai, data, formatting as fmt
from .config import INSTRUMENTS, Instrument, settings
from .moves import current_move, detect_move, explain
from .news import NewsItem, events_between, fetch_calendar, fetch_news, relevant_news
from .news_explain import explain as explain_news
from .regime import Regime, classify
from .signals import Signal, evaluate
from .state import State
from .stocks import analyze, scan

log = logging.getLogger(__name__)
state = State(settings.state_file)

MOVE_COOLDOWN = 45 * 60
WEEKDAYS = (1, 2, 3, 4, 5)  # python-telegram-bot : 0 = dimanche

HELP = """<b>SignalBot — Nasdaq & Or</b>
👇 Le plus simple : utilise les <b>boutons en bas</b> (ou /menu pour les réafficher).

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


# Menu à boutons (clavier toujours visible en bas de Telegram)
BTN_MARCHE = "📊 Marché"
BTN_SIGNAUX = "🎯 Signaux"
BTN_NEWS = "📰 News"
BTN_CALENDRIER = "🗓️ Calendrier"
BTN_POURQUOI = "❓ Pourquoi ça bouge"
BTN_BRIEF = "☀️ Brief du jour"
BTN_STOCKS = "📈 Opportunités actions"
BTN_STOCK = "🔎 Analyser une action"
BTN_WATCHLIST = "👀 Watchlist"
BTN_AIDE = "ℹ️ Aide"

MAIN_MENU = ReplyKeyboardMarkup(
    [[BTN_MARCHE, BTN_SIGNAUX],
     [BTN_NEWS, BTN_CALENDRIER],
     [BTN_POURQUOI, BTN_BRIEF],
     [BTN_STOCKS, BTN_STOCK],
     [BTN_WATCHLIST, BTN_AIDE]],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Choisis une option 👇",
)

WHY_MENU = InlineKeyboardMarkup([[
    InlineKeyboardButton("📊 Nasdaq", callback_data="why:NQ"),
    InlineKeyboardButton("🥇 Or", callback_data="why:XAU"),
    InlineKeyboardButton("🔀 Les deux", callback_data="why:ALL"),
]])

WATCHLIST_MENU = InlineKeyboardMarkup([[
    InlineKeyboardButton("➕ Ajouter", callback_data="wl:add"),
    InlineKeyboardButton("➖ Retirer", callback_data="wl:del"),
    InlineKeyboardButton("📈 Scanner", callback_data="wl:scan"),
]])

BOT_COMMANDS = [
    BotCommand("menu", "🏠 Afficher le menu"),
    BotCommand("marche", "📊 Type de marché (tendance, range…)"),
    BotCommand("signaux", "🎯 Signaux d'achat / vente"),
    BotCommand("news", "📰 News importantes expliquées"),
    BotCommand("calendrier", "🗓️ Annonces économiques"),
    BotCommand("pourquoi", "❓ Pourquoi ça bouge (nq / or)"),
    BotCommand("brief", "☀️ Plan de match du jour"),
    BotCommand("stocks", "📈 Opportunités actions"),
    BotCommand("stock", "🔎 Analyser une action (ex. /stock AAPL)"),
    BotCommand("watchlist", "👀 Liste d'actions suivies"),
    BotCommand("aide", "ℹ️ Aide"),
]


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


async def send(bot, chat_id: int, text: str, reply_markup=None) -> None:
    parts = _chunks(text)
    for i, part in enumerate(parts):
        await bot.send_message(chat_id, part, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True,
                               reply_markup=reply_markup if i == len(parts) - 1 else None)


async def reply(update: Update, text: str, reply_markup=None) -> None:
    await send(update.get_bot(), update.effective_chat.id, text, reply_markup)


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


def _arg(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.args[0] if context.args else None


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
    await reply(update, _help_text()
                + f"\n\n✅ Tu es abonné aux alertes (chat id <code>{chat_id}</code>)."
                + "\n👇 Utilise les boutons en bas pour naviguer.", MAIN_MENU)


def _help_text() -> str:
    return HELP.format(min_score=settings.min_signal_score,
                       reminder=settings.calendar_reminder_minutes,
                       brief=settings.morning_brief_time,
                       stocks=settings.stocks_report_time)


@restricted
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, _help_text(), MAIN_MENU)


@restricted
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, "🏠 <b>Menu principal</b> — choisis une option 👇", MAIN_MENU)


@restricted
async def cmd_marche(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    insts = _find_instrument(_arg(context))
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
    for inst in _find_instrument(_arg(context)):
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
    if not context.args:
        await reply(update, "❓ <b>Quel marché veux-tu comprendre ?</b>", WHY_MENU)
        return
    await _pourquoi(update, _arg(context))


async def _pourquoi(update: Update, arg: str | None) -> None:
    await update.effective_chat.send_message("🔍 J'analyse ce qui se passe…")
    for inst in _find_instrument(arg):
        move = await asyncio.to_thread(current_move, inst)
        if not move:
            await reply(update, f"{fmt.e(inst.name)} : données indisponibles.")
            continue
        await reply(update, await explain(move, alert=False))


NEWS_SEPARATOR = "\n\n〰️〰️〰️〰️〰️\n\n"


async def _news_message(title: str, items: list[NewsItem]) -> str:
    """Message complet : chaque news traduite et expliquée en français."""
    explanations = await explain_news(items)
    cards = [fmt.news_card(n, explanations[n.id], settings.timezone) for n in items]
    footer = ""
    if not ai.enabled():
        footer = ("\n\n💡 <i>Ajoute une clé ANTHROPIC_API_KEY dans .env pour avoir chaque news "
                  "traduite et expliquée en détail.</i>")
    return f"{title}\n\n" + NEWS_SEPARATOR.join(cards) + footer


@restricted
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = await asyncio.to_thread(fetch_news, 12)
    top = relevant_news(items, None, min_score=5)[:6]
    if not top:
        await reply(update, "Aucune news importante trouvée dans les 12 dernières heures.")
        return
    await update.effective_chat.send_message("📰 Je lis et j'explique les news…")
    await reply(update, await _news_message("📰 <b>News importantes (12 h)</b>", top))


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


ASK_TICKER = ("🔎 Envoie-moi le symbole de l'action (ex. <code>AAPL</code>, <code>NVDA</code>, "
              "<code>RY.TO</code> pour Toronto).")


async def _stock_detail(update: Update, ticker: str) -> None:
    ticker = ticker.upper()
    await update.effective_chat.send_message(f"⏳ J'analyse {ticker}…")
    rep = await asyncio.to_thread(analyze, ticker, settings.dip_threshold_pct,
                                  settings.earnings_lookahead_days)
    if not rep:
        await reply(update, f"Impossible d'analyser {fmt.e(ticker)} (symbole invalide ?).")
        return
    await reply(update, fmt.stock_detail(rep) + "\n\n" + fmt.DISCLAIMER)


@restricted
async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        context.user_data["await"] = "stock"
        await reply(update, ASK_TICKER)
        return
    await _stock_detail(update, context.args[0])


async def _show_watchlist(update: Update) -> None:
    tickers = state.stocks(settings.stocks)
    await reply(update, f"👀 <b>Watchlist ({len(tickers)} actions)</b>\n\n"
                + fmt.e(", ".join(tickers)), WATCHLIST_MENU)


def _add_tickers(tickers: list[str]) -> str:
    current = state.stocks(settings.stocks)
    added = [t.upper() for t in tickers if t.upper() not in current]
    state.set_stocks(current + added)
    return f"✅ Ajouté : {fmt.e(', '.join(added)) or 'rien (déjà dans la liste)'}"


def _remove_tickers(tickers: list[str]) -> str:
    remove = {t.upper() for t in tickers}
    state.set_stocks([t for t in state.stocks(settings.stocks) if t not in remove])
    return f"🗑️ Retiré : {fmt.e(', '.join(sorted(remove))) or 'rien'}"


@restricted
async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_watchlist(update)


@restricted
async def cmd_ajouter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, _add_tickers(context.args or []))


@restricted
async def cmd_retirer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, _remove_tickers(context.args or []))


# ------------------------------------------------------------------ boutons

@restricted
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    kind, _, value = (query.data or "").partition(":")
    if kind == "why":
        await _pourquoi(update, None if value == "ALL" else value)
    elif kind == "wl" and value == "add":
        context.user_data["await"] = "add"
        await reply(update, "➕ Envoie le ou les symboles à ajouter (ex. <code>AMD, RY.TO</code>).")
    elif kind == "wl" and value == "del":
        context.user_data["await"] = "del"
        await reply(update, "➖ Envoie le ou les symboles à retirer.")
    elif kind == "wl" and value == "scan":
        await cmd_stocks.__wrapped__(update, context)


@restricted
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    actions = {
        BTN_MARCHE: cmd_marche, BTN_SIGNAUX: cmd_signaux, BTN_NEWS: cmd_news,
        BTN_CALENDRIER: cmd_calendrier, BTN_BRIEF: cmd_brief, BTN_STOCKS: cmd_stocks,
        BTN_AIDE: cmd_help,
    }
    if text in actions:
        context.user_data.pop("await", None)
        await actions[text].__wrapped__(update, context)
        return
    if text == BTN_POURQUOI:
        await reply(update, "❓ <b>Quel marché veux-tu comprendre ?</b>", WHY_MENU)
        return
    if text == BTN_STOCK:
        context.user_data["await"] = "stock"
        await reply(update, ASK_TICKER)
        return
    if text == BTN_WATCHLIST:
        await _show_watchlist(update)
        return

    waiting = context.user_data.pop("await", None)
    tickers = [t for t in text.replace(",", " ").upper().split() if t]
    if waiting == "stock" and tickers:
        await _stock_detail(update, tickers[0])
    elif waiting == "add":
        await reply(update, _add_tickers(tickers))
    elif waiting == "del":
        await reply(update, _remove_tickers(tickers))
    else:
        await reply(update, "👇 Utilise les boutons du menu en bas.", MAIN_MENU)


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
    explanations = await explain_news(top)
    lines += [fmt.news_brief_line(n, explanations[n.id]) for n in top] or ["• Rien de majeur."]

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
        await broadcast(context, await _news_message("🚨 <b>News à fort impact</b>", to_send))


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


async def _post_init(app: Application) -> None:
    # Liste des commandes affichée dans le bouton « Menu » de Telegram
    await app.bot.set_my_commands(BOT_COMMANDS)


def build_app() -> Application:
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant (voir .env.example)")
    app = Application.builder().token(settings.telegram_token).post_init(_post_init).build()

    for name, fn in [("start", cmd_start), ("aide", cmd_help), ("help", cmd_help),
                     ("menu", cmd_menu),
                     ("marche", cmd_marche), ("signaux", cmd_signaux), ("pourquoi", cmd_pourquoi),
                     ("news", cmd_news), ("calendrier", cmd_calendrier), ("stocks", cmd_stocks),
                     ("stock", cmd_stock), ("watchlist", cmd_watchlist), ("ajouter", cmd_ajouter),
                     ("retirer", cmd_retirer), ("brief", cmd_brief)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

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
