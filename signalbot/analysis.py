"""Analyse IA complète : Claude reçoit TOUT (type de marché, niveaux, positionnement, news,
calendrier, autres marchés) et dit clairement ce qui se trame sur le Nasdaq et l'or, avec un
scénario principal, un scénario alternatif, les zones intéressantes et ce qui invaliderait l'idée.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import ai
from .config import INSTRUMENTS
from .levels import Level
from .news import EconEvent, NewsItem
from .positioning import Positioning, summary_for_ai
from .regime import Regime, market_view

log = logging.getLogger(__name__)

BIAS = ("haussier", "baissier", "neutre")
CONFIDENCE = ("faible", "moyenne", "forte")
PLAN_KINDS = ("achat", "vente", "invalidation", "objectif")

SYSTEM = """Tu es le stratège de marché personnel d'un trader francophone (Québec) qui trade \
UNIQUEMENT le Nasdaq 100 (NQ) et l'or (XAUUSD). Ton travail : filtrer toute l'information reçue \
et lui dire clairement ce qui se trame, pour qu'il puisse décider ses trades facilement.

Règles :
- Base-toi UNIQUEMENT sur les données fournies (prix, niveaux, type de marché, positionnement, \
news, calendrier, autres marchés). N'invente jamais un niveau de prix : chaque prix que tu cites \
doit venir des données.
- Sois concret et direct, en français simple. Explique en une phrase un terme technique si tu \
l'utilises.
- Si les signaux se contredisent ou si l'information est faible, dis-le : biais « neutre » et \
confiance « faible » plutôt que de forcer une direction.
- Les zones d'achat / de vente sont des zones à SURVEILLER (avec la confirmation à attendre), pas \
des ordres. Mentionne les annonces économiques proches à éviter.
- Pas de taille de position ni de conseil financier personnalisé.

Champs :
- overview : 3-5 phrases sur ce qui se passe globalement (le fil conducteur des news, des taux, \
du dollar et du sentiment).
- risk_events : les annonces / événements à risque à venir avec l'heure (heure de l'Est), ou vide.
- markets : une entrée pour NQ et une pour XAU :
  bias, confidence, whats_happening (2-3 phrases), main_scenario, alt_scenario, buy_zones, \
