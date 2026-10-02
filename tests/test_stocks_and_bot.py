import asyncio
import numpy as np
import pandas as pd

from signalbot import ai, data, formatting as fmt, moves, stocks
from signalbot.config import INSTRUMENTS
from signalbot.state import State


def _daily(closes):
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=len(closes), freq="D")
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
                         "Close": closes, "Volume": 1e6}, index=idx)


def _patch(monkeypatch, closes, info, earnings):
    monkeypatch.setattr(data, "history", lambda *a, **k: _daily(closes))
    monkeypatch.setattr(data, "ticker_info", lambda t: info)
    monkeypatch.setattr(data, "earnings_dates", lambda t: earnings)


GOOD_INFO = {"shortName": "Good Co", "currency": "USD", "targetMeanPrice": 150,
             "recommendationMean": 1.8, "revenueGrowth": 0.2, "profitMargins": 0.25,
             "forwardPE": 20}


def test_dip_detected_on_quality_stock(monkeypatch):
    closes = list(np.linspace(100, 200, 300)) + list(np.linspace(200, 120, 100))
    _patch(monkeypatch, closes, GOOD_INFO, pd.DataFrame())
    rep = stocks.analyze("GOOD", dip_threshold=20)
    assert rep.drawdown_pct < -35
    assert rep.dip_score >= 60
    assert any("sous son sommet" in r for r in rep.dip_reasons)
    assert rep.earnings_score == 0  # pas de date de résultats connue
    assert "GOOD" in fmt.stock_card(rep, "dip")
    assert "CELI" in fmt.stock_detail(rep)


def test_earnings_setup(monkeypatch):
    now = pd.Timestamp.now(tz="UTC")
    idx = pd.DatetimeIndex([now + pd.Timedelta(days=5)]
                           + [now - pd.Timedelta(days=90 * i) for i in range(1, 7)])
    earnings = pd.DataFrame({"Surprise(%)": [np.nan, 5, 4, 8, 2, 3, -1]}, index=idx)
    _patch(monkeypatch, list(np.linspace(100, 130, 400)), GOOD_INFO, earnings)
    rep = stocks.analyze("GOOD")
    assert rep.next_earnings is not None
    assert rep.beat_rate > 80
    assert rep.earnings_score >= 70
    assert rep.dip_score == 0  # pas de grosse baisse


def test_state_roundtrip(tmp_path):
    s = State(tmp_path / "s.json")
    s.add_chat(42)
    s.mark_seen("news:x")
    s.touch("k")
    s2 = State(tmp_path / "s.json")
    assert s2.chats == [42]
    assert s2.seen("news:x")
    assert s2.cooling_down("k", 60)
    assert not s2.cooling_down("other", 60)


def test_ai_disabled_without_key(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    assert asyncio.run(ai.ask("test")) is None


def test_move_explanation_rule_based(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    monkeypatch.setattr(moves, "fetch_news", lambda *a: [])
    monkeypatch.setattr(moves, "fetch_calendar", lambda *a: [])
    monkeypatch.setattr(moves, "positioning", lambda key: __import__("signalbot.positioning",
                        fromlist=["Positioning"]).Positioning(key))
    monkeypatch.setattr(moves, "cross_market_context",
                        lambda: [("DXY (dollar US)", 104.0, 0.5), ("Taux US 10 ans", 4.3, 1.5)])
    move = moves.Move(INSTRUMENTS["XAU"], 2300.0, -0.8, -1.2, "15 min")
    txt = asyncio.run(moves.explain(move))
    assert "ALERTE" in txt
    assert "dollar" in txt.lower()


def test_detect_move_threshold(monkeypatch):
    monkeypatch.setattr(data, "is_fresh", lambda t: True)
    monkeypatch.setattr(data, "last_change",
                        lambda t, m: (100.0, -0.7) if m == 15 else (100.0, -0.9))
    mv = moves.detect_move(INSTRUMENTS["NQ"])
    assert mv and mv.window == "15 min" and mv.direction == "baisse"
    monkeypatch.setattr(data, "last_change", lambda t, m: (100.0, 0.1))
    assert moves.detect_move(INSTRUMENTS["NQ"]) is None
    monkeypatch.setattr(data, "is_fresh", lambda t: False)
    monkeypatch.setattr(data, "last_change", lambda t, m: (100.0, -5.0))
    assert moves.detect_move(INSTRUMENTS["NQ"]) is None  # marché fermé


def test_bot_builds(monkeypatch):
    from signalbot import bot
    monkeypatch.setattr(bot.settings, "telegram_token", "123456:TEST")
    app = bot.build_app()
    assert len(app.handlers[0]) >= 14


def test_chunks():
    from signalbot.bot import _chunks
    text = "\n".join("x" * 100 for _ in range(100))
    parts = _chunks(text)
    assert all(len(p) <= 3901 for p in parts)
    assert "".join(parts).count("x") == 100 * 100
