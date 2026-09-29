"""Configuration du bot, lue depuis les variables d'environnement (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = _env(name)
    if not raw:
        return list(default)
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Instrument:
    key: str
    name: str
    ticker: str
    # Seuils d'alerte "gros mouvement" (en %)
    move_15m_pct: float
    move_60m_pct: float
    # Mots-clés pour filtrer les news pertinentes
    keywords: tuple[str, ...] = ()


INSTRUMENTS: dict[str, Instrument] = {
    "NQ": Instrument(
        key="NQ",
        name="Nasdaq 100",
        ticker=_env("NQ_TICKER", "NQ=F"),
        move_15m_pct=_env_float("NQ_MOVE_15M_PCT", 0.6),
        move_60m_pct=_env_float("NQ_MOVE_60M_PCT", 1.1),
        keywords=("nasdaq", "tech", "stocks", "equities", "wall street", "s&p", "nvidia",
                  "apple", "microsoft", "amazon", "meta", "alphabet", "google", "tesla",
                  "semiconductor", "chip", "ai "),
    ),
    "XAU": Instrument(
        key="XAU",
        name="Or (XAUUSD)",
        ticker=_env("XAU_TICKER", "GC=F"),
        move_15m_pct=_env_float("XAU_MOVE_15M_PCT", 0.45),
        move_60m_pct=_env_float("XAU_MOVE_60M_PCT", 0.9),
        keywords=("gold", "bullion", "precious metal", "safe haven", "safe-haven", "xau",
                  "central bank buying", "dollar", "yields"),
    ),
}

# Contexte inter-marchés utilisé pour expliquer les mouvements
CONTEXT_TICKERS: dict[str, str] = {
    "DXY (dollar US)": "DX-Y.NYB",
    "Taux US 10 ans": "^TNX",
    "VIX (peur)": "^VIX",
    "Pétrole WTI": "CL=F",
    "S&P 500": "ES=F",
}

DEFAULT_STOCKS = [
    # US - grosses caps
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "COST", "ADBE", "CRM", "ORCL", "PLTR", "UBER", "SHOP", "ASML", "TSM", "LLY",
    "UNH", "V", "MA", "JPM", "DIS", "NKE", "SBUX", "PYPL", "INTC", "BA",
    # Canada (pratique pour un CELI : pas de retenue US sur les dividendes)
    "RY.TO", "TD.TO", "BN.TO", "CNR.TO", "CP.TO", "ENB.TO", "ATD.TO", "CSU.TO",
    "SHOP.TO", "DOL.TO", "L.TO", "WCN.TO",
]


@dataclass
class Settings:
    telegram_token: str = _env("TELEGRAM_BOT_TOKEN")
    # Chats autorisés (séparés par des virgules). Vide = le premier /start devient propriétaire.
    allowed_chat_ids: list[int] = field(
        default_factory=lambda: [int(x) for x in _env("TELEGRAM_CHAT_ID").split(",") if x.strip()]
    )

    anthropic_api_key: str = _env("ANTHROPIC_API_KEY")
    claude_model: str = _env("CLAUDE_MODEL", "claude-opus-5-5")
    claude_effort: str = _env("CLAUDE_EFFORT", "medium")
    claude_web_search: bool = _env("CLAUDE_WEB_SEARCH", "1") not in ("0", "false", "no")

    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo(_env("TZ_NAME", "America/Toronto")))
    state_file: Path = Path(_env("STATE_FILE", "data/state.json"))

    # Intervalles des tâches automatiques (secondes)
    move_check_seconds: int = _env_int("MOVE_CHECK_SECONDS", 120)
    signal_check_seconds: int = _env_int("SIGNAL_CHECK_SECONDS", 900)
    news_check_seconds: int = _env_int("NEWS_CHECK_SECONDS", 300)
    calendar_check_seconds: int = _env_int("CALENDAR_CHECK_SECONDS", 300)

    # Qualité minimale d'un signal pour être envoyé automatiquement (0-100)
    min_signal_score: int = _env_int("MIN_SIGNAL_SCORE", 70)
    signal_cooldown_hours: float = _env_float("SIGNAL_COOLDOWN_HOURS", 4)
    # Score minimal pour qu'une news soit poussée automatiquement
    min_news_score: int = _env_int("MIN_NEWS_SCORE", 6)
    # Rappel avant une annonce économique importante (minutes)
    calendar_reminder_minutes: int = _env_int("CALENDAR_REMINDER_MINUTES", 30)

    # Heures des rapports quotidiens (heure locale, HH:MM)
    morning_brief_time: str = _env("MORNING_BRIEF_TIME", "08:45")
    stocks_report_time: str = _env("STOCKS_REPORT_TIME", "17:15")

    # Scanner d'actions
    stocks: list[str] = field(default_factory=lambda: _env_list("STOCK_WATCHLIST", DEFAULT_STOCKS))
    dip_threshold_pct: float = _env_float("DIP_THRESHOLD_PCT", 20)
    earnings_lookahead_days: int = _env_int("EARNINGS_LOOKAHEAD_DAYS", 14)


settings = Settings()
