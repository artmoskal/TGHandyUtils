"""Generic capability runtime for reusable workflow execution."""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
import time
from typing import Any, Awaitable, Callable, Iterable, NamedTuple, Optional, Protocol


from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    CapabilitySpec,
    ObservationDetail,
    RuntimePlan,
    RuntimeLimits,
    WorkflowArtifact,
    WorkflowGoal,
    WorkflowProfile,
    WorkflowRunContext,
    WorkflowTraceEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine._runtime_state import current_run_session, current_workflow_run_context
from ai_workflow_engine.engine.capability_contract import (
    denied_side_effects,
    handler_is_async,
    normalize_result,
    validate_payload,
)
from ai_workflow_engine.engine.invocation_supervision import (
    CancellationContainmentError,
    ExecutionTimeout,
    published_invocation_scope,
    resolve_invocation_window,
    supervise_awaitable,
)
from ai_workflow_engine.engine.capability_observation import CapabilityObservationProjector
from ai_workflow_engine.observability_capture import ObservationCapture
from ai_workflow_engine.budget import check_budget_before_call

CapabilityHandler = Callable[[CapabilityContext, Any], Any]
logger = logging.getLogger(__name__)


class CapabilityCall(NamedTuple):
    name: str
    payload: Any
    attempt: int = 1


class TraceSink(Protocol):
    """Receives trace events from the runtime."""

    def record(self, event: WorkflowTraceEvent) -> None:
        """Store one trace event."""


class DetailSink(Protocol):
    """Receives heavy/private observability detail records."""

    def record(self, detail: ObservationDetail) -> None:
        """Store one detail record."""

    def clear(self) -> None:
        """Drop buffered detail records when the sink supports run-local retention."""


class InMemoryTraceSink:
    """Simple trace sink suitable for tests and short in-process runs."""

    def __init__(self) -> None:
        self.events: list[WorkflowTraceEvent] = []

    def record(self, event: WorkflowTraceEvent) -> None:
        self.events.append(event)


class InMemoryDetailSink:
    """Simple detail sink suitable for tests and short debug runs."""

    def __init__(self) -> None:
        self.details: list[ObservationDetail] = []

    def record(self, detail: ObservationDetail) -> None:
        self.details.append(detail)

    def clear(self) -> None:
        self.details.clear()


class JsonlTraceSink:
    """Append trace events to a JSONL file for handoff/debuggable workflow runs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: WorkflowTraceEvent) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json())
            fh.write("\n")


class CallbackTraceSink:
    """Forward trace events to a callback without letting callback errors stop the run."""

    def __init__(self, callback: Callable[[WorkflowTraceEvent], None]) -> None:
        self.callback = callback

    def record(self, event: WorkflowTraceEvent) -> None:
        try:
            self.callback(event)
        except Exception:
            logger.warning("trace sink callback failed", exc_info=True)


class ContextEnrichingTraceSink:
    """Add run/phase/severity metadata to trace events before forwarding them."""

    def __init__(self, inner: TraceSink) -> None:
        self.inner = inner

    @property
    def events(self) -> Any:
        return getattr(self.inner, "events", None)

    def record(self, event: WorkflowTraceEvent) -> None:
        self.inner.record(_enrich_trace_event(event))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class AsyncQueueTraceSink:
    """Non-blocking asyncio.Queue trace feed for live observers."""

    def __init__(
        self,
        queue: asyncio.Queue[WorkflowTraceEvent] | None = None,
        *,
        maxsize: int = 1000,
    ) -> None:
        self.queue = queue or asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def record(self, event: WorkflowTraceEvent) -> None:
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


class AsyncQueueDetailSink:
    """Non-blocking asyncio.Queue detail feed for live observers."""

    def __init__(
        self,
        queue: asyncio.Queue[ObservationDetail] | None = None,
        *,
        maxsize: int = 1000,
    ) -> None:
        self.queue = queue or asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def record(self, detail: ObservationDetail) -> None:
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
        self.queue.put_nowait(detail)

    def clear(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self.queue.task_done()


class TeeTraceSink:
    """Fan each trace event out to multiple sinks."""

    def __init__(self, *sinks: TraceSink) -> None:
        self.sinks = list(sinks)

    def record(self, event: WorkflowTraceEvent) -> None:
        for sink in self.sinks:
            sink.record(event)


class TeeDetailSink:
    """Fan each detail record out to multiple sinks."""

    def __init__(self, *sinks: DetailSink) -> None:
        self.sinks = list(sinks)

    def record(self, detail: ObservationDetail) -> None:
        for sink in self.sinks:
            sink.record(detail)

    def clear(self) -> None:
        for sink in self.sinks:
            clear = getattr(sink, "clear", None)
            if callable(clear):
                clear()


class CapabilityRegistry:
    """Registry of typed capabilities available to a workflow supervisor."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[CapabilitySpec, CapabilityHandler]] = {}

    def register(self, spec: CapabilitySpec, handler: CapabilityHandler) -> None:
        if spec.name in self._items:
            raise ValueError(f"Capability already registered: {spec.name}")
        self._items[spec.name] = (spec, handler)

    def get(self, name: str) -> tuple[CapabilitySpec, CapabilityHandler]:
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"Unknown capability: {name}") from exc

    def list_specs(self) -> list[CapabilitySpec]:
        return [spec for spec, _handler in self._items.values()]

    def names(self) -> list[str]:
        return list(self._items)


