import asyncio
import types
from datetime import datetime, timedelta, timezone

from signalbot import ai, bot
from signalbot.news import EconEvent
from signalbot.state import State


def _setup(monkeypatch, tmp_path, events):
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(bot.settings, "allowed_chat_ids", [1])
    monkeypatch.setattr(bot, "state", State(tmp_path / "s.json"))
    monkeypatch.setattr(bot, "fetch_calendar", lambda *a, **k: events)

    async def ctx_prompt(ask):
        return f"CONTEXTE\n{ask}", []

    monkeypatch.setattr(bot, "_context_prompt", ctx_prompt)
    monkeypatch.setattr(bot.data, "last_change", lambda t, m: (100.0, -0.8))
    sent = []

    class FB:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    return sent, types.SimpleNamespace(bot=FB())


def test_preview_before_and_report_after_release(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    cpi = [EconEvent("CPI m/m", "USD", now + timedelta(minutes=20), "High", "0.2%", "0.3%"),
           EconEvent("Core CPI m/m", "USD", now + timedelta(minutes=20), "High", "0.3%", "0.3%")]
    sent, ctx = _setup(monkeypatch, tmp_path, cpi)
    asked = []

    async def fake_ask(prompt, web_search=None, *, system, effort=None, json_schema=None):
        asked.append((prompt, web_search))
        return "• Plus chaud : taux en hausse, Nasdaq et or sous pression."

    monkeypatch.setattr(ai, "ask", fake_ask)
    asyncio.run(bot.job_calendar(ctx))
    asyncio.run(bot.job_calendar(ctx))  # pas de doublon
    assert len(sent) == 1 and ("Dans 19 min" in sent[0] or "Dans 20 min" in sent[0])
    assert "CPI m/m + Core CPI m/m" in sent[0] and "Les scénarios" in sent[0]
    assert asked[0][1] is False and "Dans" in asked[0][0]  # aperçu : pas de recherche web

    # 4 minutes après la sortie
    for ev in cpi:
        ev.time = now - timedelta(minutes=4)
    asyncio.run(bot.job_calendar(ctx))
    asyncio.run(bot.job_calendar(ctx))
    assert len(sent) == 2 and "Vient de sortir" in sent[1] and "-0.80 %" in sent[1]
    assert asked[-1][1] is True and "chiffre réel" in asked[-1][0]  # recherche du vrai chiffre


def test_preview_without_ai_still_warns(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    sent, ctx = _setup(monkeypatch, tmp_path, [EconEvent("Non-Farm Payrolls", "USD", now + timedelta(minutes=10),
                                                         "High", "150K", "120K")])
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    asyncio.run(bot.job_calendar(ctx))
    assert len(sent) == 1 and "Non-Farm Payrolls" in sent[0] and "volatilité" in sent[0]


def test_trade_review_from_button(monkeypatch, tmp_path):
    sent, _ = _setup(monkeypatch, tmp_path, [])

    async def fake_ask(prompt, web_search=None, *, system, effort=None, json_schema=None):
        assert "acheter l'or vers 4200" in prompt and "coach" in system
        return "⚠️ Moyenne : tu achètes sous le put wall. Mieux : attendre 4210."

    monkeypatch.setattr(ai, "ask", fake_ask)

    class Chat:
        id = 1

        async def send_message(self, text, **kw):
            sent.append(text)

    class FB:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    fb = FB()
    ctx = types.SimpleNamespace(args=None, user_data={})

    def upd(text):
        return types.SimpleNamespace(effective_chat=Chat(), get_bot=lambda: fb,
                                     message=types.SimpleNamespace(text=text))

    asyncio.run(bot.on_text(upd(bot.BTN_TRADE), ctx))
    assert ctx.user_data["await"] == "trade" and "Décris ton idée" in sent[-1]
    asyncio.run(bot.on_text(upd("acheter l'or vers 4200 stop 4185"), ctx))
    assert "Ton trade" in sent[-1] and "Mieux : attendre 4210" in sent[-1]
