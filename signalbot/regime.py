"""Détection du type de marché : tendance, range, indécis, volatil."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .indicators import pct_rank_last

TREND_UP = "TREND_UP"
TREND_DOWN = "TREND_DOWN"
WEAK_UP = "WEAK_UP"
WEAK_DOWN = "WEAK_DOWN"
RANGE = "RANGE"
VOLATILE = "VOLATILE"
INDECISIVE = "INDECISIVE"

LABELS = {
    TREND_UP: ("🟢", "Tendance haussière"),
    TREND_DOWN: ("🔴", "Tendance baissière"),
    WEAK_UP: ("🟩", "Légèrement haussier (tendance faible)"),
    WEAK_DOWN: ("🟥", "Légèrement baissier (tendance faible)"),
    RANGE: ("↔️", "Range (latéral)"),
    VOLATILE: ("⚡", "Volatil / chaotique"),
    INDECISIVE: ("🤷", "Indécis / transition"),
}

BULLISH = {TREND_UP, WEAK_UP}
BEARISH = {TREND_DOWN, WEAK_DOWN}

RANGE_LOOKBACK = 30


@dataclass
class Regime:
    timeframe: str
    code: str
    strength: str  # "forte", "modérée", "faible"
    close: float
    adx: float
    rsi: float
    atr: float
    range_high: float
    range_low: float
    notes: list[str] = field(default_factory=list)

    @property
    def emoji(self) -> str:
        return LABELS[self.code][0]

    @property
    def label(self) -> str:
        return LABELS[self.code][1]

    @property
    def bullish(self) -> bool:
        return self.code in BULLISH

    @property
    def bearish(self) -> bool:
        return self.code in BEARISH


def _ema_crosses(df: pd.DataFrame, lookback: int) -> int:
    above = (df["Close"] > df["ema20"]).tail(lookback).astype(int)
    return int(above.diff().abs().sum())


def classify(df: pd.DataFrame, timeframe: str) -> Regime | None:
    """Classe le régime de marché de la dernière bougie d'un DataFrame avec indicateurs."""
    df = df.dropna(subset=["ema50", "adx", "atr", "rsi"])
    if len(df) < 60:
        return None

    last = df.iloc[-1]
    close, atr_v, adx_v = float(last["Close"]), float(last["atr"]), float(last["adx"])
    ema20, ema50 = float(last["ema20"]), float(last["ema50"])
    slope = (ema50 - float(df["ema50"].iloc[-11])) / atr_v if atr_v else 0.0
    bbw_rank = pct_rank_last(df["bbw"], 120)
    vol_rank = pct_rank_last(df["atr"] / df["Close"], 120)
    crosses = _ema_crosses(df, RANGE_LOOKBACK)
    window = df.tail(RANGE_LOOKBACK)
    range_high, range_low = float(window["High"].max()), float(window["Low"].min())

    # Pente de l'EMA50 sur 10 bougies, en multiples d'ATR
    bull = close > ema50 and ema20 > ema50 and slope > 0.2
    bear = close < ema50 and ema20 < ema50 and slope < -0.2
    notes: list[str] = []

    if adx_v >= 23 and bull and slope > 0.5:
        code = TREND_UP
    elif adx_v >= 23 and bear and slope < -0.5:
        code = TREND_DOWN
    elif vol_rank >= 0.85 and adx_v < 23:
        code = VOLATILE
        notes.append("volatilité très élevée sans direction claire")
    elif adx_v < 20 and (crosses >= 4 or bbw_rank < 0.35):
        code = RANGE
    elif bull:
        code = WEAK_UP
    elif bear:
        code = WEAK_DOWN
    else:
        code = INDECISIVE

    if code in (TREND_UP, TREND_DOWN):
        strength = "forte" if adx_v >= 30 else "modérée"
    elif code == RANGE:
        strength = "serré" if bbw_rank < 0.2 else "normal"
    else:
        strength = "faible"

    if bbw_rank < 0.15:
        notes.append("compression de volatilité → une cassure se prépare souvent")
    if float(last["rsi"]) >= 70:
        notes.append(f"RSI en surachat ({last['rsi']:.0f})")
    elif float(last["rsi"]) <= 30:
        notes.append(f"RSI en survente ({last['rsi']:.0f})")
    if code == RANGE and crosses >= 6:
        notes.append("le prix zigzague autour de l'EMA20 (aucun camp ne gagne)")

    return Regime(
        timeframe=timeframe,
        code=code,
        strength=strength,
        close=close,
        adx=adx_v,
        rsi=float(last["rsi"]),
        atr=atr_v,
        range_high=range_high,
        range_low=range_low,
        notes=notes,
    )


def market_view(regimes: dict[str, Regime]) -> str:
    """Résumé en langage simple de ce qu'il faut faire selon les régimes D1/H4/H1."""
    d1, h4, h1 = regimes.get("D1"), regimes.get("H4"), regimes.get("H1")
    if not h4:
        return "Pas assez de données pour conclure."

    if h4.code == VOLATILE or (h1 and h1.code == VOLATILE):
        return ("Marché nerveux et désordonné. Réduis la taille de position, élargis les stops "
                "ou reste à l'écart jusqu'à ce que ça se calme.")

    if h4.code == RANGE:
        return (f"Marché en range sur H4 entre {h4.range_low:,.2f} et {h4.range_high:,.2f}. "
                "Idée : acheter près du bas, vendre près du haut, éviter le milieu. "
                "Une clôture nette hors du range = changement de régime.")

    if h4.bullish:
        if d1 and d1.bearish:
            return ("Rebond haussier à court terme dans une tendance de fond baissière (D1). "
                    "Prudence sur les achats : ça peut être un simple rebond technique.")
        if h1 and h1.bullish:
            return ("Marché directionnel haussier. Privilégie les ACHATS sur repli "
                    "(vers l'EMA20/EMA50 H1). Évite de shorter contre la tendance.")
        return ("Tendance de fond haussière mais pause/consolidation à court terme. "
                "Attends soit un repli sur support, soit la reprise du mouvement pour acheter.")

    if h4.bearish:
        if d1 and d1.bullish:
            return ("Correction baissière dans une tendance de fond haussière (D1). "
                    "Surveille la fin de la correction : souvent une opportunité d'achat plus tard.")
        if h1 and h1.bearish:
            return ("Marché directionnel baissier. Privilégie les VENTES sur rebond "
                    "(vers l'EMA20/EMA50 H1). Évite d'acheter le couteau qui tombe.")
        return ("Tendance de fond baissière mais le court terme hésite. "
                "Attends un rebond vers une résistance pour vendre, ou la reprise de la baisse.")

    return ("Marché indécis : pas de tendance claire ni de range propre. "
            "C'est souvent le pire moment pour trader — patience, attends un signal clair.")
