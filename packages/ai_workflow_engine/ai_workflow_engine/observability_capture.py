"""Shared helpers for heavy observability detail capture."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Literal

from pydantic import BaseModel

from ai_workflow_engine._runtime_state import current_workflow_run_context
from ai_workflow_engine.models import (
    ObservationDetail,
    ObservationDetailKind,
    PrivacyLevel,
    WorkflowTraceEvent,
    WorkflowTraceSeverity,
)

logger = logging.getLogger(__name__)
CaptureMode = Literal["off", "full"]
_ENGINE_WORKER_OBSERVATION_ACTIVE: ContextVar[bool] = ContextVar(
    "engine_worker_observation_active",
    default=False,
)

_BINARY_FIELD_MARKERS = (
    "base64",
    "b64",
    "bytes",
    "byte_data",
    "image_data",
    "audio_data",
    "video_data",
    "data_uri",
)
_DATA_URI_RE = re.compile(r"^data:[^;,]+(?:;[^,]+)*;base64,", re.IGNORECASE)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_BASE64_MIN_CHARS = 512


class ObservationCapture:
    """Runtime-owned observability recorder.

    ``off`` keeps producers from writing drill-down details. ``full`` records full byte-free payloads
    plus digests behind compact trace events.
    """

    def __init__(
        self,
        trace_sink: Any,
        *,
        detail_sink: Any | None = None,
        mode: CaptureMode = "off",
    ) -> None:
        self.trace_sink = trace_sink
        self.detail_sink = detail_sink
        self.mode: CaptureMode = "full" if mode == "full" and detail_sink is not None else "off"

    @property
    def enabled(self) -> bool:
        return self.mode == "full" and self.detail_sink is not None

    def record(
        self,
        *,
        node: str,
        phase: str,
        kind: ObservationDetailKind,
        payload: dict[str, Any],
        attempt: int = 1,
        decision: str | None = None,
        severity: WorkflowTraceSeverity = "info",
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        digest_metadata_key: str | None = None,
        privacy: PrivacyLevel = "internal",
    ) -> tuple[WorkflowTraceEvent, ObservationDetail | None] | None:
        return record_observation(
            self.trace_sink,
            self.detail_sink if self.enabled else None,
            node=node,
            attempt=attempt,
            phase=phase,
            kind=kind,
            payload=payload,
            decision=decision,
            severity=severity,
            error=error,
            metadata=metadata,
            capture_text=self.enabled,
            privacy=privacy,
            digest_metadata_key=digest_metadata_key,
        )

    def record_detail(
        self,
        *,
        event_id: str,
        kind: ObservationDetailKind,
        payload: dict[str, Any],
        privacy: PrivacyLevel = "internal",
    ) -> ObservationDetail | None:
        if not self.enabled:
            return None
        return record_observation_detail(
            self.detail_sink,
            event_id=event_id,
            kind=kind,
            payload=payload,
            capture_text=True,
            privacy=privacy,
        )


@contextmanager
def engine_worker_observation_scope() -> Iterator[None]:
    """Mark the current call stack as already observed by an engine-owned LLM worker."""

    token = _ENGINE_WORKER_OBSERVATION_ACTIVE.set(True)
    try:
        yield
    finally:
        _ENGINE_WORKER_OBSERVATION_ACTIVE.reset(token)


def is_engine_worker_observation_active() -> bool:
    return _ENGINE_WORKER_OBSERVATION_ACTIVE.get()


def record_observation(
    trace_sink: Any,
    detail_sink: Any | None,
    *,
    node: str,
    phase: str,
    kind: ObservationDetailKind,
    payload: dict[str, Any],
    attempt: int = 1,
    decision: str | None = None,
    severity: WorkflowTraceSeverity = "info",
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
    capture_text: bool = False,
    privacy: PrivacyLevel = "internal",
    digest_metadata_key: str | None = None,
) -> tuple[WorkflowTraceEvent, ObservationDetail | None] | None:
    """Record a compact trace event plus a linked byte-free detail record.

    Sink write failures intentionally follow the configured sink contract. Producer-side projection
    failures are logged and skipped so observability bugs do not break the real workflow path.
    """

    if trace_sink is None:
        return None

    try:
        run_context = current_workflow_run_context()
        run_id = str(run_context.workflow_id) if run_context and run_context.workflow_id else None
        event_metadata = {"detail_kind": kind, **(metadata or {})}
        detail = None
        if detail_sink is not None and capture_text:
            safe_payload = byte_free(payload)
            digest = payload_digest(safe_payload)
            event_metadata["detail_digest"] = digest
            if digest_metadata_key:
                event_metadata[digest_metadata_key] = digest
        event_metadata = byte_free(event_metadata)
        event = WorkflowTraceEvent(
            node=node,
            attempt=attempt,
            decision=decision or phase,
            error=error,
            metadata=event_metadata,
            phase=phase,
            run_id=run_id,
            severity=severity,
        )
        if detail_sink is not None and capture_text:
            # Detail projection gets its OWN guard: a detail bug must cost the detail only —
            # dropping the whole trace EVENT with it would blind the run exactly when it
            # misbehaves (the audit's sharpening of the documented log-and-skip policy).
            try:
                detail = _build_observation_detail(
                    event_id=event.event_id,
                    run_id=run_id,
                    kind=kind,
                    payload=safe_payload,
                    digest=digest,
                    privacy=privacy,
                )
                event = event.model_copy(update={"detail_refs": [detail.detail_id]})
            except Exception:
                logger.warning(
                    "observation detail projection failed — recording bare trace event",
                    exc_info=True,
                )
                detail = None
    except Exception:
        logger.warning("observation detail projection failed", exc_info=True)
        return None

    if detail is not None:
        detail_sink.record(detail)
    trace_sink.record(event)
    return event, detail


def record_observation_detail(
    detail_sink: Any | None,
    *,
    event_id: str,
    kind: ObservationDetailKind,
    payload: dict[str, Any],
    capture_text: bool = False,
    privacy: PrivacyLevel = "internal",
) -> ObservationDetail | None:
    """Record only a detail payload for an already-owned trace event."""

    if detail_sink is None or not capture_text:
        return None

    try:
        run_context = current_workflow_run_context()
        run_id = str(run_context.workflow_id) if run_context and run_context.workflow_id else None
        safe_payload = byte_free(payload)
        detail = _build_observation_detail(
            event_id=event_id,
            run_id=run_id,
            kind=kind,
            payload=safe_payload,
            digest=payload_digest(safe_payload),
            privacy=privacy,
        )
    except Exception:
        logger.warning("observation detail projection failed", exc_info=True)
        return None

    detail_sink.record(detail)
    return detail


def payload_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def render_payload_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=True)


def render_detail_text(kind: ObservationDetailKind, payload: dict[str, Any]) -> str:
    if kind == "llm_response" and isinstance(payload.get("text"), str):
        return payload["text"]
    return render_payload_text(payload)


def llm_request_payload(request: Any) -> dict[str, Any]:
    return {
        "system": getattr(request, "system", None),
        "user": getattr(request, "user", ""),
        "messages": [byte_free(message) for message in getattr(request, "messages", [])],
        "images": [byte_free(image) for image in getattr(request, "images", [])],
        "tools": [byte_free(tool) for tool in getattr(request, "tools", [])],
        "tool_choice": getattr(request, "tool_choice", None),
    }


def llm_response_payload(response: Any) -> dict[str, Any]:
    return {
        "text": getattr(response, "text", ""),
        "tool_calls": [byte_free(call) for call in getattr(response, "tool_calls", [])],
        "stop_reason": getattr(response, "stop_reason", None),
        "model": getattr(response, "model", ""),
        "input_tokens": getattr(response, "input_tokens", 0),
        "output_tokens": getattr(response, "output_tokens", 0),
        "total_tokens": getattr(response, "total_tokens", 0),
        "estimated_usd": getattr(response, "estimated_usd", None),
        "cost_class": getattr(response, "cost_class", None),
        "notional_usd": getattr(response, "notional_usd", None),
        "metadata": byte_free(getattr(response, "metadata", {})),
    }


def langchain_messages_payload(messages: Any) -> dict[str, Any]:
    return {"messages": [byte_free(_langchain_message_payload(message)) for message in messages]}


def langchain_response_payload(response: Any, *, text: str) -> dict[str, Any]:
    return {
        "text": text,
        "raw_type": response.__class__.__name__,
        "content": byte_free(getattr(response, "content", response)),
        "response_metadata": byte_free(getattr(response, "response_metadata", {})),
    }


def byte_free(value: Any, *, field_name: str | None = None) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "type": "bytes",
            "byte_length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    fingerprint = getattr(value, "fingerprint", None)
    if callable(fingerprint):
        try:
            return {"type": value.__class__.__name__, "fingerprint": byte_free(fingerprint())}
        except Exception:
            logger.warning("observation fingerprint projection failed", exc_info=True)
    if isinstance(value, BaseModel):
        return {
            (field.alias or name): byte_free(getattr(value, name), field_name=(field.alias or name))
            for name, field in type(value).model_fields.items()
        }
    if isinstance(value, dict):
        return {str(key): byte_free(item, field_name=str(key)) for key, item in value.items()}
    if isinstance(value, set):
        return [byte_free(item) for item in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [byte_free(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return _redact_encoded_binary_string(value, field_name=field_name)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return repr(value)


def _langchain_message_payload(message: Any) -> dict[str, Any]:
    return {
        "type": message.__class__.__name__,
        "content": getattr(message, "content", message),
    }


def _build_observation_detail(
    *,
    event_id: str,
    run_id: str | None,
    kind: ObservationDetailKind,
    payload: dict[str, Any],
    digest: str,
    privacy: PrivacyLevel,
) -> ObservationDetail:
    return ObservationDetail(
        event_id=event_id,
        run_id=run_id,
        kind=kind,
        privacy=privacy,
        redaction_state="none",
        content_type="application/json",
        text=render_detail_text(kind, payload),
        json_value=payload,
        digest=digest,
    )


def _redact_encoded_binary_string(value: str, *, field_name: str | None = None) -> Any:
    normalized_field = (field_name or "").lower()
    if _DATA_URI_RE.match(value):
        return _encoded_string_fingerprint("data_uri", value)
    if any(marker in normalized_field for marker in _BINARY_FIELD_MARKERS) and len(value) >= 64:
        return _encoded_string_fingerprint("encoded_binary_string", value)
    compact = "".join(value.split())
    if (
        len(compact) >= _BASE64_MIN_CHARS
        and len(compact) % 4 == 0
        and _BASE64_RE.match(compact)
    ):
        return _encoded_string_fingerprint("base64_like_string", value)
    return value


def _encoded_string_fingerprint(kind: str, value: str) -> dict[str, Any]:
    return {
        "type": kind,
        "char_length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }
