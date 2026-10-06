"""Explication des news en français et résumé global du marché.

- explain() : pour chaque news, c'est quoi, l'impact, la réaction probable, l'effet sur les taux
  d'intérêt et la direction probable pour le Nasdaq / l'or (un seul appel Claude pour le lot).
- digest() : résumé de TOUTES les news récentes : le marché penche-t-il vers la hausse, la baisse
  ou la stabilité, et vers où vont les taux.
Sans clé Claude : explications génériques basées sur le thème des news.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import ai
from .news import NewsItem

log = logging.getLogger(__name__)

DIRECTIONS = ("haussier", "baissier", "neutre", "incertain")
RATES = ("baisse", "hausse", "aucun effet", "incertain")  # effet sur les taux d'intérêt attendus
BIASES = ("hausse", "baisse", "stabilité", "incertain")
RATES_OUTLOOK = ("baisses de taux", "hausses de taux", "statu quo", "incertain")
CONFIDENCE = ("faible", "moyenne", "forte")


@dataclass
class NewsExplanation:
    title_fr: str
    what: str  # c'est quoi la news
    impact: str  # pourquoi / comment ça peut faire bouger le marché
    nasdaq: str  # haussier / baissier / neutre / incertain
    gold: str
    importance: int  # 1 à 5
    reaction: str = ""  # réaction probable du marché
    rates: str = "incertain"  # baisse / hausse / aucun effet / incertain (taux attendus)
    rates_why: str = ""
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
- reaction : 1 phrase concrète sur la réaction probable du marché (ex. « le Nasdaq pourrait reculer à l'ouverture, l'or profiter de la nervosité »).
- rates : effet de la news sur les taux d'intérêt attendus (ce que le marché anticipe de la Fed) : « baisse » (rapproche des baisses de taux), « hausse » (taux plus hauts plus longtemps), « aucun effet » ou « incertain ». rates_why : 1 phrase qui explique pourquoi.
- nasdaq / gold : direction probable à court terme pour le Nasdaq 100 et pour l'or.
- importance : 1 (anecdotique) à 5 (peut faire bouger fort le marché aujourd'hui). Sois exigeant : 4-5 seulement pour ce qui peut vraiment faire bouger le Nasdaq ou l'or (Fed, inflation, emploi, géopolitique majeure, résultats d'un géant tech, tarifs...).
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
                    "reaction": {"type": "string"},
                    "rates": {"type": "string", "enum": list(RATES)},
                    "rates_why": {"type": "string"},
                },
                "required": ["id", "title_fr", "what", "impact", "nasdaq", "gold", "importance",
                             "reaction", "rates", "rates_why"],
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


# Lien générique entre un thème et les taux d'intérêt (sans clé Claude)
RATES_HINT = {
    "Fed": "C'est la Fed qui fixe les taux : tout dépend du ton (dur = taux hauts, doux = baisses).",
    "Taux": "Nouvelle directement liée aux taux d'intérêt.",
    "Inflation": "Inflation plus haute que prévu = baisses de taux qui s'éloignent ; plus basse = "
                 "baisses de taux qui se rapprochent.",
    "Emploi": "Emploi fort = la Fed peut garder les taux hauts ; emploi faible = baisses de taux "
              "plus probables.",
    "Croissance": "Une économie qui ralentit pousse la Fed vers des baisses de taux.",
    "Taux obligataires": "Les taux obligataires reflètent ce que le marché attend de la Fed.",
}


def _fallback_importance(item: NewsItem) -> int:
    # Sans IA, on est prudent : il faut plusieurs mots-clés forts pour parler de fort impact
    return min(5, max(1, item.score // 4))


def fallback(item: NewsItem) -> NewsExplanation:
    themes = [t for t in dict.fromkeys(item.tags) if t in THEMES]
    importance = _fallback_importance(item)
    rates_why = next((RATES_HINT[t] for t in themes if t in RATES_HINT), "")
    rates = "incertain" if rates_why else "aucun effet"
    if not themes:
        return NewsExplanation(item.title, "Nouvelle de marché.", "Impact difficile à évaluer.",
                               "incertain", "incertain", importance, rates=rates, ai=False)
    main = THEMES[themes[0]]
    impact = " ".join(THEMES[t][1] for t in themes[:2])
    return NewsExplanation(item.title, main[0], impact, main[2], main[3], importance,
                           rates=rates, rates_why=rates_why, ai=False)


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
                importance=max(1, min(5, int(entry["importance"]))),
                reaction=entry["reaction"], rates=entry["rates"], rates_why=entry["rates_why"])
    return result


# ------------------------------------------------------------------ résumé global

@dataclass
class Digest:
    nasdaq: str  # hausse / baisse / stabilité / incertain
    gold: str
    rates: str  # baisses de taux / hausses de taux / statu quo / incertain
    rates_why: str
    confidence: str  # faible / moyenne / forte
    summary: str
    themes: list[str]
    watch: list[str]
    key_ids: list[str]  # news les plus importantes
    count: int = 0
    ai: bool = True


DIGEST_SYSTEM = """Tu es un stratège de marché qui fait le point pour un trader francophone (Québec) qui trade le Nasdaq 100 et l'or (XAUUSD).

