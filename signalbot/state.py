"""État persistant (JSON) : chats abonnés, news déjà envoyées, anti-doublons d'alertes."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"chats": [], "seen": {}, "cooldowns": {}, "stocks": None}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("État illisible (%s), on repart à zéro", exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.path)

    # --- chats
    @property
    def chats(self) -> list[int]:
        return self.data["chats"]

    def add_chat(self, chat_id: int) -> None:
        if chat_id not in self.chats:
            self.chats.append(chat_id)
            self.save()

    # --- éléments déjà envoyés (news, annonces)
    def seen(self, key: str) -> bool:
        return key in self.data["seen"]

    def mark_seen(self, key: str) -> None:
        seen = self.data["seen"]
        seen[key] = time.time()
        if len(seen) > 3000:
            for k in sorted(seen, key=seen.get)[:1000]:
                del seen[k]
        self.save()

    # --- anti-spam
    def cooling_down(self, key: str, seconds: float) -> bool:
        last = self.data["cooldowns"].get(key)
        return last is not None and time.time() - last < seconds

    def touch(self, key: str) -> None:
        self.data["cooldowns"][key] = time.time()
        self.save()

    # --- watchlist actions
    def stocks(self, default: list[str]) -> list[str]:
        return self.data["stocks"] if self.data["stocks"] is not None else list(default)

    def set_stocks(self, tickers: list[str]) -> None:
        self.data["stocks"] = tickers
        self.save()
