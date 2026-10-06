import asyncio
import types

from signalbot import ai, bot, config


def _upd(sent):
    class Msg:
        async def delete(self):
            sent.append("<supprimé>")

    class Chat:
        id = 1

        async def send_message(self, text, **kw):
            sent.append(text)

    class FB:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    fb = FB()
    return types.SimpleNamespace(effective_chat=Chat(), get_bot=lambda: fb, message=Msg())


def test_save_env_replaces_and_appends(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# commentaire\nANTHROPIC_API_KEY=\nCLAUDE_MODEL=claude-opus-5-5\n")
    config.save_env("ANTHROPIC_API_KEY", "sk-ant-123", env)
    config.save_env("MYFXBOOK_EMAIL", "a@b.c", env)
    text = env.read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-123\n" in text and text.count("ANTHROPIC_API_KEY") == 1
    assert text.endswith("MYFXBOOK_EMAIL=a@b.c\n") and text.startswith("# commentaire")


def test_old_opus_line_becomes_sonnet(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-5-5")
    assert config.Settings().claude_model == "claude-sonnet-5-5"
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-4-5")
    assert config.Settings().claude_model == "claude-haiku-4-5"


def test_cle_command_saves_tests_and_erases(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    monkeypatch.setattr(bot, "save_env", lambda k, v: config.save_env(k, v, env))
    monkeypatch.setattr(bot.settings, "allowed_chat_ids", [1])
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")

    async def fake_ask(*a, **k):
        return "OK"

    monkeypatch.setattr(ai, "ask", fake_ask)
    sent = []
    asyncio.run(bot.cmd_cle(_upd(sent), types.SimpleNamespace(args=["pas-une-cle"])))
    assert "Envoie ta clé" in sent[-1] and not ai.enabled()
    asyncio.run(bot.cmd_cle(_upd(sent), types.SimpleNamespace(args=["sk-ant-abc"])))
    assert "<supprimé>" in sent and "testée" in sent[-1]
    assert ai.enabled() and "ANTHROPIC_API_KEY=sk-ant-abc" in env.read_text()
    assert "✅ Clé Claude" in bot._status_text()


def test_myfxbook_command(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    monkeypatch.setattr(bot, "save_env", lambda k, v: config.save_env(k, v, env))
    monkeypatch.setattr(bot.settings, "allowed_chat_ids", [1])
    monkeypatch.setattr(bot.settings, "myfxbook_email", "")
    monkeypatch.setattr(bot.settings, "myfxbook_password", "")
    sent = []
    asyncio.run(bot.cmd_myfxbook(_upd(sent), types.SimpleNamespace(args=["moi@mail.com", "mot", "de", "passe"])))
    assert "MYFXBOOK_PASSWORD=mot de passe" in env.read_text() and bot.myfxbook_enabled()
