"""Per-user accumulation buffer for Anki cards.

Cards (with any image HTML already embedded) and their media file paths pile up here so the
user can export them as a single deck instead of importing one .apkg per message.
"""

import os
from typing import Dict, List, Tuple

from models.anki import AnkiCard

# user_id -> {"cards": [AnkiCard], "media": [filepath]}
_buffers: Dict[int, dict] = {}


def add(user_id: int, cards: List[AnkiCard], media_paths: List[str]) -> None:
    buf = _buffers.setdefault(user_id, {"cards": [], "media": []})
    buf["cards"].extend(cards)
    for p in media_paths or []:
        if p and p not in buf["media"]:
            buf["media"].append(p)


def count(user_id: int) -> int:
    return len(_buffers.get(user_id, {}).get("cards", []))


def get(user_id: int) -> Tuple[List[AnkiCard], List[str]]:
    buf = _buffers.get(user_id, {"cards": [], "media": []})
    return list(buf["cards"]), list(buf["media"])


def clear(user_id: int) -> None:
    buf = _buffers.pop(user_id, None)
    if not buf:
        return
    for p in buf["media"]:
        try:
            os.remove(p)
        except OSError:
            pass
