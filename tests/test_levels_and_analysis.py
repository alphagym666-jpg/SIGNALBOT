import asyncio
import types

from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from signalbot import ai, analysis, bot, formatting as fmt, levels
from signalbot.state import State

NY = ZoneInfo("America/New_York")


def _five_min(days=2):
    """Deux séances de bougies 5 min : hier très échangé autour de 100, aujourd'hui vers 104."""
    start = pd.Timestamp("2026-09-30 18:00", tz=NY)
    idx = pd.date_range(start, periods=days * 276, freq="5min")  # 23 h de cotation par séance
    n = len(idx)
    rng = np.random.default_rng(0)
    close = np.r_[100 + rng.normal(0, 0.3, n // 2), 104 + rng.normal(0, 0.3, n - n // 2)]
    vol = np.r_[np.full(n // 2, 1000.0), np.full(n - n // 2, 500.0)]
    vol[n // 4] = 50_000  # gros volume à un prix précis hier
    close[n // 4] = 99.0
    return pd.DataFrame({"Open": close, "High": close + 0.2, "Low": close - 0.2, "Close": close,
                         "Volume": vol}, index=idx.tz_convert("UTC"))


def test_pro_levels_vwap_yesterday_and_profile():
    lv = levels.compute(_five_min(), "NQ")
    assert lv is not None
    vwap, poc = lv.get("VWAP"), lv.get("POC d'hier")
    assert 103 < vwap < 105  # séance du jour autour de 104
    assert 98.5 < poc < 99.6  # le gros volume d'hier à 99
    assert lv.get("Haut d'hier") > lv.get("Bas zone de valeur") >= lv.get("Bas d'hier")
    assert lv.get("Haut de la nuit") is not None or lv.get("Haut de la nuit (en cours)") is not None


def test_session_dates_start_at_6pm_new_york():
    idx = pd.DatetimeIndex([pd.Timestamp("2026-10-01 17:55", tz=NY), pd.Timestamp("2026-10-01 18:00", tz=NY)])
    d = levels.session_dates(idx)
    assert str(d[0]) == "2026-10-01" and str(d[1]) == "2026-10-02"


def test_manual_levels_parse_and_etf_conversion():
    e = levels.parse_manual(["call", "25000", "put=24000", "flip", "24500"])
    assert e == {"Call wall": 25000.0, "Put wall": 24000.0, "Bascule gamma": 24500.0}
    out = levels.manual_levels({"Call wall": 520.0}, price=20_000, etf_ratio=40.0)  # prix QQQ
    assert out[0].price == 20_800 and "SpotGamma" in out[0].label


def _inputs():
    return [analysis.MarketInput("NQ", 20_000.0, {}, [levels.Level("Haut d'hier", 20_100, "r")], None),
            analysis.MarketInput("XAU", 2_300.0, {}, [], None)]


def test_analysis_with_ai(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "sk-test")

    async def fake(prompt, schema, *, system, effort="low"):
        assert schema is analysis.SCHEMA and "20100" in prompt and effort == "high"
        plan = {"bias": "haussier", "confidence": "moyenne", "whats_happening": "Les taux baissent.",
                "main_scenario": "Cassure de 20 100.", "alt_scenario": "Rejet sous 20 100.",
                "buy_zones": "Repli vers 20 000.", "sell_zones": "Aucune.", "invalidation": "Sous 19 900.",
                "avoid": "Trader pendant le CPI."}
        return {"overview": "Marché soulagé par la baisse des taux.", "risk_events": ["CPI 8 h 30"],
                "markets": [dict(plan, key="NQ"), dict(plan, key="XAU", bias="neutre")]}

    monkeypatch.setattr(ai, "ask_json", fake)
    a = asyncio.run(analysis.analyze(_inputs(), [], [], [], ZoneInfo("America/Toronto")))
    assert a.ai and [m.key for m in a.markets] == ["NQ", "XAU"]
    msg = fmt.analysis_message(a, _inputs(), ZoneInfo("America/Toronto"))
    for txt in ("Ce qui se trame", "HAUSSIER", "Scénario principal", "Invalidation", "CPI 8 h 30",
                "prix actuel 20,000.00"):
        assert txt in msg


def test_analysis_without_ai_is_honest(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    a = asyncio.run(analysis.analyze(_inputs(), [], [], [], ZoneInfo("America/Toronto")))
    assert not a.ai and "ANTHROPIC_API_KEY" in a.overview and all(m.confidence == "faible" for m in a.markets)


def test_level_alert_once_per_level(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "state", State(tmp_path / "s.json"))
    monkeypatch.setattr(bot.settings, "allowed_chat_ids", [1])
    monkeypatch.setattr(bot.settings, "level_alerts", True)
    monkeypatch.setattr(bot.data, "is_fresh", lambda t: True)
    monkeypatch.setattr(bot.data, "last_change", lambda t, m: (20_000.0, -0.1))  # il y a 15 min : 20 020
    lv = [levels.Level("Put wall (options)", 19_990.0, "support"), levels.Level("Haut d'hier", 20_300.0, "r")]
    monkeypatch.setattr(bot, "_all_levels", lambda key, pos=None: (20_000.0, lv) if key == "NQ" else (None, []))
    sent = []

    class FB:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    ctx = types.SimpleNamespace(bot=FB())
    asyncio.run(bot.job_levels(ctx))
    asyncio.run(bot.job_levels(ctx))  # même niveau : pas de 2e alerte
    assert len(sent) == 1 and "Put wall" in sent[0] and "par le haut" in sent[0] and "20,300.00" in sent[0]


def test_sg_command_stores_levels(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "state", State(tmp_path / "s.json"))
    monkeypatch.setattr(bot.settings, "allowed_chat_ids", [1])
    out = []

    class Chat:
        id = 1

        async def send_message(self, text, **kw):
            out.append(text)

    class FB:
        async def send_message(self, chat_id, text, **kw):
            out.append(text)

    fb = FB()
    upd = types.SimpleNamespace(effective_chat=Chat(), get_bot=lambda: fb)
    asyncio.run(bot.cmd_sg(upd, types.SimpleNamespace(args=["nq", "call", "25000", "put", "24000"])))
    assert bot._manual_entries("NQ") == {"Call wall": 25000.0, "Put wall": 24000.0}
    asyncio.run(bot.cmd_sg(upd, types.SimpleNamespace(args=["nq", "effacer"])))
    assert bot._manual_entries("NQ") == {}
