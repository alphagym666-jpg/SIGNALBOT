"""Niveaux clés et positionnement : gamma des options, sentiment des particuliers, COT.

Trois sources, toutes gratuites :
1. Gamma des options (GEX) calculé à partir des chaînes d'options QQQ (Nasdaq) et GLD (or) de
   Yahoo Finance, puis converti en niveaux NQ / XAU. Donne le « call wall » (résistance), le
   « put wall » (support), le niveau de bascule gamma et le régime (gamma positif = marché amorti,
   gamma négatif = mouvements amplifiés). C'est une ESTIMATION : la convention habituelle suppose
   que les teneurs de marché sont acheteurs des calls et vendeurs des puts.
2. Sentiment des particuliers Myfxbook (compte gratuit : MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD) :
   % de traders acheteurs / vendeurs. Se lit à contre-courant : quand la foule est très
   acheteuse, le prix a souvent tendance à descendre.
3. Rapport COT de la CFTC (hebdomadaire, sans compte) : position nette des gros spéculateurs
   sur les contrats à terme de l'or et du Nasdaq.
"""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from . import data
from .config import INSTRUMENTS, settings

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (SignalBot)"
RISK_FREE = 0.04

# instrument -> (ETF dont on lit les options, symbole Myfxbook, code CFTC du contrat à terme)
SOURCES = {
    "NQ": ("QQQ", None, "209742"),     # NASDAQ-100 (mini) - CME
    "XAU": ("GLD", "XAUUSD", "088691"),  # GOLD - COMEX
}


# ------------------------------------------------------------------ gamma des options

@dataclass
class GammaLevels:
    instrument: str
    spot: float  # prix de l'instrument (NQ / XAU)
    call_wall: float | None  # plus gros gamma des calls au-dessus : résistance
    put_wall: float | None  # plus gros gamma des puts en dessous : support
    flip: float | None  # bascule gamma positif / négatif
    total_gex: float  # en milliards $ par mouvement de 1 %
    expiries: int = 0

    @property
    def positive(self) -> bool:
        return self.total_gex >= 0


def _bs_gamma(spot: np.ndarray | float, strike: np.ndarray, iv: np.ndarray, t: np.ndarray):
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / strike) + (RISK_FREE + 0.5 * iv ** 2) * t) / (iv * sqrt_t)
    return np.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi) / (spot * iv * sqrt_t)


def _gex(spot: float, chain: pd.DataFrame) -> pd.Series:
    """Gamma en $ par mouvement de 1 % (calls +, puts -), par strike."""
    g = _bs_gamma(spot, chain["strike"].values, chain["iv"].values, chain["t"].values)
    dollars = g * chain["oi"].values * 100 * spot ** 2 * 0.01 * chain["sign"].values
    return pd.Series(dollars, index=chain["strike"].values).groupby(level=0).sum()


def _option_chain(etf: str, max_expiries: int = 6, max_days: int = 45) -> pd.DataFrame:
    tk = yf.Ticker(etf)
    now = datetime.now(timezone.utc)
    rows = []
    used = 0
    for exp in list(tk.options or [])[:max_expiries * 2]:
        expiry = datetime.strptime(exp, "%Y-%m-%d").replace(hour=20, tzinfo=timezone.utc)
        days = (expiry - now).total_seconds() / 86400
        if days > max_days or used >= max_expiries:
            break
        chain = tk.option_chain(exp)
        t = max(days, 0.5) / 365
        for df, sign in ((chain.calls, 1), (chain.puts, -1)):
            part = df[["strike", "openInterest", "impliedVolatility"]].dropna()
            part = part[(part["openInterest"] > 0) & (part["impliedVolatility"] > 0.01)]
            rows.append(pd.DataFrame({"strike": part["strike"], "oi": part["openInterest"],
                                      "iv": part["impliedVolatility"], "t": t, "sign": sign}))
        used += 1
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    out["expiry_count"] = used
    return out