À partir de TOUTES les news fournies, donne la vue d'ensemble :
- nasdaq / gold : vers où le flux de nouvelles fait pencher le marché à court terme (« hausse », « baisse », « stabilité » si les forces s'équilibrent, « incertain » si on ne peut pas conclure).
- rates : ce que les news impliquent pour les taux d'intérêt de la Fed (« baisses de taux », « hausses de taux », « statu quo », « incertain ») ; rates_why : 1-2 phrases qui expliquent.
- confidence : « faible », « moyenne » ou « forte ». Sois honnête : des news contradictoires ou peu nombreuses = confiance faible.
- summary : 3-4 phrases simples qui racontent ce qui se passe et pourquoi le marché penche de ce côté.
- themes : 2 à 4 grands thèmes du moment, très courts.
- watch : 2 à 3 choses concrètes à surveiller (annonces, niveaux de taux, réaction du dollar...).
- key_ids : les id des 3 news les plus importantes au maximum.
Ignore les news anecdotiques. Ne recopie pas l'anglais. Pas de conseil financier personnalisé."""

DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "nasdaq": {"type": "string", "enum": list(BIASES)},
        "gold": {"type": "string", "enum": list(BIASES)},
        "rates": {"type": "string", "enum": list(RATES_OUTLOOK)},
        "rates_why": {"type": "string"},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        "summary": {"type": "string"},
        "themes": {"type": "array", "items": {"type": "string"}},
        "watch": {"type": "array", "items": {"type": "string"}},
        "key_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["nasdaq", "gold", "rates", "rates_why", "confidence", "summary", "themes",
                 "watch", "key_ids"],
    "additionalProperties": False,
}

_BIAS_FROM_DIRECTION = {"haussier": 1, "baissier": -1}


def _fallback_digest(items: list[NewsItem]) -> Digest:
    """Sans IA : on additionne les directions génériques des thèmes, pondérées par le score."""
    votes = {"nasdaq": 0.0, "gold": 0.0}
    themes: dict[str, int] = {}
    for n in items:
        x = fallback(n)
        votes["nasdaq"] += _BIAS_FROM_DIRECTION.get(x.nasdaq, 0) * n.score
        votes["gold"] += _BIAS_FROM_DIRECTION.get(x.gold, 0) * n.score
        for t in dict.fromkeys(n.tags):
            if t in THEMES:
                themes[t] = themes.get(t, 0) + 1
    total = sum(n.score for n in items) or 1

    def bias(v: float) -> str:
        if abs(v) / total < 0.15:
            return "incertain"
        return "hausse" if v > 0 else "baisse"

    top_themes = sorted(themes, key=themes.get, reverse=True)[:4]
    return Digest(
        nasdaq=bias(votes["nasdaq"]), gold=bias(votes["gold"]), rates="incertain",
        rates_why="Sans clé Claude, le bot ne peut pas lire le sens des annonces (plus dur ou plus "
                  "doux que prévu) pour en déduire l'effet sur les taux.",
        confidence="faible",
        summary=(f"{len(items)} news importantes. Thèmes dominants : "
                 f"{', '.join(top_themes) or 'aucun thème clair'}."),
        themes=top_themes, watch=[], key_ids=[n.id for n in items[:3]], count=len(items), ai=False)


