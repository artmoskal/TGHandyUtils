"""Shared byte-free validation for prompt memory and persisted workflow state."""

from __future__ import annotations

import numbers
from collections.abc import Iterator, Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from functools import partial
from ipaddress import IPv4Address, IPv6Address
from pathlib import PurePath
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

ByteSafetyMode = Literal["prompt", "persist"]

_EXACT_PRIMITIVES = (type(None), bool, int, float, str)
_ALLOWED_VALUE_LEAVES = (
    datetime,
    date,
    time,
    timedelta,
    UUID,
    Decimal,
    Fraction,
    complex,
    PurePath,
    IPv4Address,
    IPv6Address,
)


def assert_byte_safe(value: Any, *, mode: ByteSafetyMode, path: str = "state") -> None:
    """Fail loudly if ``value`` can carry raw bytes/media or unsafe prompt state."""

    _assert_byte_safe(value, mode=mode, path=path, seen=set())


def assert_prompt_text_safe(text: str, *, path: str = "rendered") -> None:
    """Reject raw transport data URIs in text that will be sent to a model."""

    if is_transport_data_uri(text):
        raise ValueError(f"{path} contains data URI base64 media")


def is_transport_data_uri(text: str) -> bool:
    """Return true only when the string itself is a base64 data URI."""

    stripped = text.lstrip()
    if not stripped.lower().startswith("data:"):
        return False
    header, separator, _payload = stripped.partition(",")
    if not separator:
        return False
    parts = header[5:].split(";")
    return any(part.strip().lower() == "base64" for part in parts[1:])


