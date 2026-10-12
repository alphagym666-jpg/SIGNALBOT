import asyncio
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

from signalbot import bot, mt5, sentiment as sent
from signalbot.levels import Level
from signalbot.news import EconEvent
from signalbot.positioning import CotReport, GammaLevels, Positioning, RetailSentiment
from signalbot.zones import Zone

from test_zones import _fake_market


def _pos(long_pct=None, cot_change=0, flip=None):
    p = Positioning("XAU")
    if long_pct is not None:
        p.retail = RetailSentiment("XAUUSD", long_pct, 100 - long_pct, None, None)
    if cot_change:
        p.cot = CotReport("XAU", "2026-10-06", 1000, cot_change, 60)
    if flip:
        p.gamma = GammaLevels("XAU", 4000, 4100, 3900, flip, 1.0)
    return p


def test_sentiment_buyers_when_everything_points_up():
    st = sent.compute(4000, [Level("VWAP", 3990, "")], _pos(25, 500, 3950), None, "haussier")
    assert st.score == 100 and st.label == "ACHETEUR" and st.buyers_pct == 100
    assert any("Myfxbook" in r and "75 % des particuliers vendent" in r for r in st.reasons)


def test_sentiment_retail_is_contrarian_and_mixed_is_shared():
    st = sent.compute(4000, [Level("VWAP", 4010, "")], _pos(75), None, None)
    assert st.score < 0 and "vendeur" in st.short()  # foule acheteuse + sous le VWAP
    mixed = sent.compute(4000, [Level("VWAP", 3990, "")], _pos(75), None, None)
    assert mixed.label in ("Partagé", "Plutôt vendeur")
    assert sent.compute(None, [], None).label == "Partagé"


def test_mt5_file_format(tmp_path):
    snap = mt5.MarketSnapshot("NQ", 25000.0, [Zone(25100, 25120, "VENTE", ["Haut d'hier", "Call wall"], 3)],
                              sent.Sentiment(40, ["🟢 tendance jour : haussière", "🔴 Myfxbook ; foule"]), "positif")
    ev = EconEvent("CPI m/m", "USD", datetime(2026, 10, 13, 12, 30, tzinfo=timezone.utc), "High", "", "")
    text = mt5.build_file([snap, mt5.MarketSnapshot("XAU", None, [], sent.Sentiment(0))], [ev], now=1000)
    lines = text.splitlines()
    assert lines[0] == "TIME;1000"
    assert "M;NQ;25000.00;40;ACHETEUR;70;positif" in lines
    assert "Z;NQ;VENTE;25100.00;25120.00;3;Haut d'hier, Call wall" in lines
    assert "S;NQ;tendance jour : haussière;+" in lines and "S;NQ;Myfxbook , foule;-" in lines
    assert f"N;{int(ev.time.timestamp())};CPI m/m" in lines
    assert not any(line.startswith("M;XAU") for line in lines)  # pas de prix : pas de section
    path = mt5.write([snap], [], str(tmp_path))
    assert path.read_text(encoding="utf-8").startswith("TIME;") and not (tmp_path / "SignalBot_zones.txt.tmp").exists()


def test_install_indicator_in_every_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for t in ("ABC123", "DEF456"):
        (tmp_path / "MetaQuotes" / "Terminal" / t / "MQL5" / "Indicators").mkdir(parents=True)
    (tmp_path / "MetaQuotes" / "Terminal" / "Common").mkdir()
    assert len(mt5.install_indicator()) == 2
    assert mt5.install_indicator() == []  # déjà à jour : rien à copier
    assert mt5.files_dir() == tmp_path / "MetaQuotes" / "Terminal" / "Common" / "Files"


def test_mql5_indicator_reads_what_the_bot_writes():
    raw = (Path(__file__).resolve().parent.parent / "mql5" / "SignalBot_Zones.mq5").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 avec BOM : accents et ★ lus par MetaEditor
    src = raw.decode("utf-8")
    for word in ('"SignalBot_zones.txt"', "FILE_COMMON", "CP_UTF8", 'f[0] == "M"', 'f[0] == "Z"',
                 'f[0] == "S"', 'f[0] == "N"', "OBJ_RECTANGLE", "EventSetTimer(30)", "bid / ref"):
        assert word in src
    assert mt5.FILE_NAME in src


def test_bot_writes_mt5_file_and_resends_zones_after_big_move(monkeypatch, tmp_path):
    photos, texts, fb = _fake_market(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.settings, "mt5_files_dir", str(tmp_path / "mt5"))
    path = bot._write_mt5()
    content = path.read_text(encoding="utf-8")
    assert "M;NQ;25000.00;" in content and "Z;NQ;VENTE;" in content and "M;XAU;" in content

    move = types.SimpleNamespace(direction="down", change=-1.4, window="15 min")
    monkeypatch.setattr(bot, "detect_move", lambda inst: move if inst.key == "NQ" else None)

    async def fake_explain(m):
        return "🚨 Gros mouvement"

    monkeypatch.setattr(bot, "explain", fake_explain)
    refreshed = []
    monkeypatch.setattr(bot, "refresh_gamma", refreshed.append)
    monkeypatch.setattr(bot.settings, "zones_after_moves", True)
    asyncio.run(bot.job_moves(types.SimpleNamespace(bot=fb)))
    assert refreshed == ["NQ"] and "Gros mouvement" in texts[-1]
    assert len(photos) == 2 and "après le gros mouvement" in photos[0][1] and "Sentiment" in photos[0][1]