async def digest(items: list[NewsItem]) -> Digest | None:
    """Vue d'ensemble de toutes les news récentes (None s'il n'y a aucune news)."""
    if not items:
        return None
    result = _fallback_digest(items)
    if not ai.enabled():
        return result
    listing = "\n\n".join(
        f"id: {n.id}\nheure: {n.published:%Y-%m-%d %H:%M} UTC\nsource: {n.source}\n"
        f"titre: {n.title}\nrésumé: {n.summary or '-'}" for n in items)
    data = await ai.ask_json(f"Voici les {len(items)} news récentes :\n\n{listing}",
                             DIGEST_SCHEMA, system=DIGEST_SYSTEM, effort="medium")
    if not data:
        return result
    ids = {n.id for n in items}
    return Digest(
        nasdaq=data["nasdaq"], gold=data["gold"], rates=data["rates"], rates_why=data["rates_why"],
        confidence=data["confidence"], summary=data["summary"], themes=data["themes"][:4],
        watch=data["watch"][:3], key_ids=[i for i in data["key_ids"] if i in ids][:3],
        count=len(items))


# ------------------------------------------------------------------ filtre IA : « ce que j'en pense »

@dataclass
class MarketTake:
    important: bool
    headline: str
    take: str  # l'avis de l'IA, dans ses mots
    rates: str  # baisse / hausse / aucun effet / incertain
    rates_why: str
    nasdaq: str
    gold: str
    key_ids: list[str]


TAKE_SYSTEM = """Tu es le filtre de nouvelles personnel d'un trader francophone (Québec) qui trade \
UNIQUEMENT le Nasdaq 100 et l'or (XAUUSD). Il ne veut PAS lire toutes les news : tu les lis pour \
lui et tu le déranges seulement quand ça compte vraiment.

important = true SEULEMENT si au moins une news peut vraiment faire bouger le Nasdaq ou l'or \
aujourd'hui ou changer la tendance, par exemple :
- ce qui change les attentes sur les TAUX D'INTÉRÊT : surprise sur l'inflation (CPI, PCE, PPI), \
l'emploi (NFP, chômage), le PIB, décision ou discours de la Fed qui change le ton, mouvement \
brusque des taux obligataires ou du dollar ;
- escalade ou désescalade géopolitique majeure, choc pétrolier, tarifs douaniers importants ;
- résultats ou nouvelle majeure d'un géant tech qui pèse sur le Nasdaq (Nvidia, Apple, Microsoft...) ;
- nouvelle majeure sur l'or (achats massifs des banques centrales, record, crise).
La grande majorité du temps, la réponse est important = false. Les commentaires d'analystes, les \
petites nouvelles d'entreprises, les rappels de choses déjà connues : false.
Ne répète pas une histoire déjà envoyée aujourd'hui (liste fournie), sauf s'il y a un nouveau \
développement important.

Si important :
- headline : une phrase courte qui résume.
- take : 3 à 5 phrases, dans TES mots, comme un ami trader qui explique : ce qui se passe, ce que \
ça change pour les taux d'intérêt, ce que tu en penses pour le Nasdaq et pour l'or, et quoi \
surveiller. Concret et honnête (dis-le si l'effet est incertain). Pas de conseil financier.
- rates (+ rates_why en 1 phrase), nasdaq, gold : l'effet probable.
- key_ids : les id des 1 à 3 news à l'origine.
Si pas important : remplis les champs texte avec des chaînes vides et key_ids vide."""

TAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "important": {"type": "boolean"},
        "headline": {"type": "string"},
        "take": {"type": "string"},
        "rates": {"type": "string", "enum": list(RATES)},
        "rates_why": {"type": "string"},
        "nasdaq": {"type": "string", "enum": list(DIRECTIONS)},
        "gold": {"type": "string", "enum": list(DIRECTIONS)},
        "key_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["important", "headline", "take", "rates", "rates_why", "nasdaq", "gold", "key_ids"],
    "additionalProperties": False,
}


async def market_take(items: list[NewsItem], already_sent: list[str]) -> MarketTake | None:
    """Claude lit les nouvelles news et dit s'il y a quelque chose d'important (None sans IA)."""
    if not items or not ai.enabled():
        return None
    listing = "\n\n".join(
        f"id: {n.id}\nheure: {n.published:%H:%M} UTC\nsource: {n.source}\ntitre: {n.title}\n"
        f"résumé: {n.summary or '-'}" for n in items)
    sent = "\n".join(f"- {h}" for h in already_sent) or "- rien encore"
    data = await ai.ask_json(
        f"Déjà envoyé aujourd'hui :\n{sent}\n\nNouvelles news ({len(items)}) :\n\n{listing}",
        TAKE_SCHEMA, system=TAKE_SYSTEM, effort="low")
    if not data:
        return None
    ids = {n.id for n in items}
    return MarketTake(bool(data["important"]), data["headline"], data["take"], data["rates"],
                      data["rates_why"], data["nasdaq"], data["gold"],
                      [i for i in data["key_ids"] if i in ids][:3])