class RuntimePlanCompiler:
    """Compile a reusable workflow profile into an inspectable runtime plan."""

    def __init__(self, supported_constraint_keys: Iterable[str] | None = None):
        self.supported_constraint_keys = set(supported_constraint_keys or ())

    def compile(
        self,
        profile: WorkflowProfile,
        registry: CapabilityRegistry | None = None,
    ) -> RuntimePlan:
        warnings: list[str] = []
        if self.supported_constraint_keys:
            for key in sorted(profile.constraints):
                if key not in self.supported_constraint_keys:
                    warnings.append(f"Unsupported constraint key: {key}")

        capability_names = list(profile.requested_capabilities)
        if registry is not None and not capability_names:
            capability_names = registry.names()
        if registry is not None:
            known = set(registry.names())
            for name in capability_names:
                if name not in known:
                    warnings.append(f"Unknown capability requested: {name}")

        return RuntimePlan(
            workflow_type=profile.workflow_type,
            profile_id=profile.profile_id,
            constraints=dict(profile.constraints),
            capability_names=capability_names,
            fail_mode=profile.fail_mode,
            scheduling=profile.scheduling,
            limits=profile.limits,
            safety=profile.safety,
            warnings=warnings,
        )


class CapabilityRuntime:
    """Invoke registered capabilities with validation, timeout, result envelope, and trace."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        trace_sink: TraceSink | None = None,
        *,
        detail_sink: DetailSink | None = None,
        capture_detail_text: bool = False,
    ) -> None:
        self.registry = registry
        self.trace_sink = ContextEnrichingTraceSink(trace_sink or InMemoryTraceSink())
        self.detail_sink = detail_sink
        self._capture_detail_text = capture_detail_text
        self.observation = ObservationCapture(
            self.trace_sink,
            detail_sink=detail_sink,
            mode="full" if capture_detail_text else "off",
        )
        # v0.11: one owner for capability observation projection (start/terminal trace + linked
        # details). It receives decided facts and never makes a control decision. Pass PROVIDERS,
        # not the bound method/instance, so each record resolves the runtime's CURRENT trace sink
        # and observation — a consumer may swap either after construction (v0.10.1 call-time
        # semantics; the GoPro pilot tees ``runtime.trace_sink`` this way).
        self._projector = CapabilityObservationProjector(
            lambda: self.trace_sink.record,
            lambda: self.observation,
        )

    async def invoke(
        self,
        name: str,
        payload: Any,
        context: CapabilityContext,
        *,
        attempt: int = 1,
    ) -> CapabilityResult:
        """The engine's ONE capability door — visible stage orchestration (v0.11).

        Stage order: registry lookup → input validation (contract) → start projection
        (observation) → side-effect admission (contract) → pre-call budget admission →
        window/awaiting supervision → result normalization (contract) → terminal projection
        (observation) → return. Failure paths keep the frozen inventory: unknown capability is
        a loud KeyError; an engine deadline is an honest PARTIAL; containment failure and
        handler errors are FAILED; caller cancellation propagates untranslated. The dicts built
        here are CONTROL data (they ride on the returned CapabilityResult and are mirrored to
        the trace), so they belong to the facade, not to the observation projector."""

        spec, handler = self.registry.get(name)
        start = time.monotonic()
        try:
            parsed_payload = validate_payload(spec, payload)
            self._projector.start(name=name, attempt=attempt, spec=spec, payload=parsed_payload)
            denied = denied_side_effects(spec, context)
            if denied:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                error = f"Capability side effects denied by safety policy: {', '.join(denied)}"
                rejected = CapabilityResult(
                    status="rejected",
                    error=error,
                    metadata={"denied_side_effects": denied},
                )
                self._projector.rejected(
                    name=name, attempt=attempt, spec=spec, result=rejected,
                    error=error, elapsed_ms=elapsed_ms,
                )
                return rejected
            if spec.kind in {"agent", "external"}:
                check_budget_before_call(spec.kind, name)

            # v0.11: the supervision owner resolves the window (enforcement-first, intersecting
            # capability limit + run-remaining + per-task request + parent-soft), refuses
            # already-exhausted or misdeclared-uninterruptible work, publishes/resets the ambient
            # nested-window scope, and bounds the await with cancellation containment.
            session = current_run_session()
            is_async = handler_is_async(handler)
            window, context, enforcement = resolve_invocation_window(
                name=name, spec=spec, context=context, is_async=is_async, session=session
            )
            with published_invocation_scope(window):
                result = handler(context, parsed_payload)
                if inspect.isawaitable(result):
                    result = await supervise_awaitable(
                        name=name, awaitable=result, window=window, enforcement=enforcement
                    )
            output = normalize_result(spec, result)
            if session is not None:
                # A completed invocation is the earliest universal artifact boundary. Fan-out or
                # child execution may be cancelled before its aggregate graph update commits, so
                # the run session retains evidence here rather than reconstructing it from state.
                session.retain_artifacts(output.artifacts)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            # v0.10: record the resolved window on the result event of a BOUNDED call so the
            # viewer/audit can project the effective soft/hard window + clamps + enforcement on
            # a node that finished normally too — not only on the timeout path. Byte-free
            # (durations + closed vocab), and only when bounded so unbounded traces stay lean.
            result_metadata = (
                {"execution_window": window.model_dump()} if window.is_bounded else {}
            )
            process_bound = output.metadata.get("process_execution_bound")
            if isinstance(process_bound, dict):
                result_metadata["process_execution_bound"] = dict(process_bound)
            # v0.10.1: carry the bounded-capture truth (byte totals / truncation / result-file
            # settlement) onto the trace event so the viewer can project it. It is byte-free
            # (counts + closed vocab), never the captured bytes.
            process_io = output.metadata.get("process_io")
            if isinstance(process_io, dict):
                result_metadata["process_io"] = dict(process_io)
            self._projector.terminal(
                name=name, attempt=attempt, spec=spec, output=output,
                elapsed_ms=elapsed_ms, metadata=result_metadata,
            )
            return output
        except ExecutionTimeout as timeout_exc:
            # v0.10: timeout is honest PARTIAL machine data — the work was bounded and stopped,
            # its already-incurred usage stays counted, and the window rides in metadata.
            elapsed_ms = int((time.monotonic() - start) * 1000)
            error = str(timeout_exc)
            window_meta = (
                timeout_exc.window.model_dump() if timeout_exc.window is not None else None
            )
            timeout_metadata = {
                "timeout_reason": "execution_window_exceeded",
                **({"execution_window": window_meta} if window_meta else {}),
            }
            self._projector.timeout(
                name=name, attempt=attempt, spec=spec, error=error,
                elapsed_ms=elapsed_ms, metadata=timeout_metadata,
            )
            return CapabilityResult(status="partial", error=error, metadata=timeout_metadata)
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            elapsed_ms = int((time.monotonic() - start) * 1000)
            decision = getattr(exc, "decision", None) or "failed"
            containment_window = (
                exc.window.model_dump()
                if isinstance(exc, CancellationContainmentError)
                else None
            )
            failure_metadata = (
                {
                    "timeout_reason": "cancellation_containment_failed",
                    "execution_window": containment_window,
                }
                if containment_window is not None
                else {}
            )
            self._projector.failed(
                name=name, attempt=attempt, spec=spec, error=error, decision=decision,
                elapsed_ms=elapsed_ms, metadata=failure_metadata,
            )
            return CapabilityResult(status="failed", error=error, metadata=failure_metadata)

def _enrich_trace_event(event: WorkflowTraceEvent) -> WorkflowTraceEvent:
    updates: dict[str, Any] = {}
    context = current_workflow_run_context()
    if context is not None and not event.run_id:
        run_id = getattr(context, "workflow_id", None)
        if run_id:
            updates["run_id"] = str(run_id)
    if event.phase is None:
        phase = _infer_trace_phase(event)
        if phase:
            updates["phase"] = phase
    if event.error and event.severity == "info":
        updates["severity"] = "error"
    return event.model_copy(update=updates) if updates else event


def _infer_trace_phase(event: WorkflowTraceEvent) -> str | None:
    if event.error:
        return "error"
    decision = event.decision or ""
    if decision == "start":
        return "node:start"
    if decision in {"accepted", "failed", "partial", "rejected"}:
        return "node:end"
    if decision:
        return "node:decision"
    return None


def capability_context_for_goal(
    goal: WorkflowGoal,
    *,
    plan: RuntimePlan | None = None,
    workflow_id: str = "wf-test",
) -> CapabilityContext:
    """Build a minimal capability context for examples and tests."""

    return CapabilityContext(
        goal=goal,
        plan=plan,
        run_context=WorkflowRunContext(
            workflow_id=workflow_id,
            workflow_type=goal.workflow_type,
            goal_id=goal.goal_id,
            delivery_target=goal.delivery_target,
            user_id=goal.user_id,
            metadata=dict(goal.metadata),
        ),
        limits=plan.limits if plan else RuntimeLimits(),
    )


def artifact_result(output: Any, artifacts: list[WorkflowArtifact]) -> CapabilityResult:
    """Small helper for capabilities that return artifacts."""

    return CapabilityResult(status="accepted", output=output, artifacts=artifacts)


async def gather_capabilities(
    runtime: CapabilityRuntime,
    calls: Iterable[CapabilityCall],
    context: CapabilityContext,
    *,
    max_parallel: int = 4,
) -> list[CapabilityResult]:
    """Run multiple capability calls with bounded parallelism and failure isolation."""

    async def invoke_call(call: CapabilityCall, _index: int) -> CapabilityResult:
        return await runtime.invoke(call.name, call.payload, context, attempt=call.attempt)

    return await gather_capability_calls(
        calls,
        max_parallel=max_parallel,
        invoke_call=invoke_call,
    )


async def gather_capability_calls(
    calls: Iterable[CapabilityCall],
    *,
    max_parallel: int,
    invoke_call: Callable[[CapabilityCall, int], Awaitable[CapabilityResult]],
) -> list[CapabilityResult]:
    """Shared concurrency owner for public and node-bound fanout calls.

    The callback receives the ENUMERATED input index (deterministic item identity for
    bound fanout attribution) — result order stays input order regardless of completion
    order, as before."""

    semaphore = asyncio.Semaphore(max(1, max_parallel))

    async def invoke(call: CapabilityCall, index: int) -> CapabilityResult:
        async with semaphore:
            return await invoke_call(call, index)

    return await asyncio.gather(*(invoke(call, index) for index, call in enumerate(calls)))


def format_trace_events(
    events: Iterable[WorkflowTraceEvent],
    *,
    usage: WorkflowUsageSummary | None = None,
    title: str = "Workflow trace",
    max_value_len: int = 280,
    max_total_len: int = 3500,
) -> str:
    """Render workflow trace events into a compact, human-readable run summary.

    Reusable engine traceability: a product can forward this (e.g. to a chat transport) as a debug
    message showing the key flow — nodes visited, branch decisions, per-node key info / LLM outputs
    (from event metadata), timings, and a usage/cost footer. The renderer is product-neutral; the
    domain detail comes from whatever each node records in ``WorkflowTraceEvent.metadata``.
    """

    lines: list[str] = [title]
    for index, event in enumerate(events, start=1):
        head = f"{index}. {event.node}"
        if event.decision:
            head += f" → {event.decision}"
        if event.attempt and event.attempt > 1:
            head += f" (attempt {event.attempt})"
        if event.elapsed_ms:
            head += f" [{event.elapsed_ms}ms]"
        lines.append(head)
        if event.error:
            lines.append(f"   ⚠ {event.error}")
        for key, value in (event.metadata or {}).items():
            if _omit_trace_metadata_key(key):
                continue
            if value in (None, "", [], {}):
                continue
            rendered = str(value).replace("\n", " ")
            if len(rendered) > max_value_len:
                rendered = rendered[: max_value_len] + "…"
            lines.append(f"   • {key}: {rendered}")
    footer = _format_trace_usage_footer(usage) if usage is not None else None
    if footer is not None:
        lines.append(footer)
    text = "\n".join(lines)
    if len(text) > max_total_len:
        if footer is None:
            text = text[: max_total_len] + "\n…(trace truncated)"
        else:
            suffix = "\n…(trace truncated)\n" + footer
            body_budget = max_total_len - len(suffix)
            if body_budget > 0:
                body = "\n".join(lines[:-1])
                text = body[:body_budget].rstrip() + suffix
            else:
                text = suffix[-max_total_len:]
    return text


_TRACE_METADATA_OMIT_KEYS = {
    "detail_kind",
    "detail_digest",
    "prompt_digest",
    "response_digest",
    "projection_digest",
    "plan_digest",
    "run_id",
    "workflow_id",
    "workflow_type",
    "user_id",
}


def _omit_trace_metadata_key(key: str) -> bool:
    return key in _TRACE_METADATA_OMIT_KEYS or key.startswith("_ai_workflow_engine_")


def _format_trace_usage_footer(usage: WorkflowUsageSummary) -> str:
    # Same money-honesty language as format_usage_summary: "billed" is REAL provider
    # charges only ($0 when no metered events exist — absent is not unknown); the
    # subscription segment is plan value, appears only when subscription calls exist.
    has_metered = any(
        getattr(event, "cost_class", "metered") == "metered" for event in usage.events
    )
    has_subscription = any(
        getattr(event, "cost_class", "metered") == "subscription_notional"
        for event in usage.events
    )
    billed = (
        f"billed (API): {_format_trace_cost(usage.metered_usd)}" if has_metered else "billed (API): $0"
    )
    if has_subscription:
        if usage.notional_usd is not None:
            subscription = f" · subscription: ~{_format_trace_cost(usage.notional_usd)} plan value"
        else:
            subscription = " · subscription: plan-covered (value unknown)"
    else:
        subscription = ""
    return (
        f"— {usage.text_call_count} text / {usage.image_call_count} image / "
        f"{usage.tool_call_count} tool calls, {usage.total_tokens} tokens, "
        f"{billed}{subscription}"
    )


def _format_trace_cost(value: Any) -> str:
    if value is None:
        return "?"
    try:
        return f"${float(value):.4f}"
    except (TypeError, ValueError):
        return "?"
