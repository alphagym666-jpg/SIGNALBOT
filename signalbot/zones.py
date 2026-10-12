"""Zones importantes : on regroupe tous les niveaux proches en zones ACHAT / VENTE avec une « force ».

Force = nombre de raisons qui tombent au même endroit (gamma, haut d'hier, POC, zone IA…).
Plus il y a de raisons au même prix, plus la zone a de chances de faire réagir le marché.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .levels import Level

# Poids : les niveaux d'options et ceux de l'IA comptent double
HEAVY = ("Call wall", "Put wall", "Bascule gamma", "SpotGamma", "IA", "Liquidité")
# Écart max entre deux niveaux pour les mettre dans la même zone (en % du prix)
MERGE_PCT = {"NQ": 0.0018, "XAU": 0.0025}


@dataclass
class Zone:
    low: float
    high: float
    side: str  # "ACHAT" (sous le prix) ou "VENTE" (au-dessus)
    reasons: list[str] = field(default_factory=list)
    force: int = 0

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


def short_label(label: str) -> str:
    return label.replace(" (options)", "").replace(" (SpotGamma)", " SG")


def _weight(label: str) -> int:
    return 2 if any(h in label for h in HEAVY) else 1


def last_week_levels(daily: pd.DataFrame) -> list[Level]:
    """Haut / bas de la semaine passée (bougies quotidiennes)."""
    if daily is None or daily.empty:
        return []
    index = daily.index.tz_localize(None) if daily.index.tz is not None else daily.index
    weeks = daily.groupby(index.to_period("W"))
    if len(weeks) < 2:
        return []
    prev = list(weeks)[-2][1]
    return [Level("Haut semaine passée", round(float(prev["High"].max()), 2), "résistance de la semaine"),
            Level("Bas semaine passée", round(float(prev["Low"].min()), 2), "support de la semaine")]


def plan_to_levels(plan_levels: list[dict]) -> list[Level]:
    names = {"achat": "Zone achat IA", "vente": "Zone vente IA", "objectif": "Objectif IA",
             "invalidation": "Invalidation IA"}
    out = []
    for p in plan_levels or []:
        try:
            out.append(Level(names.get(p.get("kind", ""), "IA"), round(float(p["price"]), 2),
                             p.get("note", "")))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def build(key: str, price: float, levels: list[Level], per_side: int = 4) -> list[Zone]:
    """Fusionne les niveaux proches en zones et garde les plus proches de chaque côté du prix."""
    if not price or not levels:
        return []
    tol = price * MERGE_PCT.get(key, 0.002)
    pts = sorted((lv for lv in levels if lv.price and abs(lv.price - price) / price < 0.06),
                 key=lambda lv: lv.price)
    groups: list[list[Level]] = []
    for lv in pts:
        if groups and lv.price - groups[-1][-1].price <= tol:
            groups[-1].append(lv)
        else:
            groups.append([lv])

    zones = []
    pad = tol * 0.25
    for g in groups:
        low, high = g[0].price - pad, g[-1].price + pad
        mid = (low + high) / 2
        reasons = list(dict.fromkeys(short_label(lv.label) for lv in g))
        zones.append(Zone(round(low, 2), round(high, 2), "ACHAT" if mid < price else "VENTE",
                          reasons, sum(_weight(lv.label) for lv in g)))

    buys = sorted((z for z in zones if z.side == "ACHAT"), key=lambda z: price - z.mid)[:per_side]
    sells = sorted((z for z in zones if z.side == "VENTE"), key=lambda z: z.mid - price)[:per_side]
    return sorted(buys + sells, key=lambda z: z.mid, reverse=True)


def shift(zones: list[Zone], offset: float) -> list[Zone]:
    """Décale les zones vers le prix du courtier (MetaTrader : NAS100, XAUUSD)."""
    return [Zone(round(z.low + offset, 2), round(z.high + offset, 2), z.side, z.reasons, z.force)
            for z in zones]
