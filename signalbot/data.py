"""Récupération des prix via yfinance, avec un petit cache mémoire."""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
import yfinance as yf

from .indicators import add_indicators, resample_ohlc

log = logging.getLogger(__name__)

_cache: dict[tuple, tuple[float, Any]] = {}


def _cached(key: tuple, ttl: float, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


def history(ticker: str, period: str, interval: str, ttl: float = 60) -> pd.DataFrame:
    """OHLCV pour un ticker. Retourne un DataFrame vide en cas d'erreur."""

    def fetch() -> pd.DataFrame:
        try:
            df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
        except Exception as exc:  # réseau, ticker inconnu, etc.
            log.warning("history(%s, %s, %s) a échoué: %s", ticker, period, interval, exc)
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        df = df[[c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]]
        return df.dropna(subset=["Close"])

    return _cached(("hist", ticker, period, interval), ttl, fetch)


def multi_timeframe(ticker: str) -> dict[str, pd.DataFrame]:
    """D1, H4 et H1 avec indicateurs. H4 est reconstruit à partir du H1."""
    daily = history(ticker, "2y", "1d", ttl=900)
    hourly = history(ticker, "60d", "1h", ttl=240)
    if not hourly.empty:
        # On ne garde que les bougies clôturées (la dernière est souvent encore en cours)
        now = pd.Timestamp.now(tz=hourly.index.tz)
        hourly = hourly[hourly.index + pd.Timedelta(hours=1) <= now]
    frames: dict[str, pd.DataFrame] = {}
    if not daily.empty:
        frames["D1"] = add_indicators(daily)
    if not hourly.empty:
        frames["H1"] = add_indicators(hourly)
        frames["H4"] = add_indicators(resample_ohlc(hourly, "4h"))
    return frames


def intraday(ticker: str) -> pd.DataFrame:
    return history(ticker, "5d", "5m", ttl=60)


def is_fresh(ticker: str, max_age_minutes: int = 20) -> bool:
    """Vrai si la dernière bougie 5 min est récente (marché ouvert)."""
    df = intraday(ticker)
    if df.empty:
        return False
    age = pd.Timestamp.now(tz=df.index.tz) - df.index[-1]
    return age <= pd.Timedelta(minutes=max_age_minutes)


def last_change(ticker: str, minutes: int) -> tuple[float, float] | None:
    """(dernier prix, variation en % sur `minutes`) à partir des bougies 5 min."""
    df = intraday(ticker)
    if df.empty or len(df) < 2:
        return None
    last = df.iloc[-1]
    ref_time = df.index[-1] - pd.Timedelta(minutes=minutes)
    past = df[df.index <= ref_time]
    ref = past["Close"].iloc[-1] if not past.empty else df["Open"].iloc[0]
    return float(last["Close"]), float((last["Close"] / ref - 1) * 100)


def ticker_info(ticker: str) -> dict:
    def fetch() -> dict:
        try:
            return yf.Ticker(ticker).info or {}
        except Exception as exc:
            log.warning("info(%s) a échoué: %s", ticker, exc)
            return {}

    return _cached(("info", ticker), 6 * 3600, fetch)


def earnings_dates(ticker: str) -> pd.DataFrame:
    def fetch() -> pd.DataFrame:
        try:
            df = yf.Ticker(ticker).get_earnings_dates(limit=12)
        except Exception as exc:
            log.info("earnings_dates(%s) indisponible: %s", ticker, exc)
            return pd.DataFrame()
        return df if df is not None else pd.DataFrame()

    return _cached(("earn", ticker), 6 * 3600, fetch)