def redact_transport_data_uris(value: Any) -> Any:
    """Return a JSON-like value with transport data URI strings summarized."""

    if isinstance(value, str):
        return _redact_data_uri(value) if is_transport_data_uri(value) else value
    if isinstance(value, Mapping):
        return {
            str(key): redact_transport_data_uris(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_transport_data_uris(item) for item in value]
    return value


def _assert_byte_safe(
    value: Any,
    *,
    mode: ByteSafetyMode,
    path: str,
    seen: set[int],
) -> None:
    value_type = type(value)
    if value_type in _EXACT_PRIMITIVES:
        if value_type is str and mode == "prompt":
            assert_prompt_text_safe(value, path=path)
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError(f"{path} contains raw bytes")
    if _is_image_input(value):
        raise ValueError(f"{path} contains ImageInput transport payload")

    object_id = id(value)
    if object_id in seen:
        return
    seen.add(object_id)

    if isinstance(value, Iterator):
        raise ValueError(f"{path} is a non-replayable iterator/generator")
    if isinstance(value, BaseModel):
        _walk_pydantic_model(value, mode=mode, path=path, seen=seen)
        return
    if is_dataclass(value) and not isinstance(value, type):
        _walk_dataclass(value, mode=mode, path=path, seen=seen)
        return
    if isinstance(value, Enum):
        _assert_byte_safe(value.value, mode=mode, path=f"{path}.value", seen=seen)
        return
    if isinstance(value, BaseException):
        _assert_byte_safe(value.args, mode=mode, path=f"{path}.args", seen=seen)
        _walk_instance_attributes(value, mode=mode, path=path, seen=seen)
        return
    if isinstance(value, partial):
        _assert_byte_safe(value.args, mode=mode, path=f"{path}.args", seen=seen)
        _assert_byte_safe(value.keywords or {}, mode=mode, path=f"{path}.keywords", seen=seen)
        _walk_instance_attributes(value, mode=mode, path=path, seen=seen)
        return
    if isinstance(value, Mapping):
        _walk_mapping(value, mode=mode, path=path, seen=seen)
        return
    if isinstance(value, (set, frozenset)):
        _walk_iterable(value, mode=mode, path=path, seen=seen)
        _walk_instance_attributes(value, mode=mode, path=path, seen=seen)
        return
    if isinstance(value, range):
        # A range can only ever yield ints and cannot be subclassed or carry attributes —
        # validating it is O(1); walking a range(5_000_000) element-by-element stalls runs.
        return
    if isinstance(value, (list, tuple)):
        _walk_iterable(value, mode=mode, path=path, seen=seen)
        _walk_instance_attributes(value, mode=mode, path=path, seen=seen)
        return

    had_attrs = _walk_instance_attributes(value, mode=mode, path=path, seen=seen)
    if isinstance(value, str):
        if mode == "prompt":
            assert_prompt_text_safe(str(value), path=path)
        return
    if isinstance(value, _ALLOWED_VALUE_LEAVES) or isinstance(value, numbers.Number):
        return
    if not had_attrs:
        raise ValueError(
            f"{path} is unsupported prompt/checkpoint state type {value_type.__name__}; "
            "emit JSON-like scalars, containers, or EvidenceRefs instead"
        )


def _walk_mapping(
    value: Mapping[Any, Any],
    *,
    mode: ByteSafetyMode,
    path: str,
    seen: set[int],
) -> None:
    for index, (key, item) in enumerate(value.items()):
        key_path = f"{path}.<key {index}>"
        _assert_byte_safe(key, mode=mode, path=key_path, seen=seen)
        _assert_byte_safe(item, mode=mode, path=f"{path}.{_key_label(key, index)}", seen=seen)
    _walk_instance_attributes(value, mode=mode, path=path, seen=seen)


def _walk_iterable(
    value: Any,
    *,
    mode: ByteSafetyMode,
    path: str,
    seen: set[int],
) -> None:
    for index, item in enumerate(value):
        _assert_byte_safe(item, mode=mode, path=f"{path}[{index}]", seen=seen)


def _walk_pydantic_model(
    value: BaseModel,
    *,
    mode: ByteSafetyMode,
    path: str,
    seen: set[int],
) -> None:
    for name in value.__class__.model_fields:
        _assert_byte_safe(
            _safe_getattr(value, name, path=f"{path}.{name}"),
            mode=mode,
            path=f"{path}.{name}",
            seen=seen,
        )
    extra = getattr(value, "__pydantic_extra__", None)
    if isinstance(extra, Mapping):
        for key, item in extra.items():
            _assert_byte_safe(item, mode=mode, path=f"{path}.{_key_label(key, 0)}", seen=seen)
    private = getattr(value, "__pydantic_private__", None)
    if isinstance(private, Mapping):
        for key, item in private.items():
            _assert_byte_safe(item, mode=mode, path=f"{path}.{_key_label(key, 0)}", seen=seen)
    computed = getattr(value.__class__, "model_computed_fields", {}) or {}
    for name in computed:
        _assert_byte_safe(
            _safe_getattr(value, name, path=f"{path}.{name}"),
            mode=mode,
            path=f"{path}.{name}",
            seen=seen,
        )


def _walk_dataclass(
    value: Any,
    *,
    mode: ByteSafetyMode,
    path: str,
    seen: set[int],
) -> None:
    field_names = set()
    for field in fields(value):
        field_names.add(field.name)
        _assert_byte_safe(
            _safe_getattr(value, field.name, path=f"{path}.{field.name}"),
            mode=mode,
            path=f"{path}.{field.name}",
            seen=seen,
        )
    _walk_instance_attributes(value, mode=mode, path=path, seen=seen, exclude=field_names)


def _walk_instance_attributes(
    value: Any,
    *,
    mode: ByteSafetyMode,
    path: str,
    seen: set[int],
    exclude: set[str] | None = None,
) -> bool:
    found = False
    for name, item in _iter_instance_attributes(value, path=path, exclude=exclude or set()):
        found = True
        _assert_byte_safe(item, mode=mode, path=f"{path}.{name}", seen=seen)
    return found


def _iter_instance_attributes(
    value: Any,
    *,
    path: str,
    exclude: set[str],
) -> list[tuple[str, Any]]:
    attributes: dict[str, Any] = {}
    try:
        instance_dict = getattr(value, "__dict__", None)
    except Exception as exc:  # pragma: no cover - unusual descriptor path
        raise ValueError(f"{path}.__dict__ could not be inspected: {exc}") from exc
    if isinstance(instance_dict, Mapping):
        for name, item in instance_dict.items():
            if isinstance(name, str) and name not in exclude:
                attributes[name] = item

    for klass in type(value).__mro__:
        slots = getattr(klass, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots:
            if not isinstance(slot, str):
                continue
            if slot in ("__dict__", "__weakref__") or slot in attributes or slot in exclude:
                continue
            slot_path = f"{path}.{slot}"
            try:
                attributes[slot] = getattr(value, slot)
            except AttributeError:
                continue
            except Exception as exc:
                raise ValueError(f"{slot_path} could not be inspected: {exc}") from exc
    return list(attributes.items())


def _safe_getattr(value: Any, name: str, *, path: str) -> Any:
    try:
        return getattr(value, name)
    except AttributeError as exc:
        raise ValueError(f"{path} is missing") from exc
    except Exception as exc:
        raise ValueError(f"{path} could not be inspected: {exc}") from exc


def _key_label(key: Any, index: int) -> str:
    if type(key) is str and key:
        return key[:80]
    return f"<value {index}>"


def _redact_data_uri(text: str) -> str:
    stripped = text.lstrip()
    header, _separator, payload = stripped.partition(",")
    media_type = header[5:].split(";", 1)[0] or "unknown"
    return f"<data-uri redacted media_type={media_type} chars={len(payload)}>"


def _is_image_input(value: Any) -> bool:
    # Call-time import: a module-level one would cycle (vision → engine → checkpoints → here).
    # isinstance (not a name check) so ImageInput SUBCLASSES cannot smuggle transport payloads.
    from ai_workflow_engine.vision import ImageInput

    return isinstance(value, ImageInput)
