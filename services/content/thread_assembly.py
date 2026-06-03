"""Shared helper: turn a threaded message list into flat content + screenshot data.

Used by every content processor so the (sender, text) / (sender, text, screenshot) tuple
handling lives in exactly one place.
"""

from typing import List, Optional, Tuple, Any


def assemble_thread(thread_content: List[Any]) -> Tuple[str, Optional[dict]]:
    """Return (concatenated "sender: text" content, first screenshot_data or None)."""
    text_content = []
    screenshot_data = None

    for item in thread_content:
        if len(item) == 3:  # (sender, content, screenshot)
            sender, content, image_result = item
            text_content.append((sender, content))
            if not screenshot_data:  # keep the first screenshot found
                screenshot_data = image_result
        else:  # (sender, content)
            text_content.append(item)

    concatenated = "\n".join([f"{sender}: {text}" for sender, text in text_content])
    return concatenated, screenshot_data


def collect_screenshots(thread_content: List[Any]) -> List[dict]:
    """Return all screenshot_data dicts in the thread (in order), for multi-image support."""
    shots = []
    for item in thread_content:
        if len(item) == 3 and item[2]:
            shots.append(item[2])
    return shots
