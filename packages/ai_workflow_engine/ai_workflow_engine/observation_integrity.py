"""Cross-record integrity checks for provider invocation evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


_PROVIDER_TRACE_PHASES = frozenset(
    {"llm:request", "llm:response", "provider:request", "provider:response"}
)


@dataclass(frozen=True)
class _TraceLink:
    event_id: str
    phase: str | None
    detail_capture: str | None
    detail_refs: tuple[str, ...]


@dataclass(frozen=True)
class _DetailLink:
    detail_id: str
    event_id: str
    invocation_id: str | None


class ProviderEvidenceValidator:
    """Incremental linkage validator retaining identities, never detail bodies or models."""

    def __init__(self) -> None:
        self._traces: dict[str, list[_TraceLink]] = defaultdict(list)
        self._details: dict[str, list[_DetailLink]] = defaultdict(list)
        self._usage_counts: dict[str, int] = defaultdict(int)
        self._details_by_id: dict[str, _DetailLink] = {}

    def record_trace(self, trace: Any) -> None:
        if trace.phase in _PROVIDER_TRACE_PHASES and trace.invocation_id is None:
            raise ValueError(
                f"provider trace event {trace.event_id!r} at phase {trace.phase!r} "
                "is missing its stable invocation_id"
            )
        if trace.invocation_id is None:
            return
        self._traces[str(trace.invocation_id)].append(
            _TraceLink(
                event_id=str(trace.event_id),
                phase=trace.phase,
                detail_capture=trace.detail_capture,
                detail_refs=tuple(trace.detail_refs),
            )
        )

    def record_detail(self, detail: Any) -> None:
        if detail.detail_id in self._details_by_id:
            raise ValueError(
                f"provider evidence contains duplicate detail_id {detail.detail_id!r}"
            )
        link = _DetailLink(
            detail_id=str(detail.detail_id),
            event_id=str(detail.event_id),
            invocation_id=(str(detail.invocation_id) if detail.invocation_id is not None else None),
        )
        self._details_by_id[link.detail_id] = link
        if link.invocation_id is not None:
            self._details[link.invocation_id].append(link)

    def record_usage(self, event: Any) -> None:
        if event.invocation_id is not None:
            self._usage_counts[str(event.invocation_id)] += 1

    def validate(self) -> None:
        invocation_ids = set(self._traces) | set(self._details) | set(self._usage_counts)
        for invocation_id in sorted(invocation_ids):
            usage_count = self._usage_counts.get(invocation_id, 0)
            if usage_count != 1:
                raise ValueError(
                    f"provider invocation {invocation_id!r} must have exactly one usage event; "
                    f"found {usage_count}"
                )
            linked_traces = self._traces.get(invocation_id, [])
            if not linked_traces:
                raise ValueError(
                    f"provider invocation {invocation_id!r} has usage but no trace events"
                )
            request_count = sum(_is_request(trace.phase) for trace in linked_traces)
            if request_count != 1:
                raise ValueError(
                    f"provider invocation {invocation_id!r} must have exactly one request "
                    f"trace; found {request_count}"
                )
            if not any(_is_terminal(trace.phase) for trace in linked_traces):
                raise ValueError(
                    f"provider invocation {invocation_id!r} has no response/tool-result trace"
                )
            self._validate_detail_links(invocation_id, linked_traces)

    def _validate_detail_links(
        self,
        invocation_id: str,
        linked_traces: list[_TraceLink],
    ) -> None:
        referenced_details: set[str] = set()
        for trace in linked_traces:
            if trace.detail_capture is None:
                raise ValueError(
                    f"provider invocation {invocation_id!r} trace {trace.event_id!r} "
                    "does not record detail_capture truth"
                )
            if trace.detail_capture == "captured":
                if not trace.detail_refs:
                    raise ValueError(
                        f"provider invocation {invocation_id!r} trace {trace.event_id!r} "
                        "claims captured details but has no detail_refs"
                    )
            elif trace.detail_refs:
                raise ValueError(
                    f"provider invocation {invocation_id!r} trace {trace.event_id!r} "
                    f"claims {trace.detail_capture!r} but still carries detail_refs"
                )
            for detail_id in trace.detail_refs:
                detail = self._details_by_id.get(detail_id)
                if detail is None:
                    raise ValueError(
                        f"provider invocation {invocation_id!r} references missing detail "
                        f"{detail_id!r}"
                    )
                if detail.invocation_id != invocation_id:
                    raise ValueError(
                        f"provider invocation {invocation_id!r} trace/detail identity "
                        f"mismatch at detail {detail_id!r}"
                    )
                if detail.event_id != trace.event_id:
                    raise ValueError(
                        f"provider invocation {invocation_id!r} detail {detail_id!r} "
                        "does not belong to its referencing trace event"
                    )
                if detail_id in referenced_details:
                    raise ValueError(
                        f"provider invocation {invocation_id!r} detail {detail_id!r} "
                        "is referenced by more than one trace event"
                    )
                referenced_details.add(detail_id)
        unreferenced = {
            detail.detail_id for detail in self._details.get(invocation_id, [])
        } - referenced_details
        if unreferenced:
            raise ValueError(
                f"provider invocation {invocation_id!r} has unreferenced details: "
                f"{sorted(unreferenced)!r}"
            )


def validate_provider_invocation_links(
    traces: Iterable[Any],
    details: Iterable[Any],
    usage_events: Iterable[Any],
) -> None:
    """Require each recorded provider attempt to be a self-contained evidence graph."""

    validator = ProviderEvidenceValidator()
    for trace in traces:
        validator.record_trace(trace)
    for detail in details:
        validator.record_detail(detail)
    for event in usage_events:
        validator.record_usage(event)
    validator.validate()


def _is_request(phase: str | None) -> bool:
    return bool(phase and phase.endswith(":request"))


def _is_terminal(phase: str | None) -> bool:
    return bool(phase and (phase.endswith(":response") or phase == "tool:result"))


__all__ = ["ProviderEvidenceValidator", "validate_provider_invocation_links"]
