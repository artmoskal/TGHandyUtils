"""Parse the `[i ...]` directive block that controls Anki card generation.

Examples:
  [i if qm] what parts does the valve have?   -> image on front, one merged question
  [i i-]    text only (no image)
  [i p] focus on the inlet/exhaust valves     -> treat the text as a guide, not card content
  [i h]  /  /anki help                          -> show the reference
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

_TAG_RE = re.compile(r"\[i\s+([^\]]+)\]", re.IGNORECASE)

HELP_TEXT = (
    "🃏 *Anki card tags* — put `[i ...]` anywhere in your message:\n\n"
    "*Image* (when a photo is attached):\n"
    "`[i ib]`/`[i b]` image on back (default) · `[i if]`/`[i f]` image on front\n"
    "`[i ibf]` 1st image back, 2nd front · `[i i-]` no image (text only)\n\n"
    "*Questions:*\n"
    "`[i qs]` split into several (default) · `[i qm]` one combined question · `[i q3]` exactly 3\n\n"
    "*Card type:* auto by default · `[i basic]` force Q/A · `[i cloze]` one cloze card "
    "(several deletions) · `[i cloze3]` three cloze cards\n\n"
    "*Guide:* `<source> [i p] <instruction>` — text before `[i p]` is the source, text after is "
    "an instruction for how to make the card (e.g. `JAA = Joint Aviation Authorities [i p] abbr expand` "
    "→ front: JAA, back: full form)\n\n"
    "Combine them: `[i if qm] what parts does the valve have?`\n\n"
    "Cards collect into a deck — tap *📦 Export* when done, *🗑 Clear* to reset.\n"
    "Type `/anki help` to see this again."
)


@dataclass
class AnkiDirectives:
    include_image: bool = True
    image_placement: str = "back"   # back | front
    multi_split: bool = False       # ibf: 1st image -> back, 2nd -> front
    strategy: str = "split"         # split | merge | count
    count: Optional[int] = None
    card_type: Optional[str] = None  # None=auto, or forced "basic" / "cloze"
    guide_mode: bool = False        # p flag present
    guide: Optional[str] = None     # the instruction text (what follows [i p])
    help: bool = False


def parse_directives(text: str) -> Tuple[AnkiDirectives, str]:
    """Return (directives, text-with-the-[i ...]-block-removed)."""
    d = AnkiDirectives()
    if not text:
        return d, text or ""

    m = _TAG_RE.search(text)
    if not m:
        return d, text

    for flag in m.group(1).lower().split():
        if flag in ("ib", "b"):
            d.include_image, d.image_placement = True, "back"
        elif flag in ("if", "f"):
            d.include_image, d.image_placement = True, "front"
        elif flag == "ibf":
            d.include_image, d.multi_split = True, True
        elif flag in ("i-", "i_", "i0", "in"):
            d.include_image = False
        elif flag == "qs":
            d.strategy = "split"
        elif flag == "qm":
            d.strategy = "merge"
        elif re.fullmatch(r"q\d+", flag):
            d.strategy, d.count = "count", int(flag[1:])
        elif flag in ("cloze", "c"):
            d.card_type = "cloze"  # default: one cloze note
        elif re.fullmatch(r"cloze\d+", flag):
            d.card_type, d.count = "cloze", int(flag[5:])  # N cloze notes
        elif flag == "basic":
            d.card_type = "basic"
        elif flag == "p":
            d.guide_mode = True
        elif flag in ("h", "help", "?"):
            d.help = True

    before = text[:m.start()].strip()
    after = text[m.end():].strip()

    if d.guide_mode:
        # Convention: "<source> [i p] <instruction>" — text after the tag is the instruction,
        # text before it is the source material. (If there's no source text, an attached image
        # is the source.)
        d.guide = after or None
        cleaned = before
    else:
        cleaned = (before + " " + after).strip()

    return d, cleaned
