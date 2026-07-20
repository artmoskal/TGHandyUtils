"""Cross-record integrity checks for provider invocation evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from ai_workflow_engine.models import (
    ObservationDetail,
    WorkflowTraceEvent,
    WorkflowUsageEvent,
)

_PROVIDER_TRACE_PHASES = frozenset(
    {"llm:request", "llm:response", "provider:request", "provider:response"}
)


def validate_provider_invocation_links(
    traces: Iterable[WorkflowTraceEvent],
    details: Iterable[ObservationDetail],
    usage_events: Iterable[WorkflowUsageEvent],
) -> None:
    """Require each recorded provider attempt to be a self-contained evidence graph."""

    traces_by_id: dict[str, list[WorkflowTraceEvent]] = defaultdict(list)
    details_by_id: dict[str, list[ObservationDetail]] = defaultdict(list)
    usage_by_id: dict[str, list[WorkflowUsageEvent]] = defaultdict(list)
    details_by_detail_id: dict[str, ObservationDetail] = {}

    for trace in traces:
        if trace.phase in _PROVIDER_TRACE_PHASES and trace.invocation_id is None:
            raise ValueError(
                f"provider trace event {trace.event_id!r} at phase {trace.phase!r} "
                "is missing its stable invocation_id"
            )
        if trace.invocation_id is not None:
            traces_by_id[str(trace.invocation_id)].append(trace)
    for detail in details:
        if detail.detail_id in details_by_detail_id:
            raise ValueError(
                f"provider evidence contains duplicate detail_id {detail.detail_id!r}"
            )
        details_by_detail_id[detail.detail_id] = detail
        if detail.invocation_id is not None:
            details_by_id[str(detail.invocation_id)].append(detail)
    for event in usage_events:
        if event.invocation_id is not None:
            usage_by_id[str(event.invocation_id)].append(event)

    invocation_ids = set(traces_by_id) | set(details_by_id) | set(usage_by_id)
    for invocation_id in sorted(invocation_ids):
        linked_usage = usage_by_id.get(invocation_id, [])
        if len(linked_usage) != 1:
            raise ValueError(
                f"provider invocation {invocation_id!r} must have exactly one usage event; "
                f"found {len(linked_usage)}"
            )
        linked_traces = traces_by_id.get(invocation_id, [])
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
                detail = details_by_detail_id.get(detail_id)
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
            detail.detail_id for detail in details_by_id.get(invocation_id, [])
        } - referenced_details
        if unreferenced:
            raise ValueError(
                f"provider invocation {invocation_id!r} has unreferenced details: "
                f"{sorted(unreferenced)!r}"
            )


def _is_request(phase: str | None) -> bool:
    return bool(phase and phase.endswith(":request"))


def _is_terminal(phase: str | None) -> bool:
    return bool(
        phase
        and (
            phase.endswith(":response")
            or phase == "tool:result"
        )
    )


__all__ = ["validate_provider_invocation_links"]
