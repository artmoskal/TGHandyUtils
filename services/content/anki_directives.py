"""Parse the `[i ...]` directive block that controls Anki card generation.

Examples:
  [i if qm] what parts does the valve have?                 -> image on front, one merged question
  JAA = Joint Aviation Authorities [i] abbr expand           -> guide to end
  Bernoulli principle [i visual gen] make image funny[i]     -> shortcodes + guide block
  [i langvoice ukr->pt gen] fala                                -> language visual + target audio
  [i i-]    text only (no image)
  [i h]  /  /anki help                                      -> show the reference
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

_TAG_RE = re.compile(r"\[i(?:\s+([^\]]+))?\]", re.IGNORECASE)
_CLOSE_TAG_RE = re.compile(r"\[i\]", re.IGNORECASE)
# Only a message that is ENTIRELY a speaker prefix (e.g. "Name:") is dropped. We deliberately do NOT
# strip "Name: <content>" speaker prefixes from the source: multi-speaker dialogues need the speaker
# context, and removing it here would lose who-said-what. Keeping the sender out of the *card* is the
# LLM's job at the prompt level (the planner/render prompts must ignore speaker labels), NOT a
# deterministic strip here. (Design decision, Artem 2026-06-22 — do not "fix" by stripping.)
_SPEAKER_PREFIX_ONLY_RE = re.compile(r"^(?:[^:\n]{1,80}:\s*)+$")

HELP_TEXT = (
    "🃏 *Anki card tags* — put `[i ...]` anywhere in your message:\n\n"
    "Use `[i shortcodes]instruction[i]`; closing `[i]` is optional when the instruction runs to "
    "the end. The bracket part is shortcodes only.\n\n"
    "*Image* (when a photo is attached):\n"
    "`[i ib]`/`[i b]` image on back (default) · `[i if]`/`[i f]` image on front\n"
    "`[i ibf]` 1st image back, 2nd front · `[i i-]` no image (text only)\n\n"
    "*Questions:*\n"
    "`[i qs]` split into several (default) · `[i qm]` one combined question · `[i q3]` exactly 3\n\n"
    "*Card type:* auto by default · `[i basic]` force Q/A · `[i cloze]` one cloze card "
    "(several deletions) · `[i cloze3]` three cloze cards · `[i visual]` prefer a visual card\n\n"
    "*Generated visuals:* `[i gen]` generate a visual · `[i ref]` use attached/style references "
    "for generated art · `[i reuse]` force attached image reuse\n\n"
    "*Language + voice:* `[i langvoice ukr->pt gen]` source-language to Portuguese card with "
    "generated visual and Portuguese audio. Source can be `any`, `en`, `ukr`, etc.; target is "
    "`pt` for now.\n\n"
    "*Guide:* `<source> [i] <instruction>` or `<source> [i p] <instruction>` — text before the tag "
    "is the source, text after is an instruction for how to make the card "
    "(e.g. `JAA = Joint Aviation Authorities [i] abbr expand` "
    "→ front: JAA, back: full form)\n\n"
    "Combine them: `[i if qm] what parts does the valve have?`\n\n"
    "Cards collect into a deck — tap *📦 Export* when done, *🗑 Clear* to reset.\n"
    "Type `/anki help` to see this again."
)


@dataclass
class AnkiDirectives:
    include_image: bool = True
    image_placement: str = "back"   # back | front
    image_policy: str = "auto"      # auto | none | reuse_user_image | reference | generate
    multi_split: bool = False       # ibf: 1st image -> back, 2nd -> front
    strategy: str = "split"         # split | merge | count
    count: Optional[int] = None
    card_type: Optional[str] = None  # None=auto, or forced "basic" / "cloze" / "visual"
    guide_mode: bool = False        # p flag present
    guide: Optional[str] = None     # the instruction text (what follows [i p])
    help: bool = False
    language_voice: bool = False
    source_language: Optional[str] = None
    target_language: Optional[str] = None


def _has_meaningful_source_before_tag(before: str) -> bool:
    before = before.strip()
    return bool(before) and not _SPEAKER_PREFIX_ONLY_RE.fullmatch(before)


def parse_directives(text: str, source_has_images: bool = False) -> Tuple[AnkiDirectives, str]:
    """Return (directives, text-with-the-[i ...]-block-removed)."""
    d = AnkiDirectives()
    if not text:
        return d, text or ""

    m = _TAG_RE.search(text)
    if not m:
        return d, text

    for flag in (m.group(1) or "").lower().split():
        if flag in ("ib", "b"):
            d.include_image, d.image_placement = True, "back"
        elif flag in ("if", "f"):
            d.include_image, d.image_placement = True, "front"
        elif flag == "ibf":
            d.include_image, d.multi_split = True, True
        elif flag in ("i-", "i_", "i0", "in"):
            d.include_image = False
            d.image_policy = "none"
        elif flag in ("reuse", "userimg"):
            d.image_policy = "reuse_user_image"
        elif flag in ("ref", "reference"):
            d.image_policy = "reference"
            d.card_type = d.card_type or "visual"
        elif flag in ("gen", "generate"):
            d.image_policy = "generate"
            d.card_type = d.card_type or "visual"
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
        elif flag in ("visual", "image", "img", "v"):
            d.card_type = "visual"
        elif flag == "p":
            d.guide_mode = True
        elif flag in ("h", "help", "?"):
            d.help = True
        elif flag in ("langvoice", "voicecard", "lv"):
            d.language_voice = True
            d.card_type = d.card_type or "visual"
            d.strategy = "merge"
            d.count = 1
        elif re.fullmatch(r"[a-z]{2,3}->[a-z]{2,3}", flag) or re.fullmatch(r"any->[a-z]{2,3}", flag):
            source_lang, target_lang = flag.split("->", 1)
            d.source_language = source_lang
            d.target_language = target_lang

    if d.language_voice:
        d.source_language = d.source_language or "any"
        d.target_language = d.target_language or "pt"
        d.card_type = "visual"
        d.strategy = "merge"
        d.count = 1
        if d.image_policy == "auto":
            d.image_policy = "generate"

    before = text[:m.start()].strip()
    raw_after = text[m.end():]
    closing = _CLOSE_TAG_RE.search(raw_after)
    if closing:
        instruction = raw_after[:closing.start()].strip()
        after = raw_after[closing.end():].strip()
        has_instruction_block = True
    else:
        instruction = raw_after.strip()
        after = raw_after.strip()
        has_instruction_block = bool(
            _has_meaningful_source_before_tag(before) or source_has_images or d.guide_mode
        )

    if has_instruction_block:
        # Convention: "<source> [i flags] <instruction>[i]" — text after the tag is guidance,
        # text outside the block is source material. If there's no source text, an attached image
        # can be the source.
        d.guide_mode = bool(instruction) or d.guide_mode
        d.guide = instruction or None
        cleaned = before if _has_meaningful_source_before_tag(before) else ""
        if closing and after:
            cleaned = (cleaned + " " + after).strip()
    else:
        cleaned = (before + " " + after).strip()

    return d, cleaned
