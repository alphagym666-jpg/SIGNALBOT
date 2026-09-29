"""Explication des news en français : c'est quoi, l'impact, et le sens probable pour NQ / or.

Avec une clé Claude : traduction + résumé + impact rédigés pour chaque news (un seul appel
pour tout le lot). Sans clé : explication générique basée sur le thème de la news.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import ai
from .news import NewsItem

log = logging.getLogger(__name__)

DIRECTIONS = ("haussier", "baissier", "neutre", "incertain")


@dataclass
class NewsExplanation:
    title_fr: str
    what: str  # c'est quoi la news
    impact: str  # pourquoi / comment ça peut faire bouger le marché
    nasdaq: str  # haussier / baissier / neutre / incertain
    gold: str
    importance: int  # 1 à 5
    ai: bool = True


SYSTEM = """Tu es un analyste de marché qui explique les nouvelles financières à un trader \
francophone (Québec) qui trade le Nasdaq 100 et l'or (XAUUSD), et qui investit à long terme.

Pour chaque news reçue :
- title_fr : le titre traduit en bon français, court et clair.
- what : 2-3 phrases qui expliquent c'est quoi la nouvelle, en langage simple (explique un \
terme technique s'il y en a un, ex. « CPI = indice des prix à la consommation, la mesure de \
l'inflation »).
- impact : 2-3 phrases sur l'impact possible sur le marché et POURQUOI (la chaîne de cause à \
effet : taux, dollar, sentiment de risque, profits des compagnies...). Sois honnête si \
l'impact est faible ou déjà connu du marché.
- nasdaq / gold : direction probable à court terme pour le Nasdaq 100 et pour l'or.
- importance : 1 (anecdotique) à 5 (peut faire bouger fort le marché aujourd'hui).
Ne recopie pas l'anglais. Pas de conseil financier personnalisé."""

SCHEMA = {
    "type": "object",
    "properties": {
        "news": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title_fr": {"type": "string"},
                    "what": {"type": "string"},
                    "impact": {"type": "string"},
                    "nasdaq": {"type": "string", "enum": list(DIRECTIONS)},
                    "gold": {"type": "string", "enum": list(DIRECTIONS)},
                    "importance": {"type": "integer"},
                },
                "required": ["id", "title_fr", "what", "impact", "nasdaq", "gold", "importance"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["news"],
    "additionalProperties": False,
}

# Explications génériques par thème (utilisées sans clé Claude)
THEMES: dict[str, tuple[str, str, str, str]] = {
    # thème: (c'est quoi, impact, nasdaq, or)
    "Fed": ("Nouvelle sur la Réserve fédérale américaine (Fed), qui décide des taux d'intérêt.",
            "Un ton plus dur (taux hauts plus longtemps) pèse souvent sur le Nasdaq et l'or ; "
            "un ton plus doux (baisses de taux) les aide tous les deux.", "incertain", "incertain"),
    "Taux": ("Nouvelle sur les taux d'intérêt.",
             "Taux plus hauts = argent plus cher : mauvais pour les actions tech (Nasdaq) et pour "
             "l'or, qui ne rapporte pas d'intérêt. Taux plus bas = l'inverse.", "incertain",
             "incertain"),
    "Inflation": ("Nouvelle sur l'inflation (hausse des prix : CPI, PCE, PPI).",
                  "Inflation plus haute que prévu → la Fed garde les taux hauts → Nasdaq ↓, "
                  "dollar ↑. Plus basse que prévu → baisses de taux possibles → Nasdaq ↑, or ↑.",
                  "incertain", "incertain"),
    "Emploi": ("Nouvelle sur le marché de l'emploi américain.",
               "Emploi très fort → moins de baisses de taux → or ↓ et Nasdaq mitigé. Emploi "
               "faible → baisses de taux probables → or ↑, mais peur de récession.",
               "incertain", "incertain"),
    "Croissance": ("Nouvelle sur la croissance économique (PIB, récession).",
                   "Des signes de récession font baisser les actions et poussent les "
                   "investisseurs vers l'or, valeur refuge.", "baissier", "haussier"),
    "Commerce/Tarifs": ("Nouvelle sur les tarifs douaniers, le commerce ou des sanctions.",
                        "Plus d'incertitude pour les compagnies (coûts, chaînes "
                        "d'approvisionnement) : souvent négatif pour le Nasdaq, positif pour l'or.",
                        "baissier", "haussier"),
    "Géopolitique": ("Nouvelle géopolitique (conflit, tensions, cessez-le-feu).",
                     "Les tensions poussent vers la sécurité : or ↑, actions ↓. Une détente "
                     "fait souvent l'inverse.", "incertain", "incertain"),
    "Taux obligataires": ("Nouvelle sur les taux des obligations américaines.",
                          "Taux obligataires qui montent = pression sur le Nasdaq (valorisations "
                          "tech) et sur l'or.", "incertain", "incertain"),
    "Dollar": ("Nouvelle sur le dollar américain.",
               "Un dollar plus fort rend l'or plus cher pour le reste du monde : pression sur "
               "l'or.", "neutre", "incertain"),
    "Or": ("Nouvelle qui concerne directement l'or.",
           "Impact direct possible sur le prix de l'or.", "neutre", "incertain"),
    "Risque": ("Nouvelle sur un risque financier (faillite, défaut, fermeture du gouvernement).",
               "Les risques systémiques font baisser les actions et monter l'or.",
               "baissier", "haussier"),
    "Chine": ("Nouvelle sur la Chine ou Taïwan.",
              "Clé pour les semi-conducteurs : le Nasdaq y est sensible. Des tensions poussent "
              "l'or à la hausse.", "incertain", "incertain"),
    "Mega caps": ("Nouvelle sur un géant de la tech (Nvidia, Apple, Microsoft...).",
                  "Ces compagnies pèsent très lourd dans le Nasdaq 100 : leur nouvelle peut "
                  "faire bouger tout l'indice.", "incertain", "neutre"),
    "Résultats": ("Nouvelle sur des résultats d'entreprise.",
                  "Une grosse surprise (bonne ou mauvaise) peut entraîner tout le secteur.",
                  "incertain", "neutre"),
    "Marché actions": ("Nouvelle sur le sentiment général à la bourse.",
                       "Donne le ton pour la séance.", "incertain", "neutre"),
}


def fallback(item: NewsItem) -> NewsExplanation:
    themes = [t for t in dict.fromkeys(item.tags) if t in THEMES]
    if not themes:
        return NewsExplanation(item.title, "Nouvelle de marché.", "Impact difficile à évaluer.",
                               "incertain", "incertain", min(5, max(1, item.score // 3)), ai=False)
    main = THEMES[themes[0]]
    impact = " ".join(THEMES[t][1] for t in themes[:2])
    return NewsExplanation(item.title, main[0], impact, main[2], main[3],
                           min(5, max(1, item.score // 3)), ai=False)


async def explain(items: list[NewsItem]) -> dict[str, NewsExplanation]:
    """Retourne une explication par id de news (toujours une entrée pour chaque news)."""
    result = {n.id: fallback(n) for n in items}
    if not items or not ai.enabled():
        return result
    listing = "\n\n".join(
        f"id: {n.id}\nsource: {n.source}\ntitre: {n.title}\nrésumé: {n.summary or '-'}"
        for n in items)
    data = await ai.ask_json(f"Explique ces {len(items)} news :\n\n{listing}", SCHEMA,
                             system=SYSTEM)
    for entry in (data or {}).get("news", []):
        if entry.get("id") in result:
            result[entry["id"]] = NewsExplanation(
                title_fr=entry["title_fr"], what=entry["what"], impact=entry["impact"],
                nasdaq=entry["nasdaq"], gold=entry["gold"],
                importance=max(1, min(5, int(entry["importance"]))))
    return result
