"""News financières (flux RSS) et calendrier économique (annonces à fort impact)."""

from __future__ import annotations

import calendar as _cal
import hashlib
import json
import logging
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import feedparser

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (SignalBot; +https://github.com/)"

FEEDS = {
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "CNBC Économie": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "CNBC Marchés": "https://www.cnbc.com/id/15839069/device/rss/rss.html",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "MarketWatch Marchés": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
    "FXStreet": "https://www.fxstreet.com/rss/news",
    "Yahoo Finance": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EIXIC,GC%3DF,%5EGSPC&region=US&lang=en-US",
    "Investing.com": "https://www.investing.com/rss/news_25.rss",
}

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# (motif regex, poids, marchés touchés, étiquette en français)
KEYWORDS: list[tuple[str, int, tuple[str, ...], str]] = [
    (r"\b(fed|fomc|federal reserve|powell)\b", 5, ("NQ", "XAU"), "Fed"),
    (r"\brate (cut|hike|decision)s?\b|\binterest rates?\b", 4, ("NQ", "XAU"), "Taux"),
    (r"\b(cpi|inflation|pce|ppi)\b", 5, ("NQ", "XAU"), "Inflation"),
    (r"\b(nonfarm|non-farm|payrolls?|jobs report|unemployment|jobless claims)\b", 5,
     ("NQ", "XAU"), "Emploi"),
    (r"\bgdp\b|\brecession\b", 4, ("NQ", "XAU"), "Croissance"),
    (r"\btariffs?\b|\btrade war\b|\bsanctions?\b", 4, ("NQ", "XAU"), "Commerce/Tarifs"),
    (r"\b(war|missile|strike|attack|invasion|ceasefire|escalat\w+)\b", 4, ("NQ", "XAU"),
     "Géopolitique"),
    (r"\b(treasury|bond) yields?\b|\b10-year\b", 3, ("NQ", "XAU"), "Taux obligataires"),
    (r"\bdollar\b|\bdxy\b", 2, ("XAU",), "Dollar"),
    (r"\bgold\b|\bbullion\b|\bsafe[- ]haven\b", 4, ("XAU",), "Or"),
    (r"\bnasdaq\b|\btech stocks?\b|\bwall street\b|\bs&p 500\b|\bstock market\b", 3, ("NQ",),
     "Marché actions"),
    (r"\b(nvidia|apple|microsoft|amazon|alphabet|google|meta|tesla|broadcom)\b", 2, ("NQ",),
     "Mega caps"),
    (r"\bearnings\b|\bguidance\b", 2, ("NQ",), "Résultats"),
    (r"\b(sell-?off|plunge|plummet|crash|tumble|sink|rout|soar|surge|rall(y|ies))\b", 2,
     ("NQ", "XAU"), "Gros mouvement"),
    (r"\b(shutdown|default|debt ceiling|bank failure|bankrupt)\b", 4, ("NQ", "XAU"), "Risque"),
    (r"\bchina\b|\btaiwan\b", 2, ("NQ", "XAU"), "Chine"),
    (r"\bbreaking\b|\bunexpected(ly)?\b|\bsurprise\b", 2, ("NQ", "XAU"), "Surprise"),
]
_COMPILED = [(re.compile(p, re.I), w, m, t) for p, w, m, t in KEYWORDS]


@dataclass
class NewsItem:
    id: str
    title: str
    link: str
    source: str
    published: datetime
    summary: str = ""
    score: int = 0
    markets: set[str] = field(default_factory=set)
    tags: list[str] = field(default_factory=list)


def score_text(text: str) -> tuple[int, set[str], list[str]]:
    score, markets, tags = 0, set(), []
    for rx, weight, mkts, tag in _COMPILED:
        if rx.search(text):
            score += weight
            markets.update(mkts)
            tags.append(tag)
    return score, markets, tags


def _parse_feed(source: str, url: str) -> list[NewsItem]:
    try:
        feed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as exc:
        log.warning("Flux %s illisible: %s", source, exc)
        return []
    items = []
    for e in feed.entries[:40]:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        link = e.get("link", "")
        ts = e.get("published_parsed") or e.get("updated_parsed")
        published = (datetime.fromtimestamp(_cal.timegm(ts), tz=timezone.utc) if ts
                     else datetime.now(timezone.utc))
        summary = re.sub(r"<[^>]+>", "", e.get("summary", "") or "")[:400]
        uid = hashlib.sha1((link or title).encode()).hexdigest()[:16]
        score, markets, tags = score_text(f"{title} {summary}")
        # Le titre compte double : c'est ce qui fait bouger le marché
        title_score, _, _ = score_text(title)
        items.append(NewsItem(uid, title, link, source, published, summary,
                              score + title_score, markets, tags))
    return items


def fetch_news(max_age_hours: float = 24) -> list[NewsItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    seen_titles: set[str] = set()
    items: list[NewsItem] = []
    for source, url in FEEDS.items():
        for item in _parse_feed(source, url):
            key = re.sub(r"\W+", "", item.title.lower())[:60]
            if item.published < cutoff or key in seen_titles:
                continue
            seen_titles.add(key)
            items.append(item)
    items.sort(key=lambda n: (n.score, n.published), reverse=True)
    return items


def relevant_news(items: list[NewsItem], market: str | None, since: datetime | None = None,
                  min_score: int = 3) -> list[NewsItem]:
    out = [n for n in items if n.score >= min_score and (market is None or market in n.markets)]
    if since:
        out = [n for n in out if n.published >= since]
    return out


# ---------------------------------------------------------------- calendrier économique

@dataclass
class EconEvent:
    title: str
    country: str
    time: datetime
    impact: str
    forecast: str
    previous: str

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.title}{self.time.isoformat()}".encode()).hexdigest()[:16]


def fetch_calendar(countries: tuple[str, ...] = ("USD",),
                   impacts: tuple[str, ...] = ("High",)) -> list[EconEvent]:
    try:
        req = urllib.request.Request(CALENDAR_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode())
    except Exception as exc:
        log.warning("Calendrier économique indisponible: %s", exc)
        return []
    events = []
    for e in raw:
        if e.get("country") not in countries or e.get("impact") not in impacts:
            continue
        try:
            when = datetime.fromisoformat(e["date"])
        except (KeyError, ValueError):
            continue
        events.append(EconEvent(e.get("title", "?"), e["country"], when, e["impact"],
                                e.get("forecast") or "-", e.get("previous") or "-"))
    events.sort(key=lambda ev: ev.time)
    return events


def events_between(events: list[EconEvent], start: datetime, end: datetime) -> list[EconEvent]:
    return [e for e in events if start <= e.time <= end]
