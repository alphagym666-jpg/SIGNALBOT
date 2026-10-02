"""Niveaux « pros » calculés à partir des bougies 5 min des contrats à terme (NQ, or).

- VWAP de la séance : prix moyen pondéré par le volume (les institutions s'en servent comme
  référence : au-dessus = acheteurs en contrôle, en dessous = vendeurs).
- Plus haut / plus bas / clôture de la veille (PDH / PDL / PDC).
- Plus haut / plus bas de la nuit (avant l'ouverture de New York à 9 h 30).
- Profil de volume de la veille : POC (prix le plus échangé, aimant) et zone de valeur
  (70 % du volume : VAH en haut, VAL en bas).

Une séance de contrats à terme commence à 18 h (heure de New York) la veille.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import data

NY = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)


@dataclass
class Level:
    label: str
    price: float
    role: str  # explication courte du rôle du niveau


@dataclass
class ProLevels:
    instrument: str
    price: float
    levels: list[Level] = field(default_factory=list)

    def get(self, label: str) -> float | None:
        return next((lv.price for lv in self.levels if lv.label == label), None)


def session_dates(index: pd.DatetimeIndex) -> pd.Index:
    """Date de séance : une bougie de 18 h ou plus (New York) appartient à la séance du lendemain."""
    ny = index.tz_convert(NY)
    return pd.Index((ny + pd.Timedelta(hours=6)).date)


def volume_profile(df: pd.DataFrame, bins: int = 60, value_area: float = 0.7
                   ) -> tuple[float, float, float] | None:
    """(POC, VAH, VAL) d'un ensemble de bougies, volume réparti au prix typique."""
    if df.empty or df["Volume"].sum() <= 0:
        return None
    low, high = float(df["Low"].min()), float(df["High"].max())
    if high <= low:
        return None
    typical = ((df["High"] + df["Low"] + df["Close"]) / 3).to_numpy()
    hist, edges = np.histogram(typical, bins=bins, range=(low, high), weights=df["Volume"].to_numpy())
    centers = (edges[:-1] + edges[1:]) / 2
    poc_i = int(hist.argmax())
    lo_i = hi_i = poc_i
    target, total = hist.sum() * value_area, hist[poc_i]
    while total < target and (lo_i > 0 or hi_i < len(hist) - 1):
        down = hist[lo_i - 1] if lo_i > 0 else -1
        up = hist[hi_i + 1] if hi_i < len(hist) - 1 else -1
        if up >= down:
            hi_i += 1
            total += hist[hi_i]
        else:
            lo_i -= 1
            total += hist[lo_i]
    return float(centers[poc_i]), float(edges[hi_i + 1]), float(edges[lo_i])


def compute(df: pd.DataFrame, instrument: str) -> ProLevels | None:
    """Calcule les niveaux à partir de bougies 5 min (index avec fuseau horaire)."""
    if df is None or df.empty or len(df) < 20:
        return None
    df = df.copy()
    df["session"] = session_dates(df.index)
    sessions = sorted(df["session"].unique())
    today = df[df["session"] == sessions[-1]]
    out = ProLevels(instrument, float(df["Close"].iloc[-1]))
    add = out.levels.append

    has_volume = "Volume" in df.columns and today["Volume"].sum() > 0
    if has_volume:
        typical = (today["High"] + today["Low"] + today["Close"]) / 3
        vwap = float((typical * today["Volume"]).sum() / today["Volume"].sum())
        add(Level("VWAP", round(vwap, 2), "prix moyen de la séance : au-dessus = acheteurs en contrôle"))

    if len(sessions) >= 2:
        prev = df[df["session"] == sessions[-2]]
        add(Level("Haut d'hier", round(float(prev["High"].max()), 2), "résistance fréquente, cassure = force"))
        add(Level("Bas d'hier", round(float(prev["Low"].min()), 2), "support fréquent, cassure = faiblesse"))
        add(Level("Clôture d'hier", round(float(prev["Close"].iloc[-1]), 2), "le prix y revient souvent (gap)"))
        vp = volume_profile(prev) if "Volume" in prev.columns else None
        if vp:
            poc, vah, val = vp
            add(Level("POC d'hier", round(poc, 2), "prix le plus échangé hier : aimant"))
            add(Level("Haut zone de valeur", round(vah, 2), "au-dessus : le marché accepte des prix plus hauts"))
            add(Level("Bas zone de valeur", round(val, 2), "en dessous : le marché accepte des prix plus bas"))

    ny_time = today.index.tz_convert(NY).time
    overnight = today[[t < RTH_OPEN or t >= dtime(18, 0) for t in ny_time]]
    if not overnight.empty:
        done = len(overnight) < len(today)  # New York est ouvert : la nuit est terminée
        suffix = "" if done else " (en cours)"
        add(Level("Haut de la nuit" + suffix, round(float(overnight["High"].max()), 2),
                  "cassure = continuation probable"))
        add(Level("Bas de la nuit" + suffix, round(float(overnight["Low"].min()), 2),
                  "cassure = continuation probable"))
    return out


def pro_levels(key: str, ticker: str) -> ProLevels | None:
    return compute(data.intraday(ticker), key)


# ------------------------------------------------------------------ niveaux tapés à la main

_ALIASES = {
    "call": "Call wall", "callwall": "Call wall", "cw": "Call wall", "resistance": "Call wall",
    "put": "Put wall", "putwall": "Put wall", "pw": "Put wall", "support": "Put wall",
    "flip": "Bascule gamma", "zero": "Bascule gamma", "zg": "Bascule gamma", "hvl": "Bascule gamma",
    "vt": "Volatility trigger", "trigger": "Volatility trigger", "voltrigger": "Volatility trigger",
    "abs": "Absolute gamma", "absolute": "Absolute gamma", "key": "Niveau clé", "cle": "Niveau clé",
}
_ROLES = {
    "Call wall": "plus grosse résistance des options",
    "Put wall": "plus gros support des options",
    "Bascule gamma": "au-dessus = marché calme, en dessous = marché nerveux",
    "Volatility trigger": "sous ce niveau, la volatilité risque d'augmenter",
    "Absolute gamma": "le plus gros niveau d'options : aimant",
}


def parse_manual(tokens: list[str]) -> dict[str, float]:
    """« call 25000 put 24000 flip 24500 » ou « call=25000 » -> {libellé: prix}."""
    out: dict[str, float] = {}
    words: list[str] = []
    for tok in " ".join(tokens).replace("=", " ").replace(",", " ").split():
        try:
            value = float(tok.replace(" ", ""))
        except ValueError:
            words.append(tok)
            continue
        raw = "".join(words).lower() or "niveau"
        label = _ALIASES.get(raw, " ".join(words) or "Niveau")
        n, base = 2, label
        while label in out:  # deux niveaux du même nom : on numérote
            label, n = f"{base} {n}", n + 1
        out[label] = value
        words = []
    return out


def manual_levels(entries: dict[str, float], price: float, etf_ratio: float | None) -> list[Level]:
    """Convertit les niveaux tapés (en prix NQ/XAU, ou QQQ/GLD s'ils sont beaucoup plus petits)."""
    levels = []
    for label, value in entries.items():
        if etf_ratio and value < price / 3:  # niveau donné en prix de l'ETF (QQQ / GLD)
            value = value * etf_ratio
        levels.append(Level(f"{label} (SpotGamma)", round(value, 2), _ROLES.get(label, "niveau SpotGamma")))
    return levels
