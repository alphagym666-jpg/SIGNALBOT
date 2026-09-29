"""Signaux d'achat / vente basés sur la confluence de plusieurs éléments.

Trois setups sont évalués sur le H1, filtrés par le régime H4 et D1 :
- repli dans la tendance (le plus fiable),
- rejet d'une borne de range,
- cassure après compression.

Les shorts sont évalués en « inversant » le graphique (prix * -1, RSI -> 100-RSI) et en
réutilisant exactement la même logique que pour les longs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from .indicators import pct_rank_last
from .regime import (BEARISH, BULLISH, RANGE, TREND_DOWN, TREND_UP, VOLATILE, WEAK_DOWN,
                     WEAK_UP, Regime)

_MIRROR_CODE = {TREND_UP: TREND_DOWN, TREND_DOWN: TREND_UP, WEAK_UP: WEAK_DOWN, WEAK_DOWN: WEAK_UP}


@dataclass
class Signal:
    instrument: str
    direction: str  # "ACHAT" / "VENTE"
    setup: str
    score: int
    entry: float
    stop: float
    tp1: float
    tp2: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bar_time: datetime | None = None

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry - self.stop)
        return abs(self.tp2 - self.entry) / risk if risk else 0.0


def _mirror(df: pd.DataFrame) -> pd.DataFrame:
    m = df.copy()
    m["High"], m["Low"] = -df["Low"], -df["High"]
    for col in ("Open", "Close", "ema20", "ema50", "ema200"):
        m[col] = -df[col]
    m["rsi"] = 100 - df["rsi"]
    return m


def _mirror_code(code: str | None) -> str | None:
    return _MIRROR_CODE.get(code, code) if code else None


def _long_setups(df: pd.DataFrame, h1: str | None, h4: str | None, d1: str | None) -> list[dict]:
    """Évalue les setups ACHAT. Les prix peuvent être inversés (pour les ventes)."""
    last, prev = df.iloc[-1], df.iloc[-2]
    atr = float(last["atr"])
    close = float(last["Close"])
    bullish_candle = last["Close"] > last["Open"]
    vol_ok = False
    if "Volume" in df.columns and df["Volume"].tail(20).mean() > 0:
        vol_ok = last["Volume"] > 1.2 * df["Volume"].tail(21).iloc[:-1].mean()
    out: list[dict] = []

    # 1) Repli dans une tendance haussière
    htf_up = h4 in BULLISH or (d1 == TREND_UP and h4 not in BEARISH)
    touched = df["Low"].tail(3).min() <= last["ema20"] + 0.3 * atr
    rsi_reset = df["rsi"].tail(8).min()
    if (htf_up and touched and close > last["ema20"] and close > last["ema50"]
            and 50 <= last["rsi"] <= 68 and rsi_reset < 48 and bullish_candle):
        score, reasons = 55, ["Tendance H4 haussière", "Repli sur l'EMA20 H1 puis reprise",
                              f"RSI réinitialisé ({rsi_reset:.0f}) puis repart > 50"]
        if h4 == TREND_UP:
            score += 10
            reasons.append("Tendance H4 forte (ADX élevé)")
        if d1 in BULLISH:
            score += 10
            reasons.append("D1 aussi haussier (les 3 unités de temps alignées)")
        elif d1 in BEARISH:
            score -= 15
        if rsi_reset < 42:
            score += 5
        if vol_ok:
            score += 5
            reasons.append("Volume au-dessus de la moyenne")
        stop = min(float(df["Low"].tail(5).min()) - 0.3 * atr, close - atr)
        risk = close - stop
        out.append(dict(setup="Repli dans la tendance", score=score, stop=stop,
                        tp1=close + 1.5 * risk, tp2=close + 3 * risk, reasons=reasons))

    # 2) Rejet du bas d'un range
    if h1 == RANGE or h4 == RANGE:
        window = df.tail(40)
        r_high, r_low = float(window["High"].max()), float(window["Low"].min())
        width = r_high - r_low
        in_bottom = width > 2 * atr and close <= r_low + 0.25 * width
        if in_bottom and prev["rsi"] < 38 and last["rsi"] > prev["rsi"] and bullish_candle:
            score, reasons = 50, [f"Range {r_low:,.2f} – {r_high:,.2f}",
                                  "Prix dans le bas du range", "RSI en survente qui remonte"]
            if h1 == RANGE and h4 == RANGE:
                score += 10
                reasons.append("Range confirmé sur H1 et H4")
            if df["rsi"].tail(5).min() < 30:
                score += 10
            lower_wick = min(last["Open"], last["Close"]) - last["Low"]
            if lower_wick > 0.4 * (last["High"] - last["Low"]):
                score += 5
                reasons.append("Mèche de rejet sous la bougie")
            if h4 in (TREND_DOWN,) or d1 == TREND_DOWN:
                score -= 15
            stop = r_low - 0.5 * atr
            out.append(dict(setup="Rebond sur le bas du range", score=score, stop=stop,
                            tp1=r_low + width / 2, tp2=r_high - 0.2 * atr, reasons=reasons))

    # 3) Cassure après compression
    level = float(df["High"].iloc[-21:-1].max())
    fresh = prev["Close"] <= level < close
    if fresh and close > level + 0.1 * atr and (last["High"] - last["Low"]) > 1.2 * atr:
        if h4 not in BEARISH and h4 != VOLATILE:
            score, reasons = 50, [f"Cassure du plus haut 20 bougies ({level:,.2f})",
                                  "Bougie de momentum (> 1.2 ATR)"]
            if pct_rank_last(df["bbw"].iloc[:-1], 120) < 0.4:
                score += 10
                reasons.append("Sortie d'une phase de compression")
            if h4 in BULLISH:
                score += 10
                reasons.append("Dans le sens de la tendance H4")
            if d1 in BULLISH:
                score += 5
            if vol_ok:
                score += 5
                reasons.append("Volume en hausse")
            stop = level - 0.8 * atr
            risk = close - stop
            out.append(dict(setup="Cassure", score=score, stop=stop,
                            tp1=close + 1.5 * risk, tp2=close + 3 * risk, reasons=reasons))
    return out


def evaluate(instrument: str, frames: dict[str, pd.DataFrame], regimes: dict[str, Regime],
             upcoming_events: list[str] | None = None) -> list[Signal]:
    """Retourne les signaux (triés par score) détectés sur la dernière bougie H1 clôturée."""
    df = frames.get("H1")
    if df is None:
        return []
    df = df.dropna(subset=["ema50", "atr", "rsi", "bbw"])
    if len(df) < 60:
        return []

    codes = {tf: (r.code if r else None) for tf, r in
             ((tf, regimes.get(tf)) for tf in ("H1", "H4", "D1"))}
    results: list[Signal] = []
    for direction in ("ACHAT", "VENTE"):
        if direction == "ACHAT":
            view = df
            h1, h4, d1 = codes["H1"], codes["H4"], codes["D1"]
            sign = 1
        else:
            view = _mirror(df)
            h1, h4, d1 = (_mirror_code(codes[k]) for k in ("H1", "H4", "D1"))
            sign = -1
        for s in _long_setups(view, h1, h4, d1):
            warnings = []
            score = s["score"]
            if upcoming_events:
                score -= 15
                warnings.extend(f"Annonce importante bientôt : {e}" for e in upcoming_events)
            results.append(Signal(
                instrument=instrument,
                direction=direction,
                setup=s["setup"],
                score=max(0, min(100, int(score))),
                entry=float(df["Close"].iloc[-1]),
                stop=sign * s["stop"],
                tp1=sign * s["tp1"],
                tp2=sign * s["tp2"],
                reasons=s["reasons"],
                warnings=warnings,
                bar_time=df.index[-1].to_pydatetime(),
            ))
    return sorted(results, key=lambda s: s.score, reverse=True)
