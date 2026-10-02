"""Détection des gros mouvements de prix et explication de leur cause."""

from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import ai, data
from .config import CONTEXT_TICKERS, INSTRUMENTS, Instrument
from .positioning import positioning, summary_for_ai
from .news import EconEvent, NewsItem, events_between, fetch_calendar, fetch_news, relevant_news


@dataclass
class Move:
    instrument: Instrument
    price: float
    change_15m: float
    change_60m: float
    window: str  # "15 min" ou "60 min"

    @property
    def change(self) -> float:
        return self.change_15m if self.window == "15 min" else self.change_60m

    @property
    def direction(self) -> str:
        return "hausse" if self.change > 0 else "baisse"


def detect_move(inst: Instrument) -> Move | None:
    if not data.is_fresh(inst.ticker):  # marché fermé : pas d'alerte sur des données figées
        return None
    c15 = data.last_change(inst.ticker, 15)
    c60 = data.last_change(inst.ticker, 60)
    if not c15 or not c60:
        return None
    price, ch15 = c15
    _, ch60 = c60
    if abs(ch15) >= inst.move_15m_pct:
        return Move(inst, price, ch15, ch60, "15 min")
    if abs(ch60) >= inst.move_60m_pct:
        return Move(inst, price, ch15, ch60, "60 min")
    return None


def current_move(inst: Instrument) -> Move | None:
    """Mouvement actuel, même s'il ne dépasse pas les seuils (pour /pourquoi)."""
    c15 = data.last_change(inst.ticker, 15)
    c60 = data.last_change(inst.ticker, 60)
    if not c15 or not c60:
        return None
    window = "15 min" if abs(c15[1]) / inst.move_15m_pct >= abs(c60[1]) / inst.move_60m_pct else "60 min"
    return Move(inst, c15[0], c15[1], c60[1], window)


def cross_market_context() -> list[tuple[str, float, float]]:
    """(nom, prix, variation 60 min en %) pour le dollar, les taux, le VIX, etc."""
    out = []
    for name, ticker in CONTEXT_TICKERS.items():
        ch = data.last_change(ticker, 60)
        if ch:
            out.append((name, ch[0], ch[1]))
    for inst in INSTRUMENTS.values():
        ch = data.last_change(inst.ticker, 60)
        if ch:
            out.append((inst.name, ch[0], ch[1]))
    return out


def _rule_based_reasons(move: Move, context: list[tuple[str, float, float]],
                        events: list[EconEvent], headlines: list[NewsItem]) -> list[str]:
    reasons = []
    for ev in events:
        reasons.append(f"Annonce économique « {ev.title} » vient de sortir "
                       f"(prévision {ev.forecast}, précédent {ev.previous}).")
    ctx = {name: ch for name, _, ch in context}
    dxy, yields, vix = ctx.get("DXY (dollar US)"), ctx.get("Taux US 10 ans"), ctx.get("VIX (peur)")
    key = move.instrument.key
    if key == "XAU":
        if dxy is not None and abs(dxy) >= 0.2 and (dxy > 0) != (move.change > 0):
            reasons.append(f"Le dollar bouge fort en sens inverse ({dxy:+.2f}%) : un dollar plus "
                           "fort rend l'or plus cher pour le reste du monde → pression sur l'or.")
        if yields is not None and abs(yields) >= 1.0 and (yields > 0) != (move.change > 0):
            reasons.append(f"Les taux 10 ans bougent ({yields:+.2f}%) : des taux plus hauts rendent "
                           "l'or (qui ne rapporte pas d'intérêt) moins attrayant.")
        if vix is not None and vix >= 5 and move.change > 0:
            reasons.append(f"La peur monte (VIX {vix:+.1f}%) : fuite vers la valeur refuge.")
    if key == "NQ":
        if yields is not None and abs(yields) >= 1.0 and (yields > 0) != (move.change > 0):
            reasons.append(f"Les taux 10 ans bougent ({yields:+.2f}%) : les actions tech sont très "
                           "sensibles aux taux (valorisations basées sur les profits futurs).")
        if vix is not None and abs(vix) >= 5 and (vix > 0) != (move.change > 0):
            reasons.append(f"Le VIX (indice de peur) {'grimpe' if vix > 0 else 'chute'} "
                           f"({vix:+.1f}%) : mouvement de sentiment général.")
    if headlines:
        reasons.append("News récentes qui pourraient expliquer le mouvement (voir plus bas).")
    if not reasons:
        reasons.append("Aucune news ni annonce claire trouvée : probablement du flux d'ordres, "
                       "des prises de profits ou une cassure technique (stops déclenchés).")
    return reasons


