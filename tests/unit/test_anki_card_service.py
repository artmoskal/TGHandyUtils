"""Unit tests for AnkiCardService packaging (deterministic, no LLM/network)."""

import zipfile
from types import SimpleNamespace

import pytest

from models.anki import AnkiCard
from services.anki_card_service import AnkiCardService
from core.exceptions import ParsingError


def _service():
    # Packaging never touches the LLM, so a dummy key is fine.
    return AnkiCardService(SimpleNamespace(OPENAI_API_KEY="test-key"))


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
def test_build_package_empty_cards_raises():
    with pytest.raises(ValueError):
        _service().build_package([])


@pytest.mark.unit
def test_extract_cards_empty_content_raises():
    # Empty-content guard runs before any LLM call, so no network needed.
    with pytest.raises(ParsingError):
        _service().extract_cards("   ")
