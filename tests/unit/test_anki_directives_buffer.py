"""Unit tests for Anki directives, accumulation buffer, and image embedding."""

import os
from unittest.mock import Mock, AsyncMock

import pytest

from core.interfaces import ProcessingContext
from models.anki import AnkiCard
from models.anki_workflow import ImageAssetPlan, RenderedCardSet
from services.content.anki_directives import parse_directives
from services.content import anki_buffer
from services.content.anki_processor import AnkiProcessor


def _fake_image_graph(service):
    class FakeGraph:
        last_run_state = {}

        async def run(self, source, message=None):
            return RenderedCardSet(
                cards=service.extract_cards(source.content),
                image_asset_plan=ImageAssetPlan(
                    image_role="reuse_user_image",
                    candidate_back_images=[0],
                    rationale="Use uploaded image on the answer side.",
                ),
            )

    return FakeGraph()


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
def test_parse_bare_guide_tag_splits_source_and_instruction_to_end():
    d, content = parse_directives("JAA = Joint Aviation Authorities [i] abbr expand")
    assert d.guide_mode is True
    assert d.guide == "abbr expand"
    assert content == "JAA = Joint Aviation Authorities"


@pytest.mark.unit
def test_parse_bare_guide_tag_with_closing_tag_keeps_after_text_as_source():
    d, content = parse_directives("JAA = Joint Aviation Authorities [i] abbr expand[i] aviation authority acronym")
    assert d.guide_mode is True
    assert d.guide == "abbr expand"
    assert content == "JAA = Joint Aviation Authorities aviation authority acronym"


@pytest.mark.unit
def test_parse_instruction_block_with_closing_tag():
    d, content = parse_directives("Bernoulli principle [i visual gen] make image funny[i]")
    assert d.card_type == "visual"
    assert d.image_policy == "generate"
    assert d.guide_mode is True
    assert d.guide == "make image funny"
    assert content == "Bernoulli principle"


@pytest.mark.unit
def test_parse_instruction_block_to_end_when_source_precedes_tag():
    d, content = parse_directives("Bernoulli principle [i visual] make image funny")
    assert d.card_type == "visual"
    assert d.guide_mode is True
    assert d.guide == "make image funny"
    assert content == "Bernoulli principle"


@pytest.mark.unit
def test_parse_prefix_directive_keeps_text_source_without_image_source():
    d, content = parse_directives("[i cloze] Paris is in France")
    assert d.card_type == "cloze"
    assert d.guide is None
    assert content == "Paris is in France"


@pytest.mark.unit
def test_parse_prefix_directive_ignores_thread_speaker_prefix_for_guidance_detection():
    d, content = parse_directives("User: [i cloze] Paris is in France")
    assert d.card_type == "cloze"
    assert d.guide is None
    assert content == "User: Paris is in France"


@pytest.mark.unit
def test_parse_image_source_instruction_block_to_end():
    d, content = parse_directives("[i gen] make it funny", source_has_images=True)
    assert d.card_type == "visual"
    assert d.image_policy == "generate"
    assert d.guide_mode is True
    assert d.guide == "make it funny"
    assert content == ""


@pytest.mark.unit
def test_parse_card_type_flags():
    assert parse_directives("[i cloze] x")[0].card_type == "cloze"
    assert parse_directives("[i basic] x")[0].card_type == "basic"
    assert parse_directives("[i qs] x")[0].card_type is None  # auto by default
    d = parse_directives("[i cloze3] x")[0]
    assert d.card_type == "cloze" and d.count == 3


@pytest.mark.unit
def test_cloze_instructions_default_one_vs_n():
    from services.anki_card_service import AnkiCardService
    assert "EXACTLY ONE cloze" in AnkiCardService._build_instructions("split", None, None, "cloze")
    assert "EXACTLY 3 separate cloze" in AnkiCardService._build_instructions("split", 3, None, "cloze")


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


@pytest.mark.unit
def test_buffer_undo_last(tmp_path):
    uid = 99994
    anki_buffer.clear(uid)
    m1 = tmp_path / "a.jpg"
    m1.write_bytes(b"x")
    m2 = tmp_path / "b.jpg"
    m2.write_bytes(b"y")
    anki_buffer.add(uid, [AnkiCard(question="Q1", answer="A1")], [str(m1)])
    anki_buffer.add(uid, [AnkiCard(question="Q2", answer="A2"), AnkiCard(question="Q3", answer="A3")], [str(m2)])
    assert anki_buffer.count(uid) == 3

    removed = anki_buffer.undo_last(uid)
    assert removed == 2
    assert anki_buffer.count(uid) == 1
    assert m1.exists()        # first batch kept
    assert not m2.exists()    # undone batch's media removed

    assert anki_buffer.undo_last(uid) == 1
    assert anki_buffer.undo_last(uid) == 0  # nothing left
    anki_buffer.clear(uid)


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

    result = await AnkiProcessor(svc, anki_graph=_fake_image_graph(svc)).process(ctx)
    assert result.success

    # media_files passed (4th positional arg) and image embedded on the answer (back)
    args = svc.build_package.call_args.args
    media_files = args[3]
    assert media_files and os.path.exists(media_files[0])
    assert "<img" in args[0][0].answer
    assert "<img" not in args[0][0].question
    caption = message.reply_document.call_args.kwargs["caption"]
    assert "Images in Anki: 1 uploaded back." in caption
    assert "[back image:" in caption

    anki_buffer.clear(uid)
