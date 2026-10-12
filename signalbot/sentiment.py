"""Sentiment global : le marché est-il plutôt ACHETEUR ou VENDEUR ?

On additionne des indices simples, chacun avec un poids :
- tendance de fond (H4 et jour) ;
- prix au-dessus / en dessous du VWAP (qui contrôle la séance) ;
- prix au-dessus / en dessous de la bascule gamma ;
- Myfxbook (particuliers) À CONTRE-COURANT : quand la foule est très acheteuse, elle a souvent tort ;
- COT : les gros spéculateurs ajoutent-ils des achats ou des ventes cette semaine ;
- biais de l'analyse IA du jour.
Résultat : un score de -100 (très vendeur) à +100 (très acheteur) et les raisons.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .levels import Level
from .positioning import Positioning
from .regime import Regime


@dataclass
class Sentiment:
    score: int  # -100 .. +100
    reasons: list[str] = field(default_factory=list)

    @property
    def buyers_pct(self) -> int:
        return round(50 + self.score / 2)

    @property
    def label(self) -> str:
        if self.score >= 40:
            return "ACHETEUR"
        if self.score >= 15:
            return "Plutôt acheteur"
        if self.score <= -40:
            return "VENDEUR"
        if self.score <= -15:
            return "Plutôt vendeur"
        return "Partagé"

    @property
    def emoji(self) -> str:
        return "🟢" if self.score >= 15 else "🔴" if self.score <= -15 else "⚪"

    def short(self) -> str:
        return f"{self.emoji} {self.label} ({self.buyers_pct} % acheteur / {100 - self.buyers_pct} % vendeur)"


def compute(price: float | None, levels: list[Level], pos: Positioning | None,
            regimes: dict[str, Regime] | None = None, ai_bias: str | None = None) -> Sentiment:
    total, weights, reasons = 0.0, 0.0, []

    def add(value: float, weight: float, text: str) -> None:
        nonlocal total, weights
        total += value * weight
        weights += weight
        if value:
            reasons.append(("🟢 " if value > 0 else "🔴 ") + text)

    for tf, name in (("D1", "jour"), ("H4", "4 h")):
        r = (regimes or {}).get(tf)
        if r:
            v = 1 if r.bullish else -1 if r.bearish else 0
            add(v, 1.0, f"tendance {name} : {r.label.lower()}")

    vwap = next((lv.price for lv in levels if lv.label == "VWAP"), None)
    if price and vwap:
        add(1 if price > vwap else -1, 1.0,
            "prix au-dessus du VWAP (acheteurs en contrôle)" if price > vwap
            else "prix sous le VWAP (vendeurs en contrôle)")

    g = pos.gamma if pos else None
    if price and g and g.flip:
        add(1 if price > g.flip else -1, 1.0,
            "au-dessus de la bascule gamma (marché calme)" if price > g.flip
            else "sous la bascule gamma (marché nerveux)")

    r = pos.retail if pos else None
    if r:
        if r.long_pct >= 60:
            add(-1 if r.long_pct >= 70 else -0.5, 1.5,
                f"Myfxbook : {r.long_pct:.0f} % des particuliers achètent → la foule a souvent tort")
        elif r.long_pct <= 40:
            add(1 if r.long_pct <= 30 else 0.5, 1.5,
                f"Myfxbook : {r.short_pct:.0f} % des particuliers vendent → la foule a souvent tort")
        else:
            add(0, 1.5, "")

    c = pos.cot if pos else None
    if c and c.net_change:
        add(1 if c.net_change > 0 else -1, 0.5,
            f"COT : les gros spéculateurs {'ajoutent des achats' if c.net_change > 0 else 'ajoutent des ventes'} "
            f"({c.net_change:+,} contrats)")

    if ai_bias in ("haussier", "baissier"):
        add(1 if ai_bias == "haussier" else -1, 1.5, f"analyse IA du jour : {ai_bias}")

    score = round(100 * total / weights) if weights else 0  # tout à +1 = +100
    return Sentiment(max(-100, min(100, score)), reasons)