sell_zones, invalidation (le niveau qui annule le scénario principal), avoid (ce qu'il vaut \
mieux ne pas faire aujourd'hui), plan_levels : 2 à 6 prix exacts tirés des données, à tracer \
sur le graphique (kind : « achat », « vente », « invalidation » ou « objectif », note : 2-4 mots)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "risk_events": {"type": "array", "items": {"type": "string"}},
        "markets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "enum": list(INSTRUMENTS)},
                    "bias": {"type": "string", "enum": list(BIAS)},
                    "confidence": {"type": "string", "enum": list(CONFIDENCE)},
                    "whats_happening": {"type": "string"},
                    "main_scenario": {"type": "string"},
                    "alt_scenario": {"type": "string"},
                    "buy_zones": {"type": "string"},
                    "sell_zones": {"type": "string"},
                    "invalidation": {"type": "string"},
                    "avoid": {"type": "string"},
                    "plan_levels": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "price": {"type": "number"},
                                "kind": {"type": "string", "enum": list(PLAN_KINDS)},
                                "note": {"type": "string"},
                            },
                            "required": ["price", "kind", "note"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["key", "bias", "confidence", "whats_happening", "main_scenario",
                             "alt_scenario", "buy_zones", "sell_zones", "invalidation", "avoid",
                             "plan_levels"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overview", "risk_events", "markets"],
    "additionalProperties": False,
}


@dataclass
class MarketInput:
    key: str
    price: float | None
    regimes: dict[str, Regime]
    levels: list[Level]
    positioning: Positioning | None


@dataclass
class MarketPlan:
    key: str
    bias: str
    confidence: str
    whats_happening: str
    main_scenario: str = ""
    alt_scenario: str = ""
    buy_zones: str = ""
    sell_zones: str = ""
    invalidation: str = ""
    avoid: str = ""
    plan_levels: list[dict] = field(default_factory=list)


@dataclass
class Analysis:
    overview: str
    risk_events: list[str]
    markets: list[MarketPlan] = field(default_factory=list)
    ai: bool = True


def build_prompt(inputs: list[MarketInput], news: list[NewsItem], events: list[EconEvent],
                 context: list[tuple[str, float, float]], tz) -> str:
    now = datetime.now(timezone.utc)
    parts = [f"Date et heure : {now.astimezone(tz):%Y-%m-%d %H:%M} (heure de l'Est)."]
    for m in inputs:
        name = INSTRUMENTS[m.key].name
        parts.append(f"\n=== {name} ({m.key}) — prix {m.price if m.price is not None else 'n/d'} ===")
        for tf in ("D1", "H4", "H1"):
            r = m.regimes.get(tf)
            if r:
                parts.append(f"{tf} : {r.label} ({r.strength}), ADX {r.adx:.0f}, RSI {r.rsi:.0f}, "
                             f"zone récente {r.range_low:.2f}-{r.range_high:.2f}, ATR {r.atr:.2f}")
        if m.regimes:
            parts.append(f"Lecture technique : {market_view(m.regimes)}")
        if m.levels:
            parts.append("Niveaux : " + " | ".join(f"{lv.label} {lv.price}" for lv in
                                                   sorted(m.levels, key=lambda x: x.price)))
        if m.positioning:
            parts.append("Positionnement : " + summary_for_ai([m.positioning]))
    if context:
        parts.append("\nAutres marchés (variation 60 min) : "
                     + " | ".join(f"{n} {p:.2f} ({c:+.2f} %)" for n, p, c in context))
    upcoming = [e for e in events if now - timedelta(hours=3) <= e.time <= now + timedelta(hours=30)]
    parts.append("\nAnnonces économiques US à fort impact (récentes et à venir) :")
    parts += [f"- {e.time.astimezone(tz):%a %H:%M} {e.title} (prévu {e.forecast}, précédent {e.previous})"
              for e in upcoming] or ["- aucune"]
    parts.append("\nNews récentes (titres, les plus importantes d'abord) :")
    parts += [f"- [{n.published.astimezone(tz):%H:%M}, {n.source}] {n.title}"
              + (f" — {n.summary[:200]}" if n.summary else "") for n in news[:25]] or ["- aucune"]
    parts.append("\nDis-moi ce qui se trame et comment l'aborder aujourd'hui.")
    return "\n".join(parts)


def fallback(inputs: list[MarketInput]) -> Analysis:
    plans = []
    for m in inputs:
        h4 = m.regimes.get("H4")
        bias = "haussier" if h4 and h4.bullish else "baissier" if h4 and h4.bearish else "neutre"
        plans.append(MarketPlan(m.key, bias, "faible",
                                market_view(m.regimes) if m.regimes else "Données indisponibles."))
    return Analysis("Analyse simplifiée (sans clé Claude) : seulement le type de marché. Ajoute "
                    "ANTHROPIC_API_KEY dans .env pour l'analyse complète des news, niveaux et "
                    "positionnement.", [], plans, ai=False)


async def analyze(inputs: list[MarketInput], news: list[NewsItem], events: list[EconEvent],
                  context: list[tuple[str, float, float]], tz) -> Analysis:
    if not ai.enabled():
        return fallback(inputs)
    data = await ai.ask_json(build_prompt(inputs, news, events, context, tz), SCHEMA,
                             system=SYSTEM, effort="high")
    if not data:
        return fallback(inputs)
    plans = {m["key"]: MarketPlan(**m) for m in data.get("markets", []) if m.get("key") in INSTRUMENTS}
    ordered = [plans[m.key] for m in inputs if m.key in plans]
    return Analysis(data["overview"], data.get("risk_events", []), ordered)
