"""Explications en langage simple via Claude (optionnel : nécessite ANTHROPIC_API_KEY).

Sans clé, le bot fonctionne quand même : il utilise une explication basée sur des règles
(news récentes + contexte inter-marchés + annonces économiques).
"""

from __future__ import annotations

import logging

import anthropic

from .config import settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es un analyste de marché qui explique ce qui se passe à un trader \
francophone (Québec) qui trade surtout le Nasdaq 100 (NQ) et l'or (XAUUSD), et qui investit \
à moyen/long terme dans un CELI.

Règles :
- Réponds en français simple et direct, sans jargon inutile (explique un terme technique \
s'il est nécessaire).
- Sois concret : la cause la plus probable d'abord, puis les causes secondaires.
- Distingue clairement ce qui est confirmé par les données/news fournies (ou trouvées via \
la recherche web) de ce qui est une hypothèse.
- Si aucune cause claire n'apparaît, dis-le honnêtement (flux d'ordres, prises de profits, \
cassure technique, liquidité faible...) au lieu d'inventer.
- Termine par « À surveiller » : 2-3 points concrets pour la suite (niveaux, annonces, \
réaction du dollar/des taux).
- Format Telegram : texte brut, puces avec «•», 1200 caractères maximum. Pas de markdown, \
pas de titres avec #.
- Ce n'est pas un conseil financier ; ne dis jamais à la personne combien acheter."""

_client: anthropic.AsyncAnthropic | None = None


def enabled() -> bool:
    return bool(settings.anthropic_api_key)


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=180.0)
    return _client


async def ask(prompt: str, web_search: bool | None = None) -> str | None:
    """Pose une question à Claude. Retourne None si l'IA est désactivée ou en erreur."""
    if not enabled():
        return None
    use_search = settings.claude_web_search if web_search is None else web_search
    tools = ([{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
             if use_search else [])
    messages: list[dict] = [{"role": "user", "content": prompt}]
    client = _get_client()

    try:
        # La recherche web côté serveur peut mettre le tour en pause (pause_turn) :
        # on renvoie la réponse telle quelle pour que Claude continue.
        for _ in range(4):
            response = await client.beta.messages.create(
                model=settings.claude_model,
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=tools,
                output_config={"effort": settings.claude_effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "pause_turn":
                messages = [messages[0], {"role": "assistant", "content": response.content}]
                continue
            break
    except anthropic.RateLimitError:
        log.warning("Claude : limite de requêtes atteinte")
        return None
    except anthropic.APIStatusError as exc:
        log.warning("Claude : erreur API %s: %s", exc.status_code, exc.message)
        return None
    except anthropic.APIConnectionError as exc:
        log.warning("Claude : connexion impossible: %s", exc)
        return None

    if response.stop_reason == "refusal":
        log.warning("Claude a refusé la requête")
        return None
    # On garde le texte écrit après la dernière recherche web (la réponse finale),
    # pas les phrases intermédiaires du genre « je vais chercher... ».
    blocks = list(response.content)
    last_tool = max((i for i, b in enumerate(blocks) if b.type != "text"), default=-1)
    text = "".join(b.text for b in blocks[last_tool + 1:] if b.type == "text").strip()
    if not text:
        text = "".join(b.text for b in blocks if b.type == "text").strip()
    return text or None
