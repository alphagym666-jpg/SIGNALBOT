"""Mise en forme des messages Telegram (HTML)."""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import INSTRUMENTS, Instrument
from .news import EconEvent, NewsItem
from .news_explain import Digest, NewsExplanation
from .positioning import Positioning
from .analysis import Analysis, MarketInput
from .levels import Level
from .regime import Regime, market_view
from .signals import Signal
from .stocks import StockReport

DISCLAIMER = "<i>⚠️ Outil d'aide à la décision, pas un conseil financier. Gère ton risque.</i>"

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]


def e(text: str) -> str:
    return html.escape(str(text), quote=False)


def attr(text: str) -> str:
    return html.escape(str(text), quote=True)


def _short_month(month: int) -> str:
    name = MOIS[month - 1]
    return name if len(name) <= 4 else name[:4] + "."


def fr_date(dt: datetime, pattern: str) -> str:
    """strftime avec les noms de jours/mois en français (%A, %a, %B, %b)."""
    pattern = (pattern.replace("%A", JOURS[dt.weekday()])
               .replace("%a", JOURS[dt.weekday()][:3] + ".")
               .replace("%B", MOIS[dt.month - 1])
               .replace("%b", _short_month(dt.month)))
    return dt.strftime(pattern)


def regime_block(inst: Instrument, regimes: dict[str, Regime]) -> str:
    h1 = regimes.get("H1") or regimes.get("H4") or regimes.get("D1")
    if not h1:
        return f"<b>{e(inst.name)}</b> : données indisponibles."
    lines = [f"📊 <b>{e(inst.name)}</b> — {h1.close:,.2f}"]
    for tf in ("D1", "H4", "H1"):
        r = regimes.get(tf)
        if r:
            lines.append(f"  {r.emoji} <b>{tf}</b> : {e(r.label)} ({e(r.strength)}) · "
                         f"ADX {r.adx:.0f} · RSI {r.rsi:.0f}")
    notes = {n for r in regimes.values() if r for n in r.notes}
    for n in sorted(notes):
        lines.append(f"  • {e(n)}")
    h4 = regimes.get("H4")
    if h4:
        lines.append(f"  Zone H4 récente : {h4.range_low:,.2f} – {h4.range_high:,.2f} "
                     f"(ATR H4 ≈ {h4.atr:,.2f})")
    lines.append(f"👉 {e(market_view(regimes))}")
    return "\n".join(lines)


def signal_message(sig: Signal, inst: Instrument) -> str:
    icon = "🟢" if sig.direction == "ACHAT" else "🔴"
    stars = "★" * max(1, round(sig.score / 20)) + "☆" * (5 - max(1, round(sig.score / 20)))
    lines = [
        f"{icon} <b>SIGNAL {sig.direction} — {e(inst.name)}</b>",
        f"Setup : <b>{e(sig.setup)}</b> · Qualité {sig.score}/100 {stars}",
        "",
        f"Entrée ≈ <b>{sig.entry:,.2f}</b>",
        f"Stop : {sig.stop:,.2f}  (risque {abs(sig.entry - sig.stop):,.2f})",
        f"TP1 : {sig.tp1:,.2f}  ·  TP2 : {sig.tp2:,.2f}  (R/R {sig.risk_reward:.1f})",
        "",
        "<b>Pourquoi :</b>",
        *[f"• {e(r)}" for r in sig.reasons],
    ]
    if sig.warnings:
        lines += ["", "<b>Attention :</b>", *[f"⚠️ {e(w)}" for w in sig.warnings]]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


_DIR_ICON = {"haussier": "📈 haussier", "baissier": "📉 baissier", "neutre": "➖ neutre",
             "incertain": "❔ incertain"}


_RATES_ICON = {"baisse": "📉 rapproche des baisses de taux", "hausse": "📈 taux plus hauts plus longtemps",
               "aucun effet": "➖ pas d'effet", "incertain": "❔ effet incertain"}
