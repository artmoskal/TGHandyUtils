import array
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from functools import partial
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel, PrivateAttr, computed_field

from ai_workflow_engine.byte_safety import (
    assert_byte_safe,
    is_transport_data_uri,
    redact_transport_data_uris,
)
from ai_workflow_engine.models import EvidenceRef
from ai_workflow_engine.vision import ImageInput

pytestmark = pytest.mark.unit


def _image() -> ImageInput:
    return ImageInput(source="base64", data="aGk=", media_type="image/png")


def test_byte_safety_rejects_raw_transport_payloads_in_prompt_and_persist_modes():
    for mode in ("prompt", "persist"):
        with pytest.raises(ValueError, match="state contains raw bytes"):
            assert_byte_safe(b"raw", mode=mode)
        with pytest.raises(ValueError, match="ImageInput"):
            assert_byte_safe(_image(), mode=mode)


def test_byte_safety_validates_mapping_keys_values_and_subclass_attributes():
    with pytest.raises(ValueError, match=r"state\.<key 0> contains raw bytes"):
        assert_byte_safe({b"raw": "value"}, mode="prompt")

    class TaggedDict(dict):
        pass

    tagged = TaggedDict({"ok": True})
    tagged.hidden = b"raw"
    with pytest.raises(ValueError, match="state.hidden contains raw bytes"):
        assert_byte_safe(tagged, mode="prompt")

    class TaggedInt(int):
        pass

    number = TaggedInt(7)
    number.hidden = b"raw"
    with pytest.raises(ValueError, match="state.hidden contains raw bytes"):
        assert_byte_safe(number, mode="prompt")

    class TaggedStr(str):
        pass

    clean = TaggedStr("safe")
    assert_byte_safe(clean, mode="prompt")
    dirty = TaggedStr("safe")
    dirty.hidden = b"raw"
    with pytest.raises(ValueError, match="state.hidden contains raw bytes"):
        assert_byte_safe(dirty, mode="prompt")


def test_byte_safety_rejects_non_replayable_and_array_like_state():
    with pytest.raises(ValueError, match="non-replayable"):
        assert_byte_safe((item for item in [b"raw"]), mode="prompt")

    values = {"x": b"raw"}.values()
    with pytest.raises(ValueError, match="unsupported"):
        assert_byte_safe(values, mode="prompt")

    with pytest.raises(ValueError, match="unsupported"):
        assert_byte_safe(array.array("B", b"raw"), mode="prompt")


def test_byte_safety_walks_dataclass_non_field_attrs_and_pydantic_computed_values():
    @dataclass
    class Finding:
        title: str

        def __post_init__(self):
            self.raw = b"raw"

    with pytest.raises(ValueError, match="state.raw contains raw bytes"):
        assert_byte_safe(Finding("bad"), mode="prompt")

    class ModelWithComputedBytes(BaseModel):
        x: int = 1

        @computed_field
        @property
        def raw(self) -> bytes:
            return b"raw"

    with pytest.raises(ValueError, match="state.raw contains raw bytes"):
        assert_byte_safe(ModelWithComputedBytes(), mode="prompt")

    class ModelWithPrivateBytes(BaseModel):
        x: int = 1
        _raw: bytes = PrivateAttr(default=b"raw")

    with pytest.raises(ValueError, match="raw bytes"):
        assert_byte_safe(ModelWithPrivateBytes(), mode="prompt")


def test_byte_safety_converts_descriptor_failures_to_path_specific_value_errors():
    class Base:
        __slots__ = ("bad",)

    class Child(Base):
        @property
        def bad(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match=r"state\.bad could not be inspected: boom"):
        assert_byte_safe(Child(), mode="prompt")


def test_byte_safety_walks_wrapper_payloads_before_allowing_values():
    class Wrapped(Enum):
        RAW = b"raw"
        CLEAN = "clean"

    with pytest.raises(ValueError, match=r"state\.value contains raw bytes"):
        assert_byte_safe(Wrapped.RAW, mode="prompt")
    assert_byte_safe(Wrapped.CLEAN, mode="prompt")

    with pytest.raises(ValueError, match=r"state\.args\[0\] contains raw bytes"):
        assert_byte_safe(ValueError(b"raw"), mode="prompt")

    with pytest.raises(ValueError, match="ImageInput"):
        assert_byte_safe(partial(lambda image: image, _image()), mode="prompt")


def test_byte_safety_rejects_opaque_objects_even_with_custom_repr():
    class Empty:
        pass

    class ReprOnly:
        __slots__ = ()

        def __repr__(self) -> str:
            return f"ReprOnly({hex(id(self))})"

    with pytest.raises(ValueError, match="unsupported"):
        assert_byte_safe(Empty(), mode="prompt")
    with pytest.raises(ValueError, match="unsupported"):
        assert_byte_safe(ReprOnly(), mode="prompt")


def test_byte_safety_allows_explicit_value_types_and_refs():
    assert_byte_safe(
        {
            "when": datetime(2026, 7, 7, 12, 0, 0),
            "amount": Decimal("1.25"),
            "id": UUID("123e4567-e89b-12d3-a456-426614174000"),
            "path": Path("frames/1.png"),
            "ref": EvidenceRef(ref_id="frame-1", role="frame", uri="evidence://frame-1"),
        },
        mode="prompt",
    )


def test_byte_safety_numpy_scalar_passes_without_allowing_ndarray():
    pytest.importorskip("numpy")
    import numpy as np

    assert_byte_safe(np.float64(0.9), mode="prompt")
    with pytest.raises(ValueError, match="unsupported"):
        assert_byte_safe(np.array([1, 2, 3]), mode="prompt")


def test_data_uri_detection_is_anchored_fast_and_prompt_only():
    assert is_transport_data_uri("data:image/png;base64,AAAA")
    assert is_transport_data_uri("data:;base64,X")
    assert not is_transport_data_uri("see data:image/png;base64,AAAA in the page")

    adversarial = "data:x" + ";x" * 1000
    started = time.perf_counter()
    assert not is_transport_data_uri(adversarial)
    assert time.perf_counter() - started < 0.05

    with pytest.raises(ValueError, match="data URI"):
        assert_byte_safe("data:image/png;base64,AAAA", mode="prompt")
    assert_byte_safe("data:image/png;base64,AAAA", mode="persist")


def test_transport_data_uri_redaction_preserves_tool_arg_shape_without_raw_payload():
    payload = {
        "image_url": "data:image/png;base64,abcdef",
        "nested": [{"url": "data:;base64,XYZ"}],
    }

    redacted = redact_transport_data_uris(payload)

    assert redacted["image_url"] == "<data-uri redacted media_type=image/png chars=6>"
    assert redacted["nested"][0]["url"] == "<data-uri redacted media_type=unknown chars=3>"
    assert "abcdef" not in str(redacted)
    assert not is_transport_data_uri(redacted["image_url"])
    assert_byte_safe(redacted, mode="prompt")
