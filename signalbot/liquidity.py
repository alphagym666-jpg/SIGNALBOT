"""Zones de liquidité et sweeps.

Liquidité = endroits où beaucoup de stops attendent : sommets égaux / creux égaux, haut / bas des
séances d'Asie et de Londres. Les gros joueurs vont souvent « chercher » ces stops avant de repartir.

Sweep (chasse aux stops) = le prix dépasse un niveau de liquidité puis CLÔTURE de retour de l'autre
côté : les stops ont été pris et le mouvement repart souvent dans l'autre sens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime

import pandas as pd

from .levels import NY, Level, session_dates


@dataclass
class Sweep:
    level: Level
    side: str  # "haut" (stops des vendeurs pris au-dessus → baissier) ou "bas" (→ haussier)
    extreme: float  # plus haut / plus bas atteint pendant le sweep
    close: float
    when: pd.Timestamp

    @property
    def bullish(self) -> bool:
        return self.side == "bas"


def swing_points(df: pd.DataFrame, length: int = 3) -> tuple[list[float], list[float]]:
    """Sommets / creux (un plus haut entouré de `length` bougies plus basses de chaque côté)."""
    highs, lows = df["High"].to_numpy(), df["Low"].to_numpy()
    sh, sl = [], []
    for i in range(length, len(df) - length):
        win_h = highs[i - length:i + length + 1]
        win_l = lows[i - length:i + length + 1]
        if highs[i] == win_h.max():
            sh.append(float(highs[i]))
        if lows[i] == win_l.min():
            sl.append(float(lows[i]))
    return sh, sl


def equal_levels(points: list[float], tol: float, min_touches: int = 2) -> list[float]:
    """Regroupe les sommets (ou creux) presque au même prix : là où les stops s'empilent."""
    out, group = [], []
    for p in sorted(points):
        if group and p - group[-1] > tol:
            if len(group) >= min_touches:
                out.append(sum(group) / len(group))
            group = []
        group.append(p)
    if len(group) >= min_touches:
        out.append(sum(group) / len(group))
    return out


def liquidity_levels(df5: pd.DataFrame, tol_pct: float = 0.0006) -> list[Level]:
    """Niveaux de liquidité à partir des bougies 5 min des derniers jours."""
    if df5 is None or df5.empty or len(df5) < 30:
        return []
    price = float(df5["Close"].iloc[-1])
    tol = price * tol_pct
    df15 = df5.resample("15min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    recent = df15.tail(4 * 24 * 2)  # deux jours
    sh, sl = swing_points(recent)
    out = [Level("Liquidité : sommets égaux", round(p, 2), "stops au-dessus : le prix va souvent les chercher")
           for p in equal_levels(sh, tol) if p > price]
    out += [Level("Liquidité : creux égaux", round(p, 2), "stops en dessous : le prix va souvent les chercher")
            for p in equal_levels(sl, tol) if p < price]

    days = session_dates(df5.index)
    today = df5[days == days[-1]]
    ny_t = pd.Index(today.index.tz_convert(NY).time)
    for name, start, end in (("Asie", dtime(18, 0), dtime(2, 0)), ("Londres", dtime(2, 0), dtime(8, 0))):
        mask = [(t >= start or t < end) if start > end else (start <= t < end) for t in ny_t]
        part = today[mask]
        if len(part) >= 6:
            out.append(Level(f"Haut {name}", round(float(part["High"].max()), 2), "liquidité de séance"))
            out.append(Level(f"Bas {name}", round(float(part["Low"].min()), 2), "liquidité de séance"))
    return out


SWEEPABLE = ("Liquidité", "Haut ", "Bas ", "Sommet", "Creux")


def find_sweeps(df5: pd.DataFrame, levels: list[Level], bars: int = 4) -> list[Sweep]:
    """Sweeps des dernières bougies 5 min clôturées : mèche au-delà du niveau, clôture de retour."""
    if df5 is None or len(df5) < bars + 1:
        return []
    closed = df5.iloc[:-1].tail(bars)  # la dernière bougie est souvent encore en cours
    out = []
    for lv in levels:
        if not lv.label.startswith(SWEEPABLE) and "d'hier" not in lv.label and "semaine" not in lv.label:
            continue
        for ts, row in closed.iterrows():
            if row["High"] > lv.price and row["Close"] < lv.price and row["Open"] < lv.price * 1.0005:
                out.append(Sweep(lv, "haut", float(row["High"]), float(row["Close"]), ts))
                break
            if row["Low"] < lv.price and row["Close"] > lv.price and row["Open"] > lv.price * 0.9995:
                out.append(Sweep(lv, "bas", float(row["Low"]), float(row["Close"]), ts))
                break
    return out
