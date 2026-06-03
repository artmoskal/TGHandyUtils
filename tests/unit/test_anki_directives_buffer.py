"""Unit tests for Anki directives, accumulation buffer, and image embedding."""

import os
from unittest.mock import Mock, AsyncMock

import pytest

from core.interfaces import ProcessingContext
from models.anki import AnkiCard
from services.content.anki_directives import parse_directives
from services.content import anki_buffer
from services.content.anki_processor import AnkiProcessor


# ---- directive parsing ----

@pytest.mark.unit
def test_parse_image_and_question_flags():
    d, cleaned = parse_directives("[i if qm] what parts does the valve have?")
    assert d.include_image is True
    assert d.image_placement == "front"
    assert d.strategy == "merge"
    assert cleaned == "what parts does the valve have?"


@pytest.mark.unit
def test_parse_no_image_flag():
    d, _ = parse_directives("[i i-] some text")
    assert d.include_image is False


@pytest.mark.unit
def test_parse_short_image_aliases():
    assert parse_directives("[i b qm] x")[0].image_placement == "back"
    assert parse_directives("[i f] x")[0].image_placement == "front"


@pytest.mark.unit
def test_parse_count_and_guide_and_multi():
    assert parse_directives("[i q3] x")[0].count == 3
    assert parse_directives("[i p] focus on valves")[0].guide_mode is True
    assert parse_directives("[i ibf] x")[0].multi_split is True


@pytest.mark.unit
def test_parse_guide_splits_source_and_instruction():
    d, content = parse_directives("JAA = Joint Aviation Authorities [i p] abbr expand")
    assert d.guide_mode is True
    assert d.guide == "abbr expand"
    assert content == "JAA = Joint Aviation Authorities"


@pytest.mark.unit
def test_parse_help_and_no_tags():
    assert parse_directives("[i h]")[0].help is True
    d, cleaned = parse_directives("plain content")
    assert d.help is False and cleaned == "plain content"


# ---- buffer ----

@pytest.mark.unit
def test_buffer_add_count_get_clear(tmp_path):
    uid = 99991
    anki_buffer.clear(uid)
    media = tmp_path / "img.jpg"
    media.write_bytes(b"data")
    anki_buffer.add(uid, [AnkiCard(question="Q", answer="A")], [str(media)])
    anki_buffer.add(uid, [AnkiCard(question="Q2", answer="A2")], [str(media)])  # dup media not re-added
    assert anki_buffer.count(uid) == 2
    cards, paths = anki_buffer.get(uid)
    assert len(cards) == 2 and paths == [str(media)]
    anki_buffer.clear(uid)
    assert anki_buffer.count(uid) == 0
    assert not media.exists()  # clear removes media


# ---- image embedding in the processor ----

@pytest.mark.unit
async def test_anki_processor_embeds_image_on_back_by_default():
    uid = 99992
    anki_buffer.clear(uid)
    svc = Mock()
    svc.extract_cards.return_value = [AnkiCard(question="What is shown?", answer="A valve")]
    svc.build_package.return_value = "/tmp/does_not_exist.apkg"

    message = Mock()
    message.reply = AsyncMock(return_value=Mock(delete=AsyncMock()))
    message.reply_document = AsyncMock()

    screenshot = {"image_data": b"\xff\xd8\xff", "file_id": "f1", "file_name": "x.jpg"}
    ctx = ProcessingContext(
        message=message,
        thread_content=[("U", "[SCREENSHOT TEXT] valve diagram", screenshot)],
        user_id=uid, owner_name="U", location=None,
    )

    result = await AnkiProcessor(svc).process(ctx)
    assert result.success

    # media_files passed (4th positional arg) and image embedded on the answer (back)
    args = svc.build_package.call_args.args
    media_files = args[3]
    assert media_files and os.path.exists(media_files[0])
    assert "<img" in args[0][0].answer
    assert "<img" not in args[0][0].question

    anki_buffer.clear(uid)
