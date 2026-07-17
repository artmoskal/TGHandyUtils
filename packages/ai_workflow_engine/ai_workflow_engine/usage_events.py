"""Usage-event sinks and the single event-recording entry point."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Protocol

from ai_workflow_engine.budget import (
    _enforce_per_call_budget,
    _enforce_usd_budget,
    current_usage_context,
)
from ai_workflow_engine.models import WorkflowUsageEvent

logger = logging.getLogger(__name__)


# C2 (invocation-local attribution): a PRIVATE stack of capture buckets. Capture OBSERVES,
# it never accounts: every active bucket in the current task receives each enriched event
# exactly once, while the canonical path (summary aggregation, sink fanout, log, budgets)
# runs unchanged exactly once. The stack is a tuple ContextVar, so asyncio task copies
# isolate sibling fanout items and concurrent runs; nesting is INCLUSIVE (an outer
# capability's bucket also sees nested provider work). Buckets store only the already-
# existing usage event object — no prompt/result payload is retained.
_ACTIVE_USAGE_CAPTURES: ContextVar[tuple[list[WorkflowUsageEvent], ...]] = ContextVar(
    "workflow_usage_captures",
    default=(),
)


@contextmanager
def capture_usage_events() -> Iterator[list[WorkflowUsageEvent]]:
    """Collect the usage events recorded by the CURRENT task while the scope is active.

    The single supported consumer is invocation-local attribution (``invoke_bound``'s
    ``model_binding`` truth). Token reset restores the previous stack on every exit path —
    success, exception, budget refusal, timeout, cancellation."""

    bucket: list[WorkflowUsageEvent] = []
    token = _ACTIVE_USAGE_CAPTURES.set((*_ACTIVE_USAGE_CAPTURES.get(), bucket))
    try:
        yield bucket
    finally:
        _ACTIVE_USAGE_CAPTURES.reset(token)


def _publish_to_captures(event: WorkflowUsageEvent) -> None:
    for bucket in _ACTIVE_USAGE_CAPTURES.get():
        bucket.append(event)


class UsageSink(Protocol):
    """Receives usage events from the runtime."""

    def record(self, event: WorkflowUsageEvent) -> None:
        """Store one usage event."""


class InMemoryUsageSink:
    """Simple usage sink suitable for tests and short in-process runs."""

    def __init__(self) -> None:
        self.events: list[WorkflowUsageEvent] = []

    def record(self, event: WorkflowUsageEvent) -> None:
        self.events.append(event)


class JsonlUsageSink:
    """Append usage events to a JSONL file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: WorkflowUsageEvent) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json())
            fh.write("\n")


class AsyncQueueUsageSink:
    """Non-blocking asyncio.Queue usage feed for live observers."""

    def __init__(
        self,
        queue: asyncio.Queue[WorkflowUsageEvent] | None = None,
        *,
        maxsize: int = 1000,
    ) -> None:
        self.queue = queue or asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def record(self, event: WorkflowUsageEvent) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                dropped = False
            else:
                self.queue.task_done()
                dropped = True
            if dropped:
                self.dropped += 1
        self.queue.put_nowait(event)


class TeeUsageSink:
    """Fan each usage event out to multiple sinks."""

    def __init__(self, *sinks: UsageSink) -> None:
        self.sinks = list(sinks)

    def record(self, event: WorkflowUsageEvent) -> None:
        for sink in self.sinks:
            sink.record(event)


def record_usage_event(event: WorkflowUsageEvent) -> None:
    context = current_usage_context()
    if context:
        if event.run_id is None:
            event.run_id = context.run_context.workflow_id
        event.metadata.setdefault("run_id", context.run_context.workflow_id)
        event.metadata.setdefault("workflow_id", context.run_context.workflow_id)
        event.metadata.setdefault("workflow_type", context.run_context.workflow_type)
        event.metadata.setdefault("user_id", context.run_context.user_id)
        from ai_workflow_engine.correlation import enrich_related_run

        # Related-run id: enriched ONCE here, BEFORE summary aggregation and sink fanout,
        # through the SAME type-strict rule every event surface uses (int 42 never
        # "matches" "42"; conflicts refuse before persistence).
        event.metadata = enrich_related_run(
            event.metadata,
            context.run_context.correlation_id,
            surface="usage",
            event_id=event.event_id,
        ) or event.metadata
        # C2: captures observe the SAME semantic event the ledger persists — published after
        # run/correlation enrichment, before canonical aggregation; never accounted twice.
        _publish_to_captures(event)
        context.summary.add_event(event)
        if context.usage_sink is not None:
            context.usage_sink.record(event)
        logger.info("workflow_usage %s", json.dumps(event.model_dump(), sort_keys=True, default=str))
        _enforce_per_call_budget(event, context)
        _enforce_usd_budget(context)
        return
    _publish_to_captures(event)
    logger.info("workflow_usage %s", json.dumps(event.model_dump(), sort_keys=True, default=str))

