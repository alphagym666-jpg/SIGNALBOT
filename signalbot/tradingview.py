"""Ligne de niveaux pour l'indicateur TradingView « SignalBot Niveaux » (tradingview/).

TradingView ne permet pas à un indicateur d'aller chercher des données sur Internet : le bot
fabrique donc une ligne de texte que l'on colle dans les paramètres de l'indicateur.

Format (une section par marché, séparées par « || ») :
    NQ|DATE=2026-10-02;BIAS=haussier;GAMMA=positif;25000:Call wall;24000:Put wall||XAU|...
Les prix sont ceux des contrats à terme (NQ1!, GC1!) : l'indicateur les convertit au prix du
graphique.
"""

from __future__ import annotations

import re
from pathlib import Path

from .levels import Level

PINE_FILE = Path(__file__).resolve().parent.parent / "tradingview" / "SignalBot_Niveaux.pine"
PINE_INPUT = 'levelsTxt = input.text_area("", '

# niveaux que l'indicateur calcule déjà lui-même en direct : inutile de les envoyer
LIVE_IN_PINE = ("VWAP", "Haut d'hier", "Bas d'hier", "Clôture d'hier", "Haut de la nuit",
                "Bas de la nuit")
PLAN_LABELS = {"achat": "Achat IA", "vente": "Vente IA", "invalidation": "Invalidation IA",
               "objectif": "Objectif IA"}


def clean(text: str) -> str:
    """Enlève les caractères qui servent de séparateurs dans la ligne."""
    return re.sub(r"\s+", " ", re.sub(r"[;:|=\n\r]", " ", text)).strip()[:40]


def section(key: str, levels: list[Level], plan_levels: list[dict], bias: str | None,
            gamma_positive: bool | None, date: str) -> str:
    items = [f"DATE={date}"]
    if bias:
        items.append(f"BIAS={clean(bias)}")
    if gamma_positive is not None:
        items.append("GAMMA=" + ("positif" if gamma_positive else "négatif"))
    seen: set[tuple[float, str]] = set()
    for lv in levels:
        if lv.label.startswith(LIVE_IN_PINE):
            continue
        item = (round(lv.price, 2), clean(lv.label))
        if item not in seen:
            seen.add(item)
    for p in plan_levels:
        try:
            price = round(float(p["price"]), 2)
        except (KeyError, TypeError, ValueError):
            continue
        label = PLAN_LABELS.get(p.get("kind", ""), "IA")
        note = clean(p.get("note", ""))
        seen.add((price, f"{label} {note}".strip()[:40]))
    items += [f"{price:g}:{label}" for price, label in sorted(seen, reverse=True)]
    return f"{key}|" + ";".join(items)


def build_line(sections: list[str]) -> str:
    return "||".join(sections)


def pine_with_levels(line: str, source: str | None = None) -> str:
    """Code complet de l'indicateur, avec la ligne du jour déjà mise comme valeur par défaut."""
    src = source if source is not None else PINE_FILE.read_text(encoding="utf-8")
    if PINE_INPUT not in src:
        raise ValueError("Indicateur TradingView introuvable ou modifié")
    literal = line.replace("\\", "/").replace('"', "'")
    return src.replace(PINE_INPUT, f'levelsTxt = input.text_area("{literal}", ', 1)
