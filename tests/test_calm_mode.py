import asyncio
import types
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from signalbot import ai, bot, news_explain
from signalbot.news import NewsItem, score_text
from signalbot.state import State

TZ = ZoneInfo("America/Toronto")


def _item(title, uid):
    sc, m, tg = score_text(title)
    return NewsItem(uid, title, f"https://x.com/{uid}", "CNBC", datetime.now(timezone.utc), "", sc * 2, m, tg)


def _ctx(sent):
    class FB:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)
    return types.SimpleNamespace(bot=FB())


def test_quiet_hours_cross_midnight(monkeypatch):
    monkeypatch.setattr(bot.settings, "quiet_hours", "22:00-07:00")
    assert bot.in_quiet_hours(datetime(2026, 10, 6, 23, 30, tzinfo=TZ))
    assert bot.in_quiet_hours(datetime(2026, 10, 6, 6, 59, tzinfo=TZ))
    assert not bot.in_quiet_hours(datetime(2026, 10, 6, 7, 0, tzinfo=TZ))
    assert not bot.in_quiet_hours(datetime(2026, 10, 6, 14, 0, tzinfo=TZ))
    monkeypatch.setattr(bot.settings, "quiet_hours", "")
    assert not bot.in_quiet_hours(datetime(2026, 10, 6, 23, 30, tzinfo=TZ))


def test_quiet_hours_only_urgent_passes(monkeypatch):
    monkeypatch.setattr(bot.settings, "allowed_chat_ids", [1])
    monkeypatch.setattr(bot, "in_quiet_hours", lambda now=None: True)
    sent = []
    asyncio.run(bot.broadcast(_ctx(sent), "news"))
    asyncio.run(bot.broadcast(_ctx(sent), "gros mouvement", urgent=True))
    assert [s.strip() for s in sent] == ["gros mouvement"]


def test_news_mode_falls_back_without_key(monkeypatch):
    monkeypatch.setattr(bot.settings, "news_mode", "ia")
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    assert bot._news_mode() == "mouvement"
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "sk-test")
    assert bot._news_mode() == "ia"
    monkeypatch.setattr(bot.settings, "news_mode", "n'importe quoi")
    assert bot._news_mode() == "ia"


def test_ai_filter_sends_only_important_and_respects_daily_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(bot.settings, "news_mode", "ia")
    monkeypatch.setattr(bot.settings, "news_max_per_day", 1)
    monkeypatch.setattr(bot.settings, "allowed_chat_ids", [1])
    monkeypatch.setattr(bot, "state", State(tmp_path / "s.json"))
    monkeypatch.setattr(bot, "_market_inputs", lambda: [])  # pas de réseau dans les tests
    bot.state.data["seen"] = {"news:old": 1}
    calls = []

    async def fake(prompt, schema, *, system, effort="low"):
        calls.append(prompt)
        important = "CPI" in prompt
        return {"important": important, "headline": "Inflation plus chaude que prévu" if important else "",
                "take": "Les baisses de taux s'éloignent : pression sur le Nasdaq et l'or." if important else "",
                "rates": "hausse" if important else "aucun effet", "rates_why": "Inflation forte.",
                "nasdaq": "baissier", "gold": "baissier", "key_ids": ["c1"] if important else []}

    monkeypatch.setattr(ai, "ask_json", fake)
    sent = []
    news = {"batch": [_item("Apple analyst raises price target on iPhone demand", "a1")]}
    monkeypatch.setattr(bot, "fetch_news", lambda h: news["batch"])

    asyncio.run(bot.job_news(_ctx(sent)))
    assert not sent  # pas important : silence

    news["batch"] = [_item("Fed watch: CPI inflation jumps more than expected", "c1")]
    asyncio.run(bot.job_news(_ctx(sent)))
    assert len(sent) == 1 and "Inflation plus chaude" in sent[0] and "taux plus hauts" in sent[0]
    assert "Déjà envoyé" in calls[-1]

    news["batch"] = [_item("Fed Powell: CPI inflation forces rate hike talk", "c2")]
    asyncio.run(bot.job_news(_ctx(sent)))
    assert len(sent) == 1  # limite de 1 par jour atteinte : Claude n'est même pas appelé
    assert len(calls) == 2


def test_market_take_without_key_is_none(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    assert asyncio.run(news_explain.market_take([_item("CPI jumps", "x")], [])) is None
