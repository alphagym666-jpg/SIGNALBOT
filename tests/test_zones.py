import asyncio
import types
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from signalbot import bot, chart, zones as zn
from signalbot.levels import Level
from signalbot.news import EconEvent
from signalbot.positioning import Positioning
from signalbot.state import State


def _candles(n=60, start=25000.0):
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("h"), periods=n, freq="h")
    c = start + np.cumsum(np.random.default_rng(0).normal(0, 20, n))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "Close": c, "High": np.maximum(o, c) + 10,
                         "Low": np.minimum(o, c) - 10, "Volume": 1000.0}, index=idx)


def test_levels_merge_into_zones_with_force():
    price = 25000.0
    levels = [Level("Call wall (options)", 25200, ""), Level("Haut d'hier", 25190, ""),
              Level("POC d'hier", 24900, ""), Level("Bascule gamma (options)", 24890, ""),
              Level("Bas zone de valeur", 24700, ""), Level("Loin", 30000, "")]
    zones = zn.build("NQ", price, levels)
    assert [z.side for z in zones] == ["VENTE", "ACHAT", "ACHAT"]  # du plus haut au plus bas, « Loin » ignoré
    assert zones[0].reasons == ["Haut d'hier", "Call wall"] and zones[0].force == 3
    assert zones[1].force == 3 and "Bascule gamma" in zones[1].reasons
    shifted = zn.shift(zones, -40)
    assert shifted[0].low == zones[0].low - 40


def test_last_week_and_ai_levels():
    idx = pd.date_range("2026-10-01", "2026-10-12", freq="D", tz="UTC")
    daily = pd.DataFrame({"High": range(100, 100 + len(idx)), "Low": range(50, 50 + len(idx))}, index=idx)
    labels = {lv.label: lv.price for lv in zn.last_week_levels(daily)}
    assert labels == {"Haut semaine passée": 110.0, "Bas semaine passée": 54.0}  # semaine du 5 au 11 oct.
    plan = zn.plan_to_levels([{"kind": "achat", "price": 4200, "note": "rebond"}, {"kind": "x"}])
    assert len(plan) == 1 and plan[0].label == "Zone achat IA"


def test_chart_is_a_png():
    zones = zn.build("NQ", 25000, [Level("Haut d'hier", 25100, ""), Level("POC d'hier", 24900, "")])
    png = chart.render("Test", _candles(), zones, 25000, "sous-titre", offset=-30)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000
    assert chart.render("Vide", pd.DataFrame(), [], None)[:4] == b"\x89PNG"


def _fake_market(monkeypatch, tmp_path, events=()):
    monkeypatch.setattr(bot.settings, "allowed_chat_ids", [1])
    monkeypatch.setattr(bot, "state", State(tmp_path / "s.json"))
    monkeypatch.setattr(bot, "fetch_calendar", lambda *a, **k: list(events))
    monkeypatch.setattr(bot, "positioning", lambda key: Positioning(key))
    monkeypatch.setattr(bot, "_all_levels", lambda key, pos=None: (
        25000.0, [Level("Haut d'hier", 25100, ""), Level("Call wall (options)", 25110, ""),
                  Level("POC d'hier", 24900, "")]))
    monkeypatch.setattr(bot.data, "history", lambda *a, **k: _candles())
    monkeypatch.setattr(bot.data, "last_change", lambda t, m: (25000.0, 0.1))
    photos, texts = [], []

    class FB:
        async def send_photo(self, chat_id, photo, caption=None, **kw):
            photos.append((photo.getvalue(), caption))

        async def send_message(self, chat_id, text, **kw):
            texts.append(text)

    return photos, texts, FB()


def test_zones_button_sends_two_charts(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    photos, texts, fb = _fake_market(monkeypatch, tmp_path,
                                     [EconEvent("CPI m/m", "USD", now + timedelta(hours=1), "High", "0.2%", "0.3%")])

    class Chat:
        id = 1

        async def send_message(self, text, **kw):
            texts.append(text)

    upd = types.SimpleNamespace(effective_chat=Chat(), get_bot=lambda: fb,
                                message=types.SimpleNamespace(text=bot.BTN_ZONES))
    asyncio.run(bot.on_text(upd, types.SimpleNamespace(args=None, user_data={})))
    assert len(photos) == 2 and all(p[0][:4] == b"\x89PNG" for p in photos)
    cap = photos[0][1]
    assert "Nasdaq" in cap and "VENTE ★★★" in cap and "Haut d'hier, Call wall" in cap and "CPI m/m" in cap
    assert "Or" in photos[1][1] and any("/ecart" in t for t in texts)
    assert all(len(p[1]) <= 1024 for p in photos)


def test_ecart_moves_zones_to_metatrader_price(monkeypatch, tmp_path):
    photos, texts, fb = _fake_market(monkeypatch, tmp_path)

    class Chat:
        id = 1

        async def send_message(self, text, **kw):
            texts.append(text)

    upd = types.SimpleNamespace(effective_chat=Chat(), get_bot=lambda: fb)
    asyncio.run(bot.cmd_ecart.__wrapped__(upd, types.SimpleNamespace(args=["nq", "24960"])))
    assert bot._broker_offset("NQ") == -40 and "-40.00" in texts[-1]
    asyncio.run(bot.cmd_zones.__wrapped__(upd, types.SimpleNamespace(args=None)))
    assert "prix <b>24,960.00</b>" in photos[0][1] and "Prix MetaTrader" in photos[0][1]
    asyncio.run(bot.cmd_ecart.__wrapped__(upd, types.SimpleNamespace(args=["nq", "0"])))
    assert bot._broker_offset("NQ") == 0


def test_zones_sent_automatically_after_big_news(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    photos, texts, fb = _fake_market(
        monkeypatch, tmp_path, [EconEvent("Non-Farm Payrolls", "USD", now - timedelta(minutes=4), "High", "1", "2")])
    monkeypatch.setattr(bot.settings, "zones_after_news", True)
    monkeypatch.setattr(bot.ai, "enabled", lambda: False)
    ctx = types.SimpleNamespace(bot=fb)
    asyncio.run(bot.job_calendar(ctx))
    assert any("Vient de sortir" in t for t in texts)
    assert len(photos) == 2 and "Zones mises à jour" in photos[0][1] and "Non-Farm" in photos[0][1]
    asyncio.run(bot.job_zones(ctx))  # envoi programmé (matin / midi)
    assert len(photos) == 4