_BIAS_ICON = {"hausse": "📈 penche à la HAUSSE", "baisse": "📉 penche à la BAISSE",
              "stabilité": "➖ plutôt STABLE", "incertain": "❔ incertain"}
_OUTLOOK_ICON = {"baisses de taux": "📉 vers des BAISSES de taux", "hausses de taux": "📈 vers des HAUSSES de taux",
                 "statu quo": "➖ statu quo (pas de changement)", "incertain": "❔ incertain"}


BIAS_SHORT = {"hausse": "📈 hausse", "baisse": "📉 baisse", "stabilité": "➖ stable", "incertain": "❔"}
OUTLOOK_SHORT = {"baisses de taux": "📉 baisses", "hausses de taux": "📈 hausses", "statu quo": "➖ statu quo",
                 "incertain": "❔"}


def digest_message(d: Digest, items: dict[str, NewsItem], explanations: dict[str, NewsExplanation],
                   hours: int) -> str:
    """Résumé global : où penche le marché selon toutes les news, et vers où vont les taux."""
    lines = [
        f"🧭 <b>Résumé des news</b> · {d.count} news importantes ({hours} dernières h)",
        "",
        f"📊 <b>Nasdaq :</b> {_BIAS_ICON.get(d.nasdaq, d.nasdaq)}",
        f"🥇 <b>Or :</b> {_BIAS_ICON.get(d.gold, d.gold)}",
        f"🏦 <b>Taux d'intérêt :</b> {_OUTLOOK_ICON.get(d.rates, d.rates)}",
        f"   <i>{e(d.rates_why)}</i>" if d.rates_why else None,
        f"🎚️ Confiance : <b>{e(d.confidence)}</b>",
        "",
        f"📝 <b>En bref :</b> {e(d.summary)}",
    ]
    if d.themes:
        lines += ["", "🔑 <b>Thèmes du moment</b>", *[f"• {e(t)}" for t in d.themes]]
    if d.watch:
        lines += ["", "👀 <b>À surveiller</b>", *[f"• {e(w)}" for w in d.watch]]
    keys = [k for k in d.key_ids if k in items]
    if keys:
        lines += ["", "🔥 <b>News clés</b>"]
        for k in keys:
            title = explanations[k].title_fr if k in explanations else items[k].title
            lines.append(f"• <a href=\"{attr(items[k].link)}\">{e(title)}</a>")
    return "\n".join(line for line in lines if line is not None)


def news_card(n: NewsItem, x: NewsExplanation, tz: ZoneInfo) -> str:
    """Fiche complète d'une news : titre FR, c'est quoi, impact, direction NQ / or."""
    heat = "🔴" if x.importance >= 4 else "🟠" if x.importance == 3 else "🟡"
    lines = [
        f"{heat} <b>{e(x.title_fr)}</b>" + ("" if x.ai else " 🇺🇸"),
        f"<i>{e(n.source)} · {n.published.astimezone(tz):%H:%M} · importance "
        f"{'★' * x.importance}{'☆' * (5 - x.importance)}</i>",
        "",
        f"📝 <b>C'est quoi :</b> {e(x.what)}",
        f"💥 <b>Impact :</b> {e(x.impact)}",
    ]
    if x.reaction:
        lines.append(f"🎯 <b>Réaction probable :</b> {e(x.reaction)}")
    rates = _RATES_ICON.get(x.rates, x.rates)
    lines.append(f"🏦 <b>Taux d'intérêt :</b> {rates}" + (f" — {e(x.rates_why)}" if x.rates_why else ""))
    lines.append(f"📊 Nasdaq : {_DIR_ICON.get(x.nasdaq, x.nasdaq)}   🥇 Or : {_DIR_ICON.get(x.gold, x.gold)}")
    lines.append(f"🔗 <a href=\"{attr(n.link)}\">Lire l'article</a>")
    return "\n".join(lines)