def levels_from_chain(chain: pd.DataFrame, etf_spot: float, ratio: float, instrument: str,
                      expiries: int = 0) -> GammaLevels | None:
    """Calcule les niveaux gamma (en prix de l'ETF) et les convertit en prix de l'instrument."""
    if chain.empty or etf_spot <= 0:
        return None
    chain = chain[(chain["strike"] > etf_spot * 0.8) & (chain["strike"] < etf_spot * 1.2)]
    if chain.empty:
        return None
    by_strike = _gex(etf_spot, chain)
    calls = _gex(etf_spot, chain[chain["sign"] > 0])
    puts = _gex(etf_spot, chain[chain["sign"] < 0])
    above = calls[calls.index > etf_spot]
    below = puts[puts.index < etf_spot]
    call_wall = float(above.idxmax()) if not above.empty else None
    put_wall = float(below.idxmin()) if not below.empty else None

    # niveau de bascule : prix où le gamma total change de signe (le plus proche du prix actuel)
    grid = np.linspace(etf_spot * 0.9, etf_spot * 1.1, 81)
    totals = np.array([_gex(s, chain).sum() for s in grid])
    flip = None
    crossings = np.where(np.diff(np.sign(totals)) != 0)[0]
    if len(crossings):
        i = min(crossings, key=lambda k: abs(grid[k] - etf_spot))
        x0, x1, y0, y1 = grid[i], grid[i + 1], totals[i], totals[i + 1]
        flip = float(x0 - y0 * (x1 - x0) / (y1 - y0))

    def conv(v):
        return round(v * ratio, 2) if v is not None else None

    return GammaLevels(instrument, round(etf_spot * ratio, 2), conv(call_wall), conv(put_wall),
                       conv(flip), float(by_strike.sum()) / 1e9, expiries)


_gamma_cache: dict[str, tuple[float, GammaLevels | None]] = {}


