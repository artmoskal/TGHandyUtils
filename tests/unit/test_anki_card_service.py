"""Unit tests for AnkiCardService packaging (deterministic, no LLM/network)."""

import zipfile
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from models.anki import AnkiCard
from services.anki_card_service import AnkiCardService
from core.exceptions import ParsingError


def _service():
    # Packaging never touches the LLM, so a dummy key is fine.
    return AnkiCardService(SimpleNamespace(OPENAI_API_KEY="test-key"))


class _FakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.outputs.pop(0))


@pytest.mark.unit
def test_build_package_produces_valid_apkg(tmp_path):
    cards = [
        AnkiCard(question="Capital of France?", answer="Paris", tags=["geo", "europe"]),
        AnkiCard(question="2 + 2?", answer="4"),
    ]
    out = tmp_path / "deck.apkg"
    result = _service().build_package(cards, deck_name="Test Deck", output_path=str(out))

    assert result == str(out)
    assert out.exists() and out.stat().st_size > 0
    # An .apkg is a zip containing the Anki collection database.
    assert zipfile.is_zipfile(str(out))
    with zipfile.ZipFile(str(out)) as zf:
        names = zf.namelist()
    assert any(n.startswith("collection.anki2") for n in names), names


@pytest.mark.unit
def test_build_package_tags_with_spaces_are_normalised(tmp_path):
    # Anki tags cannot contain spaces; service must normalise them.
    cards = [AnkiCard(question="Q", answer="A", tags=["spaced tag"])]
    out = tmp_path / "deck.apkg"
    # Should not raise; genanki would reject a space-containing tag otherwise.
    _service().build_package(cards, output_path=str(out))
    assert out.exists()


@pytest.mark.unit
def test_build_package_cloze_card(tmp_path):
    cards = [AnkiCard(type="cloze", text="Paris is the capital of {{c1::France}}.")]
    out = tmp_path / "cloze.apkg"
    _service().build_package(cards, output_path=str(out))
    assert out.exists() and zipfile.is_zipfile(str(out))


@pytest.mark.unit
def test_build_package_cloze_without_deletion_falls_back(tmp_path):
    # marked cloze but no {{c...}} -> must not crash (falls back to a basic note)
    cards = [AnkiCard(type="cloze", text="just a sentence with no deletion")]
    out = tmp_path / "fallback.apkg"
    _service().build_package(cards, output_path=str(out))
    assert out.exists() and zipfile.is_zipfile(str(out))


@pytest.mark.unit
def test_build_package_empty_cards_raises():
    with pytest.raises(ValueError):
        _service().build_package([])


@pytest.mark.unit
def test_extract_cards_empty_content_raises():
    # Empty-content guard runs before any LLM call, so no network needed.
    with pytest.raises(ParsingError):
        _service().extract_cards("   ")


@pytest.mark.unit
def test_extract_cards_uses_render_model_profile(monkeypatch):
    calls = []

    def fake_create_chat_llm(config, model, temperature):
        calls.append((model, temperature))
        return _FakeLLM(['{"cards":[{"type":"basic","question":"Q","answer":"A","tags":[]}]}'])

    monkeypatch.setattr("services.anki_card_service.create_chat_llm", fake_create_chat_llm)
    svc = AnkiCardService(
        SimpleNamespace(
            OPENAI_API_KEY="test-key",
            ANKI_CARD_MODEL="gpt-5.4-nano",
            ANKI_RENDER_MODEL="gpt-5.4-mini",
        )
    )

    cards = svc.extract_cards("Fact.")

    assert cards[0].question == "Q"
    assert calls == [("gpt-5.4-mini", 0.2)]


@pytest.mark.unit
def test_extract_cards_uses_cache_friendly_renderer_messages():
    svc = _service()
    svc._llm = _FakeLLM(['{"cards":[{"type":"basic","question":"Q","answer":"A","tags":[]}]}'])

    svc.extract_cards("A valve controls flow.", guide="make it concise")

    messages = svc._llm.calls[0]
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert "expert at creating Anki flashcards" in messages[0].content
    assert "Sibling list / named set" in messages[0].content
    assert "back lists all high-value items" in messages[0].content
    assert "only one sample item" in messages[0].content
    assert "make it concise" not in messages[0].content
    assert "A valve controls flow" in messages[1].content
    assert "make it concise" in messages[1].content


@pytest.mark.unit
def test_extract_cards_normalises_single_brace_cloze_markup():
    svc = _service()
    svc._llm = _FakeLLM(
        [
            '{"cards":[{"type":"cloze","text":"The pitot-static system measures {c1::impact pressure}.","tags":[]}]}'
        ]
    )

    cards = svc.extract_cards("The pitot-static system measures impact pressure.", card_type="cloze")

    assert cards[0].text == "The pitot-static system measures {{c1::impact pressure}}."
    assert len(svc._llm.calls) == 1


@pytest.mark.unit
def test_extract_cards_retries_invalid_requested_cloze_once():
    svc = _service()
    svc._llm = _FakeLLM(
        [
            '{"cards":[{"type":"cloze","text":"No deletion here.","tags":[]}]}',
            '{"cards":[{"type":"cloze","text":"The system measures {{c1::impact pressure}}.","tags":[]}]}',
        ]
    )

    cards = svc.extract_cards("The system measures impact pressure.", card_type="cloze")

    assert cards[0].text == "The system measures {{c1::impact pressure}}."
    assert len(svc._llm.calls) == 2
