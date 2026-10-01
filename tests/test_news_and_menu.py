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
                          "nasdaq": "haussier", "gold": "haussier", "importance": 9,
                          "reaction": "Le Nasdaq pourrait monter à l'ouverture.",
                          "rates": "baisse", "rates_why": "La Fed devient plus douce."}]}

    monkeypatch.setattr(ai, "ask_json", fake_ask_json)
    expl = asyncio.run(news_explain.explain([item]))["abc"]
    assert expl.ai and expl.title_fr.startswith("Powell")
    assert expl.importance == 5  # borné à 5
    card = fmt.news_card(item, expl, ZoneInfo("America/Toronto"))
    assert "📈 haussier" in card and "🇺🇸" not in card
    assert "Réaction probable" in card and "rapproche des baisses de taux" in card


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
    assert len(labels) == 11 and bot.BTN_RESUME in labels
    assert all(any(ch for ch in label if ord(ch) > 0x2000) for label in labels)  # icône
    names = {c.command for c in bot.BOT_COMMANDS}
    assert {"menu", "marche", "news", "resume", "stocks"} <= names


def test_card_shows_reaction_and_rates(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    item = _item("Hot CPI inflation report pushes Treasury yields higher")
    expl = asyncio.run(news_explain.explain([item]))[item.id]
    assert expl.rates_why  # thème inflation -> lien avec les taux expliqué
    card = fmt.news_card(item, expl, ZoneInfo("America/Toronto"))
    assert "Taux d'intérêt" in card


def test_digest_with_ai(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "sk-test")
    items = [_item("Fed's Powell signals rate cuts ahead", "a"), _item("Gold hits record high", "b")]

    async def fake_ask_json(prompt, schema, *, system, effort="low"):
        assert "a" in prompt and schema is news_explain.DIGEST_SCHEMA
        return {"nasdaq": "hausse", "gold": "hausse", "rates": "baisses de taux",
                "rates_why": "La Fed devient plus douce.", "confidence": "moyenne",
                "summary": "Le marché anticipe des baisses de taux.", "themes": ["Fed"],
                "watch": ["Discours de Powell"], "key_ids": ["a", "inconnu"]}

    monkeypatch.setattr(ai, "ask_json", fake_ask_json)
    d = asyncio.run(news_explain.digest(items))
    assert d.ai and d.rates == "baisses de taux" and d.key_ids == ["a"]
    msg = fmt.digest_message(d, {n.id: n for n in items}, {}, 12)
    assert "penche à la HAUSSE" in msg and "BAISSES de taux" in msg and "Powell" in msg


def test_digest_without_ai_is_cautious(monkeypatch):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    items = [_item("Tariffs escalate trade war with China", "t1"),
             _item("Recession fears grow as GDP shrinks", "t2")]
    d = asyncio.run(news_explain.digest(items))
    assert not d.ai and d.confidence == "faible" and d.nasdaq == "baisse" and d.rates == "incertain"
    assert asyncio.run(news_explain.digest([])) is None


def test_high_impact_filter_and_hourly_budget(monkeypatch, tmp_path):
    from signalbot.state import State
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    monkeypatch.setattr(bot, "state", State(tmp_path / "s.json"))
    strong = _item("Fed Powell rate cut as CPI inflation and jobs report shock, war escalates", "s")
    weak = _item("Gold edges up", "w")
    keep, expl = asyncio.run(bot._high_impact([strong, weak]))
    assert [n.id for n in keep] == ["s"]  # « Gold edges up » : trop faible, même pas analysée
    assert bot._news_budget() == bot.settings.news_max_per_hour
    bot.state.data["news_sent"] = [datetime.now(timezone.utc).timestamp()] * 5
    assert bot._news_budget() == 0
