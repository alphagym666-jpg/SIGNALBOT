import asyncio
import types

import pandas as pd
import pytest

from signalbot import bot, coach, formatting as fmt, liquidity as liq
from signalbot.config import INSTRUMENTS
from signalbot.levels import Level
from signalbot.positioning import GammaLevels, Positioning
from signalbot.sentiment import Sentiment
from signalbot.state import State
from signalbot.zones import Zone


def _bars(rows, start="2026-10-12 13:00", freq="5min"):
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx).assign(Volume=100.0)


def _regime(bull):
    return types.SimpleNamespace(bullish=bull, bearish=not bull, label="Tendance haussière" if bull else "Tendance baissière")


ZONE = Zone(99.5, 100.5, "ACHAT", ["Put wall", "POC d'hier"], 4)


def test_rejection_of_buy_zone_gives_strong_buy_signal():
    # 15 min : on descend dans la zone puis une bougie verte clôture au-dessus
    df15 = _bars([(103, 103.5, 102, 102.2), (102.2, 102.4, 100.8, 101), (101, 101.2, 99.6, 99.9),
                  (99.9, 101.4, 99.7, 101.2)], freq="15min")
    since = df15.index[2]
    assert coach.confirmation(ZONE, df15, since, atr=1.0) == "rejet"
    sweep = liq.Sweep(Level("Bas Asie", 99.8, ""), "bas", 99.6, 99.9, since)
    s = coach.build_setup("XAU", ZONE, "rejet", df15, since, 1.0,
                          [ZONE, Zone(104, 104.5, "VENTE", ["Haut d'hier"], 2)],
                          {"H4": _regime(True), "H1": _regime(True)}, Sentiment(40), [sweep], True, None)
    assert s.direction == "ACHAT" and s.score >= 90 and s.target == 104 and s.stop < 99.5
    assert any("sweep" in r for r in s.reasons)
    msg = fmt.setup_message(s, INSTRUMENTS["XAU"], "Je la prendrais.", 70)
    assert f"SIGNAL ACHAT {s.score}/100" in msg and "avis du prof" in msg


def test_break_of_buy_zone_gives_sell_and_news_blocks_it():
    df15 = _bars([(101, 101.2, 100, 100.2), (100.2, 100.3, 98.9, 99.0)], freq="15min")
    assert coach.confirmation(ZONE, df15, df15.index[0], atr=1.0) == "cassure"
    s = coach.build_setup("NQ", ZONE, "cassure", df15, df15.index[0], 1.0, [ZONE],
                          {"H4": _regime(False)}, Sentiment(-30), [], False, "CPI m/m à 08:30")
    assert s.direction == "VENTE" and s.score <= 40 and s.warnings[0].startswith("🚫 CPI")
    msg = fmt.setup_message(s, INSTRUMENTS["NQ"], None, 70)
    assert "confirmation faible" in msg and "Je ne la prendrais pas" in msg


def test_no_confirmation_while_price_stays_in_zone():
    df15 = _bars([(101, 101.2, 99.8, 100.0), (100.0, 100.4, 99.6, 100.1)], freq="15min")
    assert coach.confirmation(ZONE, df15, df15.index[0], atr=1.0) is None
    assert coach.zone_hit([ZONE, Zone(99.9, 100.2, "ACHAT", ["x"], 1)], 100.0, 3) is ZONE
    assert coach.zone_hit([ZONE], 100.0, 5) is None


def test_equal_highs_and_sweeps():
    assert liq.equal_levels([100.0, 100.02, 105.0, 99.99], tol=0.05) == [pytest.approx(100.0033, abs=1e-3)]
    df5 = _bars([(98.9, 99.1, 98.7, 99.0), (99, 99.5, 98.8, 99.2), (99.2, 100.3, 99.1, 99.7), (99.7, 99.9, 99.4, 99.6),
                 (99.6, 99.8, 99.5, 99.7)])
    sweeps = liq.find_sweeps(df5, [Level("Liquidité : sommets égaux", 100.0, ""), Level("VWAP", 99.5, "")])
    assert len(sweeps) == 1 and sweeps[0].side == "haut" and not sweeps[0].bullish and sweeps[0].extreme == 100.3


def test_touch_message_says_what_to_wait_for():
    msg = fmt.zone_touch_message(INSTRUMENTS["XAU"], ZONE, 100.0, "NFP à 08:30")
    assert "rejette" in msg and "achat probable" in msg and "casse" in msg and "vente" in msg
    assert "ne trade pas avant la news" in msg and "Attends la confirmation" in msg


def test_coach_job_touch_then_signal(monkeypatch, tmp_path):
    monkeypatch.setattr(bot.settings, "allowed_chat_ids", [1])
    monkeypatch.setattr(bot.settings, "coach_alerts", True)
    monkeypatch.setattr(bot, "state", State(tmp_path / "s.json"))
    monkeypatch.setattr(bot, "fetch_calendar", lambda *a, **k: [])
    monkeypatch.setattr(bot.ai, "enabled", lambda: False)
    monkeypatch.setattr(bot.data, "is_fresh", lambda t: True)
    pos = Positioning("XAU", gamma=GammaLevels("XAU", 100, 105, 95, 98, 1.0))
    snap = bot.Snapshot("XAU", pos, 100.0, [ZONE], Sentiment(30), [], {"H4": _regime(True)}, [])
    monkeypatch.setattr(bot, "_snapshot", lambda key: snap if key == "XAU" else bot.Snapshot(key, None, None, [], Sentiment(0)))
    sent = []

    class FB:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    ctx = types.SimpleNamespace(bot=FB())
    asyncio.run(bot.job_coach(ctx))
    assert len(sent) == 1 and "est dans une zone ACHAT" in sent[0]
    asyncio.run(bot.job_coach(ctx))
    assert len(sent) == 1  # pas encore de confirmation (pas assez de bougies) : silence

    ts = pd.Timestamp(bot.state.data["coach"]["XAU"]["ts"], unit="s", tz="UTC").floor("15min")
    rows = [(101, 101.2, 100.6, 100.9)] * 16 + [(100.9, 101, 99.6, 99.9), (99.9, 101.6, 99.7, 101.4)]
    df5 = _bars([r for r in rows for _ in range(3)], start=ts - pd.Timedelta(minutes=15 * 16))
    monkeypatch.setattr(bot.data, "intraday", lambda t: df5)
    asyncio.run(bot.job_coach(ctx))
    assert len(sent) == 2 and "SIGNAL ACHAT" in sent[1] and "/100" in sent[1]
    assert "XAU" not in bot.state.data["coach"]