def event_line(ev: EconEvent, tz: ZoneInfo) -> str:
    return (f"• <b>{fr_date(ev.time.astimezone(tz), '%a %d %H:%M')}</b> — {e(ev.title)} "
            f"<i>(prévu {e(ev.forecast)}, préc. {e(ev.previous)})</i>")


def _fmt_pct(v: float | None, scale: float = 1) -> str:
    return f"{v * scale:+.0f}%" if v is not None else "n/d"


def stock_card(r: StockReport, kind: str) -> str:
    if kind == "earnings":
        head = f"📅 <b>{e(r.ticker)}</b> — {e(r.name)} · score {r.earnings_score}/100"
        reasons = r.earnings_reasons
    else:
        head = f"📉 <b>{e(r.ticker)}</b> — {e(r.name)} · score {r.dip_score}/100"
        reasons = r.dip_reasons
    lines = [head, f"   Prix {r.price:,.2f} {e(r.currency)} · 1 mois {r.change_1m_pct:+.1f}% · "
                   f"vs sommet 52s {r.drawdown_pct:.0f}%"]
    lines += [f"   ✅ {e(x)}" for x in reasons]
    lines += [f"   ⚠️ {e(x)}" for x in r.risks[:2]]
    return "\n".join(lines)


def stock_detail(r: StockReport) -> str:
    lines = [
        f"🔎 <b>{e(r.ticker)} — {e(r.name)}</b>",
        f"Prix : {r.price:,.2f} {e(r.currency)}",
        f"Vs sommet 52 semaines : {r.drawdown_pct:.1f}% · 1 mois : {r.change_1m_pct:+.1f}%",
        f"Au-dessus de la MM200 : {'oui' if r.above_200d else 'non' if r.above_200d is not None else 'n/d'}",
        f"RSI hebdo : {r.rsi_weekly:.0f}" if r.rsi_weekly is not None else "RSI hebdo : n/d",
        f"Croissance revenus : {_fmt_pct(r.revenue_growth, 100)} · Marge nette : "
        f"{_fmt_pct(r.profit_margin, 100)}",
        f"P/E prévisionnel : {r.forward_pe:.1f}" if r.forward_pe else "P/E prévisionnel : n/d",
        f"Note analystes : {r.analyst_rating:.1f}/5 (1 = achat fort)" if r.analyst_rating
        else "Note analystes : n/d",
        f"Cible analystes : {_fmt_pct(r.target_upside_pct)}",
        f"Prochains résultats : {fr_date(r.next_earnings, '%d %b %Y')}" if r.next_earnings
        else "Prochains résultats : n/d",
        f"Historique : bat les attentes {r.beat_rate:.0f}% du temps (surprise moy. "
        f"{r.avg_surprise:+.1f}%)" if r.beat_rate is not None else "Historique résultats : n/d",
    ]
    if r.earnings_reasons:
        lines += ["", f"<b>Setup earnings</b> ({r.earnings_score}/100)",
                  *[f"✅ {e(x)}" for x in r.earnings_reasons]]
    if r.dip_reasons:
        lines += ["", f"<b>Setup grosse baisse</b> ({r.dip_score}/100)",
                  *[f"✅ {e(x)}" for x in r.dip_reasons]]
    if r.risks:
        lines += ["", "<b>Risques</b>", *[f"⚠️ {e(x)}" for x in r.risks]]
    if r.ticker.endswith(".TO"):
        lines += ["", "🍁 Titre canadien : aucune retenue d'impôt étrangère sur les dividendes "
                      "dans un CELI."]
    elif r.currency == "USD":
        lines += ["", "ℹ️ Titre US dans un CELI : 15% de retenue sur les dividendes, non "
                      "récupérable (peu important pour un titre de croissance)."]
    return "\n".join(lines)