async def explain(move: Move, alert: bool = True) -> str:
    """Construit le message Telegram (HTML) expliquant le mouvement."""
    inst = move.instrument
    now = datetime.now(timezone.utc)
    news_items, events, context, pos = await asyncio.gather(
        asyncio.to_thread(fetch_news, 6),
        asyncio.to_thread(fetch_calendar),
        asyncio.to_thread(cross_market_context),
        asyncio.to_thread(positioning, inst.key),
    )
    headlines = relevant_news(news_items, inst.key, since=now - timedelta(hours=3))[:6]
    recent_events = events_between(events, now - timedelta(minutes=90), now + timedelta(minutes=5))

    arrow = "🚀" if move.change > 0 else "🩸"
    title = "ALERTE GROS MOUVEMENT" if alert else "Pourquoi ça bouge ?"
    lines = [
        f"{arrow} <b>{title} — {html.escape(inst.name)}</b>",
        f"Prix : <b>{move.price:,.2f}</b> | 15 min : <b>{move.change_15m:+.2f}%</b> | "
        f"60 min : <b>{move.change_60m:+.2f}%</b>",
        "",
    ]

    ctx_line = " | ".join(f"{n} {ch:+.2f}%" for n, _, ch in context
                          if n != inst.name)
    headline_txt = "\n".join(f"- [{n.source}, {n.published:%H:%M} UTC] {n.title}"
                             for n in headlines) or "(aucune)"
    events_txt = "\n".join(f"- {e.title} à {e.time:%H:%M %Z} (prévu {e.forecast}, précédent "
                           f"{e.previous})" for e in recent_events) or "(aucune)"

    prompt = (
        f"Il est {now:%Y-%m-%d %H:%M} UTC. Le {inst.name} ({inst.ticker}) vient de faire une "
        f"{move.direction} de {move.change:+.2f}% en {move.window} (15 min : "
        f"{move.change_15m:+.2f}%, 60 min : {move.change_60m:+.2f}%). Prix actuel : "
        f"{move.price:,.2f}.\n\n"
        f"Contexte inter-marchés (variation 60 min) : {ctx_line or 'indisponible'}\n\n"
        f"Annonces économiques US à fort impact dans la dernière heure et demie :\n{events_txt}\n\n"
        f"Titres de news récents (peuvent être incomplets) :\n{headline_txt}\n\n"
        f"Niveaux clés et positionnement :\n{summary_for_ai([pos])}\n\n"
        "Explique-moi pourquoi le marché bouge comme ça en ce moment, ce que ça veut dire, "
        "et à quoi faire attention (niveaux gamma où le prix pourrait rebondir ou accélérer). "
        "Si la recherche web est disponible, vérifie les toutes "
        "dernières nouvelles pour trouver la vraie cause."
    )
    explanation = await ai.ask(prompt)
    if explanation:
        lines.append("🧠 <b>Explication</b>")
        lines.append(html.escape(explanation, quote=False))
    else:
        lines.append("🧠 <b>Causes probables</b>")
        lines.extend(f"• {html.escape(r, quote=False)}" for r in
                     _rule_based_reasons(move, context, recent_events, headlines))

    if headlines:
        lines += ["", "📰 <b>News récentes</b>"]
        lines.extend(f"• <a href=\"{html.escape(n.link)}\">{html.escape(n.title, quote=False)}</a> "
                     f"<i>({html.escape(n.source)})</i>" for n in headlines[:4])
    if context:
        lines += ["", "🌍 <b>Autres marchés (60 min)</b>", html.escape(ctx_line, quote=False)]
    return "\n".join(lines)
