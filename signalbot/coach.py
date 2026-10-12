"""Le coach : suit le prix dans les zones et donne un signal noté sur 100 quand c'est confirmé.

1. Le prix entre dans une zone forte (gamma, liquidité, hier…) → message « attends la confirmation » :
   si ça rejette → achat probable (zone ACHAT) ; si ça casse → risque de vente.
2. Confirmation sur une bougie 15 min CLÔTURÉE :
   - rejet : clôture de retour hors de la zone du bon côté ;
   - cassure : clôture franchement de l'autre côté de la zone.
3. Score /100 : force de la zone, tendance H1 / H4, sentiment (Myfxbook, COT…), sweep de liquidité,
   régime gamma, news proche (une grosse news bloque le signal).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .liquidity import Sweep
from .regime import Regime
from .sentiment import Sentiment
from .zones import Zone


@dataclass
class Setup:
    key: str
    direction: str  # "ACHAT" ou "VENTE"
    kind: str  # "rejet" ou "cassure"
    score: int
    entry: float
    stop: float
    target: float
    zone: Zone
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def rr(self) -> float:
        risk = abs(self.entry - self.stop)
        return abs(self.target - self.entry) / risk if risk else 0.0


def zone_hit(zones: list[Zone], price: float, min_force: int = 2) -> Zone | None:
    """Zone forte dans laquelle se trouve le prix (la plus forte s'il y en a plusieurs)."""
    inside = [z for z in zones if z.low <= price <= z.high and z.force >= min_force]
    return max(inside, key=lambda z: z.force, default=None)


def to_15m(df5: pd.DataFrame) -> pd.DataFrame:
    """Bougies 15 min CLÔTURÉES à partir des 5 min."""
    if df5 is None or df5.empty:
        return pd.DataFrame()
    df = df5.resample("15min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    now = df5.index[-1] + pd.Timedelta(minutes=5)
    return df[df.index + pd.Timedelta(minutes=15) <= now]


def confirmation(zone: Zone, df15: pd.DataFrame, since: pd.Timestamp, atr: float) -> str | None:
    """« rejet », « cassure » ou None d'après la dernière bougie 15 min clôturée depuis le contact."""
    after = df15[df15.index >= since.floor("15min")]
    if after.empty:
        return None
    last = after.iloc[-1]
    buf = 0.15 * atr
    if zone.side == "ACHAT":
        if last["Close"] < zone.low - buf:
            return "cassure"
        if last["Close"] > zone.high and last["Close"] > last["Open"] and after["Low"].min() <= zone.high:
            return "rejet"
    else:
        if last["Close"] > zone.high + buf:
            return "cassure"
        if last["Close"] < zone.low and last["Close"] < last["Open"] and after["High"].max() >= zone.low:
            return "rejet"
    return None


def build_setup(key: str, zone: Zone, kind: str, df15: pd.DataFrame, since: pd.Timestamp, atr: float,
                zones: list[Zone], regimes: dict[str, Regime], st: Sentiment | None,
                sweeps: list[Sweep], gamma_positive: bool | None, news_soon: str | None) -> Setup:
    after = df15[df15.index >= since.floor("15min")]
    entry = float(after["Close"].iloc[-1])
    buy = (zone.side == "ACHAT") == (kind == "rejet")
    direction = "ACHAT" if buy else "VENTE"
    pad = 0.3 * atr
    if kind == "rejet":
        stop = (min(float(after["Low"].min()), zone.low) - pad) if buy else (max(float(after["High"].max()), zone.high) + pad)
    else:
        stop = zone.high + pad if not buy else zone.low - pad
    risk = abs(entry - stop) or atr
    if buy:
        nxt = [z.low for z in zones if z.side == "VENTE" and z.low > entry + 0.5 * risk]
        target = min(nxt) if nxt else entry + 2 * risk
    else:
        nxt = [z.high for z in zones if z.side == "ACHAT" and z.high < entry - 0.5 * risk]
        target = max(nxt) if nxt else entry - 2 * risk

    score, reasons, warnings = 50, [], []
    force_pts = min(zone.force, 5) * 5
    score += force_pts
    reasons.append(f"zone {zone.side} {'★' * min(zone.force, 5)} ({', '.join(zone.reasons)})"
                   + (" rejetée" if kind == "rejet" else " cassée") + " à la clôture 15 min")
    for tf in ("H4", "H1"):
        r = regimes.get(tf)
        if not r:
            continue
        if (buy and r.bullish) or (not buy and r.bearish):
            score += 8
            reasons.append(f"dans le sens de la tendance {tf} ({r.label.lower()})")
        elif (buy and r.bearish) or (not buy and r.bullish):
            score -= 10
            warnings.append(f"contre la tendance {tf} ({r.label.lower()})")
    if st:
        if (buy and st.score >= 15) or (not buy and st.score <= -15):
            score += 8
            reasons.append(f"sentiment {st.label.lower()} ({st.buyers_pct} % acheteur)")
        elif (buy and st.score <= -15) or (not buy and st.score >= 15):
            score -= 8
            warnings.append(f"sentiment {st.label.lower()} contre le trade")
    sw = [s for s in sweeps if s.bullish == buy]
    if sw:
        score += 10
        reasons.append(f"sweep de liquidité ({sw[0].level.label} {sw[0].level.price:,.2f}) : stops pris puis retour")
    if gamma_positive is not None:
        if kind == "rejet" and gamma_positive:
            score += 5
            reasons.append("gamma positif : les zones tiennent souvent (rebonds)")
        elif kind == "cassure" and not gamma_positive:
            score += 5
            reasons.append("gamma négatif : les cassures vont souvent plus loin")
        elif kind == "cassure" and gamma_positive:
            score -= 5
            warnings.append("gamma positif : risque de faux départ, une reprise de la zone est possible")
    rr = abs(target - entry) / risk
    if rr < 1.2:
        score -= 10
        warnings.append(f"objectif proche (R/R {rr:.1f})")
    if news_soon:
        score = min(score, 40)
        warnings.insert(0, f"🚫 {news_soon} : ne prends pas ce trade avant la news")
    return Setup(key, direction, kind, max(0, min(99, score)), round(entry, 2), round(stop, 2),
                 round(target, 2), zone, reasons, warnings)
