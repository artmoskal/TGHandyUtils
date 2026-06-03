"""Integration eval: generate real flashcards with gpt-4o-mini and write .apkg files.

This is an evaluation harness, not a strict assertion test - its value is the printed Q/A and
the .apkg files (under test-results/anki_eval/) which you import into Anki to judge quality and
import friction. Add samples in tests/integration/anki_samples.yaml.

Run: ./test.sh integration -- tests/integration/test_anki_eval.py -s
"""

import os
import zipfile

import pytest
import yaml

from config import Config
from services.anki_card_service import AnkiCardService

SAMPLES_FILE = os.path.join(os.path.dirname(__file__), "anki_samples.yaml")
OUTPUT_DIR = os.path.join("test-results", "anki_eval")


def _load_samples():
    with open(SAMPLES_FILE, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("samples", [])


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("sample", _load_samples(), ids=lambda s: s.get("name", "sample"))
def test_generate_cards_for_sample(sample):
    if not Config.OPENAI_API_KEY:
        pytest.skip("OPENAI_API_KEY not set")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    service = AnkiCardService(Config())

    name = sample["name"]
    content = sample["content"]

    cards = service.extract_cards(content)
    assert len(cards) >= 1

    print(f"\n=== {name} -> {len(cards)} card(s) ===")
    for i, card in enumerate(cards, 1):
        print(f"  [{i}] Q: {card.question}")
        print(f"      A: {card.answer}")
        print(f"      tags: {card.tags}")

    out_path = os.path.join(OUTPUT_DIR, f"{name}.apkg")
    service.build_package(cards, deck_name=f"Eval - {name}", output_path=out_path)
    assert zipfile.is_zipfile(out_path)
    print(f"  wrote {out_path}")
