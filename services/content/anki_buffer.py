"""Per-user accumulation buffer for Anki cards, tracked as per-message batches.

Cards (with any image HTML already embedded) and their media file paths pile up here so the
user can export them as a single deck instead of importing one .apkg per message. Batches let
"Undo last" drop the most recent message's cards if it produced junk.
"""

import os
from typing import Dict, List, Tuple

from models.anki import AnkiCard

# user_id -> {"batches": [ {"cards": [AnkiCard], "media": [filepath]} ]}
_buffers: Dict[int, dict] = {}


def add(user_id: int, cards: List[AnkiCard], media_paths: List[str]) -> None:
    buf = _buffers.setdefault(user_id, {"batches": []})
    buf["batches"].append({"cards": list(cards), "media": list(media_paths or [])})


def count(user_id: int) -> int:
    return sum(len(b["cards"]) for b in _buffers.get(user_id, {}).get("batches", []))


def get(user_id: int) -> Tuple[List[AnkiCard], List[str]]:
    """Flatten all batches into (cards, deduped media paths)."""
    cards, media = [], []
    for b in _buffers.get(user_id, {}).get("batches", []):
        cards.extend(b["cards"])
        for p in b["media"]:
            if p and p not in media:
                media.append(p)
    return cards, media


def undo_last(user_id: int) -> int:
    """Remove the most recent batch; return how many cards were removed."""
    buf = _buffers.get(user_id)
    if not buf or not buf["batches"]:
        return 0
    last = buf["batches"].pop()
    for p in last["media"]:
        try:
            os.remove(p)
        except OSError:
            pass
    return len(last["cards"])


def clear(user_id: int) -> None:
    buf = _buffers.pop(user_id, None)
    if not buf:
        return
    for b in buf["batches"]:
        for p in b["media"]:
            try:
                os.remove(p)
            except OSError:
                pass
