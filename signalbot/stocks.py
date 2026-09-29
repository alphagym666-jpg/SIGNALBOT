"""Scanner d'actions pour le moyen / long terme (CELI).

Deux types d'opportunités :
1. « Earnings bientôt » : résultats trimestriels dans les prochains jours pour une compagnie
   qui bat régulièrement les attentes, avec des analystes positifs.
2. « Grosse baisse » : une bonne compagnie (rentable, en croissance) qui a beaucoup baissé
   depuis son sommet — zone d'accumulation possible pour le long terme.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from . import data
from .indicators import rsi, sma

log = logging.getLogger(__name__)


@dataclass
class StockReport:
    ticker: str
    name: str
    price: float
    currency: str
    drawdown_pct: float  # distance au plus haut 52 semaines (négatif)
    change_1m_pct: float
    rsi_weekly: float | None
    above_200d: bool | None
    next_earnings: datetime | None
    beat_rate: float | None  # % des derniers trimestres où le BPA a battu l'estimé
    avg_surprise: float | None
    analyst_rating: float | None  # 1 = achat fort ... 5 = vente
    target_upside_pct: float | None
    revenue_growth: float | None
    profit_margin: float | None
    forward_pe: float | None
    earnings_score: int = 0
    dip_score: int = 0
    earnings_reasons: list[str] = field(default_factory=list)
    dip_reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


def _num(info: dict, key: str) -> float | None:
    v = info.get(key)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _earnings_stats(ticker: str) -> tuple[datetime | None, float | None, float | None]:
    df = data.earnings_dates(ticker)
    if df.empty:
        return None, None, None
    now = pd.Timestamp.now(tz="UTC")
    idx = df.index.tz_convert("UTC") if df.index.tz is not None else df.index.tz_localize("UTC")
    df = df.set_axis(idx)
    future = df[df.index > now]
    next_date = future.index.min().to_pydatetime() if not future.empty else None
    past = df[df.index <= now]
    if "Surprise(%)" in past.columns:
        surprises = past["Surprise(%)"].dropna().head(8)
        if len(surprises) >= 3:
            return next_date, float((surprises > 0).mean() * 100), float(surprises.mean())
    return next_date, None, None


def analyze(ticker: str, dip_threshold: float = 20, lookahead_days: int = 14) -> StockReport | None:
    hist = data.history(ticker, "2y", "1d", ttl=3600)
    if hist.empty or len(hist) < 60:
        return None
    info = data.ticker_info(ticker)
    close = hist["Close"]
    price = float(close.iloc[-1])
    high_52w = float(close.tail(252).max())
    drawdown = (price / high_52w - 1) * 100
    change_1m = (price / float(close.iloc[-22]) - 1) * 100 if len(close) > 22 else 0.0
    weekly = close.resample("W").last().dropna()
    rsi_w = float(rsi(weekly).iloc[-1]) if len(weekly) > 20 else None
    sma200 = sma(close, 200).iloc[-1]
    above_200d = bool(price > sma200) if pd.notna(sma200) else None

    next_earn, beat_rate, avg_surprise = _earnings_stats(ticker)
    target = _num(info, "targetMeanPrice")
    report = StockReport(
        ticker=ticker,
        name=info.get("shortName") or info.get("longName") or ticker,
        price=price,
        currency=info.get("currency") or "",
        drawdown_pct=drawdown,
        change_1m_pct=change_1m,
        rsi_weekly=rsi_w,
        above_200d=above_200d,
        next_earnings=next_earn,
        beat_rate=beat_rate,
        avg_surprise=avg_surprise,
        analyst_rating=_num(info, "recommendationMean"),
        target_upside_pct=(target / price - 1) * 100 if target else None,
        revenue_growth=_num(info, "revenueGrowth"),
        profit_margin=_num(info, "profitMargins"),
        forward_pe=_num(info, "forwardPE"),
    )
    _score_earnings(report, lookahead_days)
    _score_dip(report, dip_threshold)
    return report


def _quality(r: StockReport) -> tuple[int, list[str], list[str]]:
    """Points de qualité fondamentale communs aux deux analyses."""
    score, good, risks = 0, [], []
    if r.revenue_growth is not None:
        if r.revenue_growth > 0.10:
            score += 10
            good.append(f"Revenus en forte croissance (+{r.revenue_growth * 100:.0f}% sur 1 an)")
        elif r.revenue_growth > 0:
            score += 5
        else:
            risks.append(f"Revenus en baisse ({r.revenue_growth * 100:.0f}%)")
    if r.profit_margin is not None:
        if r.profit_margin > 0.10:
            score += 10
            good.append(f"Très rentable (marge {r.profit_margin * 100:.0f}%)")
        elif r.profit_margin <= 0:
            score -= 10
            risks.append("Pas rentable")
    if r.analyst_rating is not None:
        if r.analyst_rating <= 2.0:
            score += 10
            good.append(f"Analystes positifs (note {r.analyst_rating:.1f}/5, 1 = achat fort)")
        elif r.analyst_rating >= 3.0:
            score -= 5
            risks.append(f"Analystes tièdes (note {r.analyst_rating:.1f}/5)")
    if r.target_upside_pct is not None and r.target_upside_pct >= 15:
        score += 5
        good.append(f"Cible moyenne des analystes {r.target_upside_pct:+.0f}% plus haut")
    return score, good, risks


def _score_earnings(r: StockReport, lookahead_days: int) -> None:
    if not r.next_earnings:
        return
    days = (r.next_earnings - datetime.now(timezone.utc)).days
    if not 0 <= days <= lookahead_days:
        return
    score = 30
    reasons = [f"Résultats le {r.next_earnings:%Y-%m-%d} (dans {days} jour{'s' if days > 1 else ''})"]
    if r.beat_rate is not None:
        if r.beat_rate >= 75:
            score += 20
            reasons.append(f"Bat les attentes {r.beat_rate:.0f}% du temps (surprise moy. "
                           f"{r.avg_surprise:+.1f}%)")
        elif r.beat_rate < 50:
            score -= 10
            r.risks.append(f"Rate souvent les attentes (bat seulement {r.beat_rate:.0f}% du temps)")
    q, good, risks = _quality(r)
    score += q
    reasons += good
    r.risks += risks
    if r.above_200d:
        score += 5
        reasons.append("Au-dessus de sa moyenne 200 jours (tendance long terme saine)")
    if r.change_1m_pct > 15:
        score -= 10
        r.risks.append(f"Déjà +{r.change_1m_pct:.0f}% en 1 mois : les bonnes nouvelles sont "
                       "peut-être déjà dans le prix")
    r.risks.append("Les résultats sont un événement binaire : le titre peut bouger de ±10% "
                   "même sur de bons chiffres (la guidance compte plus que le trimestre)")
    r.earnings_score = max(0, min(100, score))
    r.earnings_reasons = reasons


def _score_dip(r: StockReport, threshold: float) -> None:
    if r.drawdown_pct > -threshold:
        return
    score = 30 + min(15, int((-r.drawdown_pct - threshold) / 2))
    reasons = [f"{r.drawdown_pct:.0f}% sous son sommet de 52 semaines"]
    q, good, risks = _quality(r)
    score += q
    reasons += good
    r.risks += [x for x in risks if x not in r.risks]
    if r.rsi_weekly is not None and r.rsi_weekly < 35:
        score += 10
        reasons.append(f"RSI hebdo en survente ({r.rsi_weekly:.0f}) : vendeurs épuisés ?")
    if r.forward_pe is not None and 0 < r.forward_pe < 25:
        score += 5
        reasons.append(f"Valorisation raisonnable (P/E prévisionnel {r.forward_pe:.0f})")
    if r.change_1m_pct < -15:
        score -= 5
        r.risks.append(f"Chute encore en cours ({r.change_1m_pct:.0f}% sur 1 mois) : "
                       "attendre une stabilisation ou acheter en plusieurs fois (DCA)")
    r.dip_score = max(0, min(100, score))
    r.dip_reasons = reasons


def scan(tickers: list[str], dip_threshold: float, lookahead_days: int) -> list[StockReport]:
    reports = []
    for t in tickers:
        try:
            rep = analyze(t, dip_threshold, lookahead_days)
        except Exception as exc:  # un ticker problématique ne doit pas bloquer le scan
            log.warning("Analyse de %s échouée: %s", t, exc)
            continue
        if rep:
            reports.append(rep)
    return reports
