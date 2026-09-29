"""Mise en forme des messages Telegram (HTML)."""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Instrument
from .news import EconEvent, NewsItem
from .regime import Regime, market_view
from .signals import Signal
from .stocks import StockReport

DISCLAIMER = "<i>⚠️ Outil d'aide à la décision, pas un conseil financier. Gère ton risque.</i>"

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]


def e(text: str) -> str:
    return html.escape(str(text), quote=False)


def attr(text: str) -> str:
    return html.escape(str(text), quote=True)


def _short_month(month: int) -> str:
    name = MOIS[month - 1]
    return name if len(name) <= 4 else name[:4] + "."


def fr_date(dt: datetime, pattern: str) -> str:
    """strftime avec les noms de jours/mois en français (%A, %a, %B, %b)."""
    pattern = (pattern.replace("%A", JOURS[dt.weekday()])
               .replace("%a", JOURS[dt.weekday()][:3] + ".")
               .replace("%B", MOIS[dt.month - 1])
               .replace("%b", _short_month(dt.month)))
    return dt.strftime(pattern)


def regime_block(inst: Instrument, regimes: dict[str, Regime]) -> str:
    h1 = regimes.get("H1") or regimes.get("H4") or regimes.get("D1")
    if not h1:
        return f"<b>{e(inst.name)}</b> : données indisponibles."
    lines = [f"📊 <b>{e(inst.name)}</b> — {h1.close:,.2f}"]
    for tf in ("D1", "H4", "H1"):
        r = regimes.get(tf)
        if r:
            lines.append(f"  {r.emoji} <b>{tf}</b> : {e(r.label)} ({e(r.strength)}) · "
                         f"ADX {r.adx:.0f} · RSI {r.rsi:.0f}")
    notes = {n for r in regimes.values() if r for n in r.notes}
    for n in sorted(notes):
        lines.append(f"  • {e(n)}")
    h4 = regimes.get("H4")
    if h4:
        lines.append(f"  Zone H4 récente : {h4.range_low:,.2f} – {h4.range_high:,.2f} "
                     f"(ATR H4 ≈ {h4.atr:,.2f})")
    lines.append(f"👉 {e(market_view(regimes))}")
    return "\n".join(lines)


def signal_message(sig: Signal, inst: Instrument) -> str:
    icon = "🟢" if sig.direction == "ACHAT" else "🔴"
    stars = "★" * max(1, round(sig.score / 20)) + "☆" * (5 - max(1, round(sig.score / 20)))
    lines = [
        f"{icon} <b>SIGNAL {sig.direction} — {e(inst.name)}</b>",
        f"Setup : <b>{e(sig.setup)}</b> · Qualité {sig.score}/100 {stars}",
        "",
        f"Entrée ≈ <b>{sig.entry:,.2f}</b>",
        f"Stop : {sig.stop:,.2f}  (risque {abs(sig.entry - sig.stop):,.2f})",
        f"TP1 : {sig.tp1:,.2f}  ·  TP2 : {sig.tp2:,.2f}  (R/R {sig.risk_reward:.1f})",
        "",
        "<b>Pourquoi :</b>",
        *[f"• {e(r)}" for r in sig.reasons],
    ]
    if sig.warnings:
        lines += ["", "<b>Attention :</b>", *[f"⚠️ {e(w)}" for w in sig.warnings]]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def news_line(n: NewsItem, tz: ZoneInfo) -> str:
    tags = ", ".join(dict.fromkeys(n.tags))
    markets = "/".join(sorted(n.markets)) or "-"
    return (f"• <a href=\"{attr(n.link)}\">{e(n.title)}</a>\n"
            f"   <i>{e(n.source)} · {n.published.astimezone(tz):%H:%M} · {e(markets)} · "
            f"{e(tags)}</i>")


def event_line(ev: EconEvent, tz: ZoneInfo) -> str:
    return (f"• <b>{fr_date(ev.time.astimezone(tz), '%a %d %H:%M')}</b> — {e(ev.title)} "
            f"<i>(prévu {e(ev.forecast)}, préc. {e(ev.previous)})</i>")


def _fmt_pct(v: float | None, scale: float = 1) -> str:
    return f"{v * scale:+.0f}%" if v is not None else "n/d"


def stock_card(r: StockReport, kind: str) -> str:
    if kind == "earnings":
        head = f"📅 <b>{e(r.ticker)}</b> — {e(r.name)} · score {r.earnings_score}/100"
        reasons = r.earnings_reasons
    else:
        head = f"📉 <b>{e(r.ticker)}</b> — {e(r.name)} · score {r.dip_score}/100"
        reasons = r.dip_reasons
    lines = [head, f"   Prix {r.price:,.2f} {e(r.currency)} · 1 mois {r.change_1m_pct:+.1f}% · "
                   f"vs sommet 52s {r.drawdown_pct:.0f}%"]
    lines += [f"   ✅ {e(x)}" for x in reasons]
    lines += [f"   ⚠️ {e(x)}" for x in r.risks[:2]]
    return "\n".join(lines)


def stock_detail(r: StockReport) -> str:
    lines = [
        f"🔎 <b>{e(r.ticker)} — {e(r.name)}</b>",
        f"Prix : {r.price:,.2f} {e(r.currency)}",
        f"Vs sommet 52 semaines : {r.drawdown_pct:.1f}% · 1 mois : {r.change_1m_pct:+.1f}%",
        f"Au-dessus de la MM200 : {'oui' if r.above_200d else 'non' if r.above_200d is not None else 'n/d'}",
        f"RSI hebdo : {r.rsi_weekly:.0f}" if r.rsi_weekly is not None else "RSI hebdo : n/d",
        f"Croissance revenus : {_fmt_pct(r.revenue_growth, 100)} · Marge nette : "
        f"{_fmt_pct(r.profit_margin, 100)}",
        f"P/E prévisionnel : {r.forward_pe:.1f}" if r.forward_pe else "P/E prévisionnel : n/d",
        f"Note analystes : {r.analyst_rating:.1f}/5 (1 = achat fort)" if r.analyst_rating
        else "Note analystes : n/d",
        f"Cible analystes : {_fmt_pct(r.target_upside_pct)}",
        f"Prochains résultats : {fr_date(r.next_earnings, '%d %b %Y')}" if r.next_earnings
        else "Prochains résultats : n/d",
        f"Historique : bat les attentes {r.beat_rate:.0f}% du temps (surprise moy. "
        f"{r.avg_surprise:+.1f}%)" if r.beat_rate is not None else "Historique résultats : n/d",
    ]
    if r.earnings_reasons:
        lines += ["", f"<b>Setup earnings</b> ({r.earnings_score}/100)",
                  *[f"✅ {e(x)}" for x in r.earnings_reasons]]
    if r.dip_reasons:
        lines += ["", f"<b>Setup grosse baisse</b> ({r.dip_score}/100)",
                  *[f"✅ {e(x)}" for x in r.dip_reasons]]
    if r.risks:
        lines += ["", "<b>Risques</b>", *[f"⚠️ {e(x)}" for x in r.risks]]
    if r.ticker.endswith(".TO"):
        lines += ["", "🍁 Titre canadien : aucune retenue d'impôt étrangère sur les dividendes "
                      "dans un CELI."]
    elif r.currency == "USD":
        lines += ["", "ℹ️ Titre US dans un CELI : 15% de retenue sur les dividendes, non "
                      "récupérable (peu important pour un titre de croissance)."]
    return "\n".join(lines)


def now_str(tz: ZoneInfo) -> str:
    return fr_date(datetime.now(tz), "%a %d %b %H:%M")
