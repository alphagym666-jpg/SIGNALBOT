from pathlib import Path

from signalbot import tradingview as tv
from signalbot.levels import Level


def _pine_parse(line: str, key: str):
    """Reproduit exactement le découpage fait par l'indicateur Pine (parseSection + boucle)."""
    sec = ""
    for part in line.replace("\n", "").split("||"):
        if part.startswith(key + "|"):
            sec = part[len(key) + 1:]
    meta, levels = {}, []
    for item in sec.split(";") if sec else []:
        if "=" in item and item.split("=")[0] in ("BIAS", "GAMMA", "DATE"):
            k, v = item.split("=", 1)
            meta[k] = v
        else:
            kv = item.split(":")
            if len(kv) >= 2:
                levels.append((float(kv[0]), kv[1]))
    return meta, levels


def test_line_roundtrip_with_pine_parser():
    nq = tv.section("NQ", [Level("VWAP", 24_500, "r"), Level("Call wall (options)", 25_000, "r"),
                           Level("POC d'hier", 24_400.5, "r"), Level("Call wall (options)", 25_000, "r"),
                           Level("Haut d'hier", 24_700, "r")],
                    [{"price": 24_300, "kind": "achat", "note": "repli sur POC; zone"},
                     {"price": 24_100, "kind": "invalidation", "note": "sous le put wall"}],
                    "haussier", True, "2026-10-02")
    xau = tv.section("XAU", [Level("Put wall (SpotGamma)", 4_100, "r")], [], None, False, "2026-10-02")
    line = tv.build_line([nq, xau])

    meta, levels = _pine_parse(line, "NQ")
    assert meta == {"DATE": "2026-10-02", "BIAS": "haussier", "GAMMA": "positif"}
    names = [n for _, n in levels]
    assert "VWAP" not in names and "Haut d'hier" not in names  # calculés en direct par l'indicateur
    assert names.count("Call wall (options)") == 1  # pas de doublon
    assert (24_300.0, "Achat IA repli sur POC zone") in levels  # « ; » enlevé de la note
    assert [p for p, _ in levels] == sorted([p for p, _ in levels], reverse=True)

    meta_x, levels_x = _pine_parse(line, "XAU")
    assert meta_x["GAMMA"] == "négatif" and levels_x == [(4_100.0, "Put wall (SpotGamma)")]


def test_pine_file_basics():
    src = (Path(__file__).resolve().parent.parent / "tradingview" / "SignalBot_Niveaux.pine").read_text()
    assert src.startswith("//@version=5")
    assert 'indicator("SignalBot Niveaux"' in src
    for word in ("parseSection", "BIAS=", "GAMMA=", "DATE=", "CME_MINI:NQ1!", "COMEX:GC1!", "alert("):
        assert word in src
    assert "\t" not in src  # Pine refuse les tabulations


def test_pine_with_levels_embeds_line():
    code = tv.pine_with_levels('NQ|DATE=2026-10-03;25000:Call "wall"')
    assert code.startswith("//@version=5")
    assert 'input.text_area("NQ|DATE=2026-10-03;25000:Call \'wall\'", ' in code
    assert code.count("input.text_area(") == 1


def _pine_market(ticker: str) -> str:
    """Reproduit la reconnaissance automatique du marché faite par l'indicateur."""
    tk = ticker.upper()
    gold = "XAU" in tk or "GOLD" in tk or tk.startswith("GC")
    nas = (tk.startswith("NQ") or tk.startswith("MNQ") or any(w in tk for w in ("NAS", "US100", "USTEC", "NDX", "US TECH")))
    return "XAU" if gold else "NQ" if nas else ""


def test_market_detection_rules_match_pine():
    src = (Path(__file__).resolve().parent.parent / "tradingview" / "SignalBot_Niveaux.pine").read_text()
    assert 'isNas ? "NQ" : ""' in src  # un marché inconnu n'est plus traité comme le Nasdaq
    for t in ("XAUUSD", "GOLD", "GC1!", "XAUUSD.PRO"):
        assert _pine_market(t) == "XAU"
    for t in ("NQ1!", "MNQ1!", "NAS100", "US100", "USTEC", "NDX", "US100.CASH"):
        assert _pine_market(t) == "NQ"
    for t in ("EURUSD", "US30", "SPX500", "BTCUSD"):
        assert _pine_market(t) == ""