def now_str(tz: ZoneInfo) -> str:
    return fr_date(datetime.now(tz), "%a %d %b %H:%M")


# ------------------------------------------------------------------ positionnement

def _crowd_reading(long_pct: float) -> str:
    if long_pct >= 65:
        return "la foule est très ACHETEUSE → à contre-courant, prudence sur les achats"
    if long_pct <= 35:
        return "la foule est très VENDEUSE → à contre-courant, prudence sur les ventes"
    return "positionnement équilibré, pas de signal"


def positioning_block(inst: Instrument, p: Positioning, myfxbook_on: bool) -> str:
    lines = [f"🎯 <b>{e(inst.name)}</b>"]
    g = p.gamma
    if g:
        lines.append(f"🧲 <b>Gamma des options :</b> {'🟢 positif' if g.positive else '🔴 négatif'} "
                     f"({g.total_gex:+.2f} G$ par 1 %)")
        lines.append("   → " + ("les teneurs de marché amortissent les mouvements : le prix tend à rester "
                                "entre les murs" if g.positive else
                                "les mouvements sont amplifiés : cassures et chutes plus violentes"))
        if g.call_wall:
            lines.append(f"   🧱 Call wall (résistance) : <b>{g.call_wall:,.2f}</b>")
        if g.put_wall:
            lines.append(f"   🛡️ Put wall (support) : <b>{g.put_wall:,.2f}</b>")
        if g.flip:
            side = "au-dessus → plutôt calme" if g.spot >= g.flip else "en dessous → plutôt nerveux"
            lines.append(f"   ⚖️ Bascule gamma : <b>{g.flip:,.2f}</b> (prix {side})")
    else:
        lines.append("🧲 Gamma des options : indisponible pour le moment")
    if p.retail:
        r = p.retail
        lines.append(f"👥 <b>Particuliers (Myfxbook) :</b> {r.long_pct:.0f} % acheteurs / {r.short_pct:.0f} % "
                     f"vendeurs → {_crowd_reading(r.long_pct)}")
    elif inst.key == "XAU" and not myfxbook_on:
        lines.append("👥 Particuliers : ajoute MYFXBOOK_EMAIL et MYFXBOOK_PASSWORD dans .env (compte gratuit)")
    if p.cot:
        c = p.cot
        lines.append(f"🏛️ <b>COT (gros spéculateurs, {e(c.date)}) :</b> {c.net:+,} contrats nets "
                     f"({c.net_change:+,} sur la semaine) · {c.long_pct:.0f} % acheteurs")
    return "\n".join(lines)


def news_report(n: NewsItem, x: NewsExplanation, moves: dict[str, float], report: str | None,
                tz: ZoneInfo) -> str:
    """Compte rendu d'une news qui a vraiment fait bouger le marché."""
    react = " · ".join(f"{name} <b>{chg:+.2f} %</b>" for name, chg in moves.items())
    lines = [
        "📣 <b>NEWS QUI FAIT BOUGER LE MARCHÉ</b>",
        "",
        f"<b>{e(x.title_fr)}</b>" + ("" if x.ai else " 🇺🇸"),
        f"<i>{e(n.source)} · {n.published.astimezone(tz):%H:%M}</i>",
        f"📊 Réaction depuis la news : {react}",
        f"🏦 Taux d'intérêt : {_RATES_ICON.get(x.rates, x.rates)}",
        "",
    ]
    if report:
        lines += ["🧠 <b>Compte rendu</b>", e(report)]
    else:
        lines += [f"📝 <b>C'est quoi :</b> {e(x.what)}", f"💥 <b>Impact :</b> {e(x.impact)}"]
        if x.reaction:
            lines.append(f"🎯 <b>Réaction probable :</b> {e(x.reaction)}")
    lines.append(f"🔗 <a href=\"{attr(n.link)}\">Lire l'article</a>")
    return "\n".join(lines)


# ------------------------------------------------------------------ niveaux et analyse IA

