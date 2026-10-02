import asyncio
import types
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from signalbot import ai, bot, formatting as fmt, positioning as pos
from signalbot.config import INSTRUMENTS
from signalbot.news import NewsItem, score_text
from signalbot.news_explain import NewsExplanation
from signalbot.state import State


def _chain(spot=500.0):
    rows = []
    for k in np.arange(440, 561, 5.0):
        call_oi = 50_000 if k == 520 else 2_000
        put_oi = 60_000 if k == 480 else 2_000
        rows.append({"strike": k, "oi": call_oi, "iv": 0.2, "t": 20 / 365, "sign": 1})
        rows.append({"strike": k, "oi": put_oi, "iv": 0.2, "t": 20 / 365, "sign": -1})
    return pd.DataFrame(rows)


def test_gamma_levels_and_conversion():
    g = pos.levels_from_chain(_chain(), 500.0, ratio=40.0, instrument="NQ", expiries=3)
    assert g.call_wall == 520 * 40 and g.put_wall == 480 * 40  # converti en prix NQ
    assert g.spot == 20_000 and g.expiries == 3
    assert g.flip is None or 440 * 40 < g.flip < 560 * 40
    txt = fmt.positioning_block(INSTRUMENTS["NQ"], pos.Positioning("NQ", gamma=g), False)
    assert "Call wall" in txt and "20,800.00" in txt and "Put wall" in txt


def test_parse_myfxbook_and_cot():
    out = pos.parse_outlook({"error": False, "symbols": [
        {"name": "xauusd", "longPercentage": 72, "shortPercentage": 28, "avgLongPrice": 2300.5,
         "avgShortPrice": 2310.1}, {"name": "BAD"}]})
    assert out["XAUUSD"].long_pct == 72 and "BAD" not in out
    cot = pos.parse_cot([{"report_date_as_yyyy_mm_dd": "2026-09-29T00:00:00.000",
                          "noncomm_positions_long_all": "300000", "noncomm_positions_short_all": "100000",
                          "change_in_noncomm_long_all": "5000", "change_in_noncomm_short_all": "-2000"}], "XAU")
    assert cot.net == 200_000 and cot.net_change == 7_000 and cot.date == "2026-09-29" and cot.long_pct == 75
    assert pos.parse_cot([], "XAU") is None
    txt = fmt.positioning_block(INSTRUMENTS["XAU"], pos.Positioning("XAU", retail=out["XAUUSD"], cot=cot), True)
    assert "très ACHETEUSE" in txt and "+200,000" in txt


def _news(uid="n1"):
    t = "Fed Powell signals surprise rate hike as CPI inflation jumps"
    sc, m, tg = score_text(t)
    return NewsItem(uid, t, "https://x.com/a", "CNBC", datetime.now(timezone.utc), "", sc * 2, m, tg)


def test_news_sent_only_when_market_moves(monkeypatch, tmp_path):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    monkeypatch.setattr(bot.settings, "news_only_if_move", True)
    monkeypatch.setattr(bot.settings, "allowed_chat_ids", [1])
    monkeypatch.setattr(bot, "state", State(tmp_path / "s.json"))
    monkeypatch.setattr(bot, "_positioning_all", lambda: [])
    bot.state.data["seen"] = {"news:old": 1}  # pas le premier lancement
    item = _news()
    monkeypatch.setattr(bot, "fetch_news", lambda h: [item])

    async def fake_explain(items):
        return {n.id: NewsExplanation("Hausse surprise des taux", "La Fed monte ses taux.", "Négatif.",
                                      "baissier", "baissier", 5, rates="hausse") for n in items}

    monkeypatch.setattr(bot, "explain_news", fake_explain)
    prices = {"NQ": 20_000.0, "XAU": 2_300.0}
    monkeypatch.setattr(bot, "_price", lambda inst: prices[inst.key])
    sent = []

    class FB:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    ctx = types.SimpleNamespace(bot=FB())
    asyncio.run(bot.job_news(ctx))
    assert not sent and item.id in bot.state.data["pending_news"]  # en attente, rien d'envoyé

    prices["NQ"] = 20_000 * 1.001  # +0.1 % : pas assez
    asyncio.run(bot.job_news(ctx))
    assert not sent

    prices["NQ"] = 20_000 * 0.994  # -0.6 % : le marché réagit
    asyncio.run(bot.job_news(ctx))
    assert len(sent) == 1 and "NEWS QUI FAIT BOUGER" in sent[0] and "-0.60 %" in sent[0]
    assert not bot.state.data["pending_news"]


def test_pending_news_dropped_if_market_ignores_it(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "state", State(tmp_path / "s.json"))
    monkeypatch.setattr(bot, "_price", lambda inst: 100.0)
    old = datetime.now(timezone.utc).timestamp() - 3 * 3600
    bot.state.data["pending_news"] = {"x": {"t": old, "prices": {"NQ": 100.0, "XAU": 100.0},
                                            "item": {}, "expl": {}}}
    sent = []

    class FB:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    asyncio.run(bot._check_pending_news(types.SimpleNamespace(bot=FB())))
    assert not sent and not bot.state.data["pending_news"]
