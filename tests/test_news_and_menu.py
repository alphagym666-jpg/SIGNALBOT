import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from signalbot import ai, bot, formatting as fmt, news_explain
from signalbot.news import NewsItem, score_text


def _item(title, uid="n1"):
    score, markets, tags = score_text(title)
    return NewsItem(uid, title, "https://example.com/a?b=1&c=2", "CNBC",
                    datetime(2026, 9, 29, 14, 30, tzinfo=timezone.utc), "", score, markets, tags)


def test_fallback_explanation_in_french(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    item = _item("Gold jumps as CPI inflation comes in hotter than expected")
    expl = asyncio.run(news_explain.explain([item]))[item.id]
    assert not expl.ai
    assert "inflation" in expl.what.lower()
    card = fmt.news_card(item, expl, ZoneInfo("America/Toronto"))
    assert "C'est quoi" in card and "Impact" in card and "Nasdaq" in card
    assert "🇺🇸" in card  # pas d'IA -> titre anglais signalé
    assert "&amp;c=2" in card  # lien correctement échappé


def test_ai_explanation_used_when_available(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "sk-test")
    item = _item("Fed's Powell signals rate cuts ahead", uid="abc")

    async def fake_ask_json(prompt, schema, *, system, effort="low"):
        assert "abc" in prompt
        return {"news": [{"id": "abc", "title_fr": "Powell annonce des baisses de taux",
                          "what": "Le président de la Fed laisse entendre des baisses.",
                          "impact": "Taux plus bas = bon pour la tech et l'or.",
                          "nasdaq": "haussier", "gold": "haussier", "importance": 9}]}

    monkeypatch.setattr(ai, "ask_json", fake_ask_json)
    expl = asyncio.run(news_explain.explain([item]))["abc"]
    assert expl.ai and expl.title_fr.startswith("Powell")
    assert expl.importance == 5  # borné à 5
    card = fmt.news_card(item, expl, ZoneInfo("America/Toronto"))
    assert "📈 haussier" in card and "🇺🇸" not in card


def test_ai_failure_falls_back(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "sk-test")

    async def broken(*a, **k):
        return None

    monkeypatch.setattr(ai, "ask_json", broken)
    item = _item("Tariffs on China chips escalate trade war")
    expl = asyncio.run(news_explain.explain([item]))[item.id]
    assert not expl.ai and expl.nasdaq == "baissier"


def test_menu_buttons_are_all_handled():
    labels = [b.text for row in bot.MAIN_MENU.keyboard for b in row]
    assert len(labels) == 10
    assert all(any(ch for ch in label if ord(ch) > 0x2000) for label in labels)  # icône
    names = {c.command for c in bot.BOT_COMMANDS}
    assert {"menu", "marche", "news", "stocks"} <= names
