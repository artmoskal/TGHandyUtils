from types import SimpleNamespace

import pytest
from dependency_injector import providers

from composition.container import ApplicationContainer


def _fake_config():
    return SimpleNamespace(
        DATABASE_PATH=":memory:",
        DATABASE_TIMEOUT=30,
        OPENAI_API_KEY="test-openai-key",
        ANKI_CARD_MODEL="gpt-5.4-mini",
        ANKI_DECISION_MODEL="gpt-5.4-mini",
        ANKI_SCENARIO_MODEL="gpt-5.4-mini",
        ANKI_RENDER_MODEL="gpt-5.4-mini",
        ANKI_QUALITY_MODEL="gpt-5.4-mini",
        ANKI_QUALITY_INSPECT_IMAGES=False,
        ANKI_QUALITY_EVALUATION_ENABLED=True,
        ANKI_EVALUATE_FALLBACK_CARDS=False,
        ANKI_MAX_QUALITY_REPAIRS_PER_RUN=1,
        ANKI_IMAGE_GENERATION_ENABLED=True,
        ANKI_AUTO_IMAGE_GENERATION_ENABLED=False,
        ANKI_MAX_IMAGE_GENERATIONS_PER_RUN=1,
        ANKI_IMAGE_MODEL="gpt-image-2",
        ANKI_IMAGE_SIZE="1536x1024",
        ANKI_IMAGE_QUALITY="low",
        ANKI_IMAGE_OUTPUT_FORMAT="png",
        ANKI_STYLE_CHARACTER_REFERENCE_IMAGE="assets/anki/character.png",
        ANKI_STYLE_DESIGN_REFERENCE_IMAGE="assets/anki/design.png",
        ANKI_STYLE_REFERENCE_VERSION="ppla-test",
    )


@pytest.mark.unit
def test_container_resolves_style_reference_paths_as_strings():
    container = ApplicationContainer()
    container.config.override(providers.Object(_fake_config()))

    graph = container.anki_generation_graph()

    assert graph.style_reference_images == [
        "assets/anki/character.png",
        "assets/anki/design.png",
    ]
    assert all(isinstance(path, str) for path in graph.style_reference_images)
    assert graph.style_reference_version == "ppla-test"
