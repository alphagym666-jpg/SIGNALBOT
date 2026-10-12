"""Lien automatique avec MetaTrader 5 (même ordinateur que le bot).

Le bot écrit chaque minute ses zones, le sentiment et les prochaines news dans le dossier
commun de MetaTrader (Common\\Files\\SignalBot_zones.txt). L'indicateur « SignalBot Zones »
(mql5/SignalBot_Zones.mq5) relit ce fichier tout seul et redessine les zones : rien à copier-coller.

Les prix du fichier sont ceux des contrats à terme (NQ, GC) ; l'indicateur les convertit au prix
du courtier (NAS100, XAUUSD…) avec le rapport entre les deux prix.
"""

from __future__ import annotations

import filecmp
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .sentiment import Sentiment
from .zones import Zone

log = logging.getLogger(__name__)

FILE_NAME = "SignalBot_zones.txt"
INDICATOR = Path(__file__).resolve().parent.parent / "mql5" / "SignalBot_Zones.mq5"


@dataclass
class MarketSnapshot:
    key: str
    price: float | None
    zones: list[Zone]
    sentiment: Sentiment
    gamma: str = ""  # « positif », « négatif » ou vide


def _metaquotes_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    path = Path(appdata) / "MetaQuotes" / "Terminal"
    return path if path.is_dir() else None


def files_dir(configured: str = "") -> Path | None:
    """Dossier commun de MetaTrader (ou celui de MT5_FILES_DIR). None si MetaTrader est absent."""
    if configured:
        return Path(configured)
    base = _metaquotes_dir()
    return base / "Common" / "Files" if base else None


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace(";", ",")).strip()


def build_file(snapshots: list[MarketSnapshot], events: list, now: float | None = None) -> str:
    lines = [f"TIME;{int(now or time.time())}"]
    for s in snapshots:
        if not s.price:
            continue
        st = s.sentiment
        lines.append(f"M;{s.key};{s.price:.2f};{st.score};{clean(st.label)};{st.buyers_pct};{clean(s.gamma)}")
        for z in s.zones:
            lines.append(f"Z;{s.key};{z.side};{z.low:.2f};{z.high:.2f};{z.force};{clean(', '.join(z.reasons))}")
        for r in st.reasons[:5]:
            lines.append(f"S;{s.key};{clean(r[2:] if r[:1] in '🟢🔴' else r)};{'+' if r.startswith('🟢') else '-'}")
    for ev in events[:6]:
        lines.append(f"N;{int(ev.time.timestamp())};{clean(ev.title)}")
    return "\n".join(lines) + "\n"


def write(snapshots: list[MarketSnapshot], events: list, configured_dir: str = "") -> Path | None:
    folder = files_dir(configured_dir)
    if folder is None:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / FILE_NAME
    tmp = folder / (FILE_NAME + ".tmp")
    tmp.write_text(build_file(snapshots, events), encoding="utf-8")
    os.replace(tmp, target)  # MetaTrader ne lit jamais un fichier à moitié écrit
    return target


def install_indicator() -> list[Path]:
    """Copie l'indicateur dans chaque MetaTrader 5 installé (MQL5\\Indicators). Retourne les copies faites."""
    base = _metaquotes_dir()
    done = []
    if base is None or not INDICATOR.exists():
        return done
    for terminal in base.iterdir():
        dest_dir = terminal / "MQL5" / "Indicators"
        if not dest_dir.is_dir():
            continue
        dest = dest_dir / INDICATOR.name
        try:
            if not dest.exists() or not filecmp.cmp(INDICATOR, dest, shallow=False):
                shutil.copyfile(INDICATOR, dest)
                done.append(dest)
        except OSError as exc:
            log.warning("Copie de l'indicateur MT5 impossible (%s) : %s", dest, exc)
    return done
