"""B3 red battery (product side): Anki continuity + idempotency derivation.

Secured-consumer iteration. The Anki graph owns the PRODUCT MEANING of continuity and
operation idempotency (the adapter only maps fields to the browser API):

- canonical continuity input: ``anki|v1|user:<user_id>|style:<style_version-or-none>``;
- the wire name is bounded and derived: ``anki-<first-32-hex-of-sha256(input)>`` — the
  raw user id NEVER appears in provider-facing identity;
- same user/style is stable across runs; different user OR style changes the name;
- one image operation key: workflow run id + fixed operation slot — stable across a
  transport retry of the SAME operation, different for a new run or another slot.

Target home: ``services/content/anki_media_contract.py`` (``continuity_name``,
``image_idempotency_key``). These tests were captured RED before implementation; the
graph-propagation half is fenced separately at the real graph boundary.
"""

from __future__ import annotations

import hashlib

import pytest

pytestmark = pytest.mark.unit

def test_continuity_name_is_bounded_derived_and_private():
    from services.content.anki_media_contract import continuity_name

    name = continuity_name(user_id=123456789, style_version="ppla-split-v3")
    expected_input = "anki|v1|user:123456789|style:ppla-split-v3"
    expected = "anki-" + hashlib.sha256(expected_input.encode("utf-8")).hexdigest()[:32]
    assert name == expected
    assert "123456789" not in name, "the raw user id must never reach provider identity"
    assert len(name) <= 40, "the wire name is bounded"


def test_continuity_name_handles_absent_style_and_isolates_users_and_styles():
    from services.content.anki_media_contract import continuity_name

    no_style = continuity_name(user_id=1, style_version=None)
    expected_input = "anki|v1|user:1|style:none"
    assert no_style == "anki-" + hashlib.sha256(expected_input.encode()).hexdigest()[:32]

    base = continuity_name(user_id=1, style_version="v3")
    assert continuity_name(user_id=1, style_version="v3") == base, "stable across runs"
    assert continuity_name(user_id=2, style_version="v3") != base, "user isolation"
    assert continuity_name(user_id=1, style_version="v4") != base, "style isolation"


def test_image_idempotency_key_is_stable_per_operation_and_new_per_run():
    from services.content.anki_media_contract import image_idempotency_key

    key = image_idempotency_key(workflow_id="run-77", operation_slot=0)
    assert key == image_idempotency_key(workflow_id="run-77", operation_slot=0), (
        "the SAME operation's transport retry keeps the SAME key"
    )
    assert key != image_idempotency_key(workflow_id="run-78", operation_slot=0), (
        "an intentional new run gets a new key"
    )
    assert key != image_idempotency_key(workflow_id="run-77", operation_slot=1), (
        "a second image operation in one run is a different operation"
    )
    assert "run-77" in key or len(key) >= 16, "the key must be traceable and non-trivial"