def levels_block(levels: list[Level], price: float | None, limit: int = 14) -> str:
    """Niveaux triés du plus haut au plus bas, avec le prix actuel placé au bon endroit."""
    if not levels:
        return "   (aucun niveau disponible)"
    rows = sorted(levels, key=lambda lv: lv.price, reverse=True)
    if price is not None and len(rows) > limit:  # on garde les plus proches du prix
        rows = sorted(sorted(rows, key=lambda lv: abs(lv.price - price))[:limit],
                      key=lambda lv: lv.price, reverse=True)
    lines, placed = [], price is None
    for lv in rows:
        if not placed and lv.price < price:
            lines.append(f"   ➡️ <b>prix actuel {price:,.2f}</b>")
            placed = True
        lines.append(f"   {'🔺' if price is not None and lv.price >= price else '🔻'} {lv.price:,.2f} — "
                     f"{e(lv.label)}")
    if not placed:
        lines.append(f"   ➡️ <b>prix actuel {price:,.2f}</b>")
    return "\n".join(lines)


_BIAS_BIG = {"haussier": "📈 HAUSSIER", "baissier": "📉 BAISSIER", "neutre": "➖ NEUTRE"}


def analysis_message(a: Analysis, inputs: list[MarketInput], tz: ZoneInfo) -> str:
    by_key = {m.key: m for m in inputs}
    lines = [f"🧠 <b>Ce qui se trame sur le marché</b> · {now_str(tz)}", "", e(a.overview)]
    if a.risk_events:
        lines += ["", "⚠️ <b>Événements à risque</b>", *[f"• {e(x)}" for x in a.risk_events]]
    for p in a.markets:
        m = by_key.get(p.key)
        inst = INSTRUMENTS[p.key]
        price = f" — {m.price:,.2f}" if m and m.price is not None else ""
        lines += ["", "━━━━━━━━━━━━━━━━", f"📊 <b>{e(inst.name)}</b>{price}",
                  f"Biais : <b>{_BIAS_BIG.get(p.bias, p.bias)}</b> (confiance {e(p.confidence)})",
                  f"🔎 <b>Ce qui se passe :</b> {e(p.whats_happening)}"]
        for icon, title, text in (("🎯", "Scénario principal", p.main_scenario),
                                  ("🔀", "Scénario alternatif", p.alt_scenario),
                                  ("🟢", "Zones d'achat à surveiller", p.buy_zones),
                                  ("🔴", "Zones de vente à surveiller", p.sell_zones),
                                  ("⛔", "Invalidation", p.invalidation),
                                  ("🚫", "À éviter", p.avoid)):
            if text:
                lines.append(f"{icon} <b>{title} :</b> {e(text)}")
        if m and m.levels:
            lines += ["📏 <b>Niveaux clés</b>", levels_block(m.levels, m.price, limit=8)]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def level_alert(inst: Instrument, lv: Level, price: float, from_below: bool, above: Level | None,
                below: Level | None) -> str:
    dist = abs(lv.price - price)
    lines = [f"📍 <b>{e(inst.name)} approche un niveau clé</b>",
             f"Prix {price:,.2f} → <b>{e(lv.label)} {lv.price:,.2f}</b> "
             f"(à {dist:,.2f}, {'par le bas' if from_below else 'par le haut'})",
             f"Rôle : {e(lv.role)}",
             "👉 Regarde la réaction : " + ("un rejet = vendeurs présents, une cassure franche = continuation "
                                           "vers le haut" if from_below else
                                           "un rebond = acheteurs présents, une cassure franche = continuation "
                                           "vers le bas")]
    if above:
        lines.append(f"🔺 Niveau suivant au-dessus : {above.price:,.2f} ({e(above.label)})")
    if below:
        lines.append(f"🔻 Niveau suivant en dessous : {below.price:,.2f} ({e(below.label)})")
    return "\n".join(lines)