def gamma_levels(key: str) -> GammaLevels | None:
    hit = _gamma_cache.get(key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    etf = SOURCES[key][0]
    result = None
    try:
        etf_hist = data.history(etf, "5d", "1d", ttl=600)
        inst_hist = data.history(INSTRUMENTS[key].ticker, "5d", "1d", ttl=600)
        if not etf_hist.empty and not inst_hist.empty:
            etf_spot = float(etf_hist["Close"].iloc[-1])
            ratio = float(inst_hist["Close"].iloc[-1]) / etf_spot
            chain = _option_chain(etf)
            expiries = int(chain["expiry_count"].iloc[0]) if not chain.empty else 0
            result = levels_from_chain(chain, etf_spot, ratio, key, expiries)
    except Exception as exc:
        log.warning("Gamma %s indisponible: %s", key, exc)
    _gamma_cache[key] = (time.time(), result)
    return result


# ------------------------------------------------------------------ Myfxbook

@dataclass
class RetailSentiment:
    symbol: str
    long_pct: float
    short_pct: float
    avg_long: float | None
    avg_short: float | None


_mfx_session: dict = {"id": None, "ts": 0.0}
_mfx_cache: dict = {"ts": 0.0, "data": {}}


def _get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def myfxbook_enabled() -> bool:
    return bool(settings.myfxbook_email and settings.myfxbook_password)


def _mfx_login() -> str | None:
    if _mfx_session["id"] and time.time() - _mfx_session["ts"] < 6 * 3600:
        return _mfx_session["id"]
    q = urllib.parse.urlencode({"email": settings.myfxbook_email,
                                "password": settings.myfxbook_password})
    res = _get_json(f"https://www.myfxbook.com/api/login.json?{q}")
    if res.get("error"):
        log.warning("Myfxbook : connexion refusée (%s)", res.get("message"))
        return None
    _mfx_session.update(id=res.get("session"), ts=time.time())
    return _mfx_session["id"]


def parse_outlook(res: dict) -> dict[str, RetailSentiment]:
    out = {}
    for s in res.get("symbols") or []:
        try:
            out[s["name"].upper()] = RetailSentiment(
                s["name"].upper(), float(s["longPercentage"]), float(s["shortPercentage"]),
                float(s["avgLongPrice"]) if s.get("avgLongPrice") else None,
                float(s["avgShortPrice"]) if s.get("avgShortPrice") else None)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def retail_sentiment() -> dict[str, RetailSentiment]:
    """Sentiment des particuliers (gratuit : 100 requêtes / jour -> cache de 30 min)."""
    if not myfxbook_enabled():
        return {}
    if time.time() - _mfx_cache["ts"] < 1800:
        return _mfx_cache["data"]
    result: dict[str, RetailSentiment] = {}
    try:
        session = _mfx_login()
        if session:
            res = _get_json("https://www.myfxbook.com/api/get-community-outlook.json?"
                            + urllib.parse.urlencode({"session": session}))
            if res.get("error"):
                _mfx_session["id"] = None  # session expirée : on se reconnectera
                log.warning("Myfxbook : %s", res.get("message"))
            else:
                result = parse_outlook(res)
    except Exception as exc:
        log.warning("Myfxbook indisponible: %s", exc)
    _mfx_cache.update(ts=time.time(), data=result)
    return result


# ------------------------------------------------------------------ COT (CFTC)

@dataclass
class CotReport:
    instrument: str
    date: str
    net: int  # position nette des gros spéculateurs (non commerciaux)
    net_change: int  # variation sur la semaine
    long_pct: float  # part des positions acheteuses chez les spéculateurs


COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_cot_cache: dict[str, tuple[float, CotReport | None]] = {}


def parse_cot(rows: list[dict], key: str) -> CotReport | None:
    if not rows:
        return None
    r = rows[0]
    try:
        long_, short = int(float(r["noncomm_positions_long_all"])), int(float(r["noncomm_positions_short_all"]))
        ch_long = int(float(r.get("change_in_noncomm_long_all") or 0))
        ch_short = int(float(r.get("change_in_noncomm_short_all") or 0))
    except (KeyError, TypeError, ValueError):
        return None
    total = long_ + short
    return CotReport(key, str(r.get("report_date_as_yyyy_mm_dd", ""))[:10], long_ - short,
                     ch_long - ch_short, 100 * long_ / total if total else 50.0)


def cot_report(key: str) -> CotReport | None:
    hit = _cot_cache.get(key)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    code = SOURCES[key][2]
    q = urllib.parse.urlencode({"$where": f"cftc_contract_market_code='{code}'",
                                "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "1"})
    result = None
    try:
        req = urllib.request.Request(f"{COT_URL}?{q}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = parse_cot(json.loads(resp.read().decode()), key)
    except Exception as exc:
        log.warning("COT %s indisponible: %s", key, exc)
    _cot_cache[key] = (time.time(), result)
    return result


# ------------------------------------------------------------------ vue d'ensemble

@dataclass
class Positioning:
    instrument: str
    gamma: GammaLevels | None = None
    retail: RetailSentiment | None = None
    cot: CotReport | None = None
    notes: list[str] = field(default_factory=list)


def positioning(key: str) -> Positioning:
    p = Positioning(key, gamma=gamma_levels(key), cot=cot_report(key))
    mfx_symbol = SOURCES[key][1]
    if mfx_symbol:
        p.retail = retail_sentiment().get(mfx_symbol)
    return p


def summary_for_ai(positions: list[Positioning]) -> str:
    """Résumé texte des niveaux / positionnements, à donner à Claude comme contexte."""
    lines = []
    for p in positions:
        name = INSTRUMENTS[p.instrument].name
        if p.gamma:
            g = p.gamma
            lines.append(f"{name} : gamma {'positif' if g.positive else 'négatif'} "
                         f"({g.total_gex:+.2f} G$/1%), call wall {g.call_wall}, put wall {g.put_wall}, "
                         f"bascule gamma {g.flip}")
        if p.retail:
            lines.append(f"{name} : particuliers {p.retail.long_pct:.0f}% acheteurs / "
                         f"{p.retail.short_pct:.0f}% vendeurs (Myfxbook)")
        if p.cot:
            lines.append(f"{name} : COT du {p.cot.date}, spéculateurs nets {p.cot.net:+,} contrats "
                         f"({p.cot.net_change:+,} sur la semaine)")
    return "\n".join(lines) or "indisponible"
