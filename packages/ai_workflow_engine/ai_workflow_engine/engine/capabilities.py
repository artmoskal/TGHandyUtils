"""Generic capability runtime for reusable workflow execution."""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
import time
import uuid
from contextvars import ContextVar
from typing import Any, Callable, Iterable, NamedTuple, Optional, Protocol

from pydantic import ValidationError

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
from ai_workflow_engine.execution_window import (
    ExecutionWindowInputs,
    resolve_execution_window,
)
from ai_workflow_engine.observability_capture import ObservationCapture
from ai_workflow_engine.usage import check_budget_before_call

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


class JsonlDetailSink:
    """Append observation details to a JSONL file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, detail: ObservationDetail) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(detail.model_dump_json(by_alias=True))
            fh.write("\n")

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")


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


# v0.10 #4: an object with an async ``__call__`` is an async handler too — a bare
# iscoroutinefunction(handler) misses HumanClarificationCapability / agent capability objects.
def _handler_is_async(handler: Any) -> bool:
    if inspect.iscoroutinefunction(handler):
        return True
    call = getattr(handler, "__call__", None)
    return bool(call is not None and inspect.iscoroutinefunction(call))


# v0.10 #6a: the SOFT deadline (monotonic) of the currently-executing bounded invocation, so a
# NESTED invocation (subworkflow/child-plan capability) can only SHORTEN the window, never
# extend it. Set around the handler call, read by the resolver as parent_soft_remaining_s.
_ACTIVE_PARENT_SOFT_DEADLINE: "ContextVar[Optional[float]]" = ContextVar(
    "workflow_active_parent_soft_deadline", default=None
)


def _parent_soft_remaining_s() -> Optional[float]:
    deadline = _ACTIVE_PARENT_SOFT_DEADLINE.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


class _ExecutionTimeout(Exception):
    """A capability exceeded its resolved execution window. Carries the window so the outcome
    can be recorded as a truthful PARTIAL (bounded work stopped), not an anonymous failure."""

    def __init__(self, message: str, *, window: Any = None) -> None:
        super().__init__(message)
        self.window = window


class _CancellationContainmentError(Exception):
    """A cooperative handler suppressed cancellation at its execution window and finished
    anyway — the engine cannot claim it stopped. Surfaced as a FAILURE (not a clean partial),
    because unstoppable side effects may have continued past the boundary."""


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

    async def invoke(
        self,
        name: str,
        payload: Any,
        context: CapabilityContext,
        *,
        attempt: int = 1,
    ) -> CapabilityResult:
        spec, handler = self.registry.get(name)
        start = time.monotonic()
        try:
            parsed_payload = self._validate_payload(spec, payload)
            start_event = WorkflowTraceEvent(
                node=name,
                attempt=attempt,
                decision="start",
                phase="tool:request",
            )
            start_event = start_event.model_copy(
                update={
                    "detail_refs": self._record_tool_payload(
                        spec,
                        parsed_payload,
                        event_id=start_event.event_id,
                    )
                }
            )
            self._record(start_event)
            denied = self._denied_side_effects(spec, context)
            if denied:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                error = f"Capability side effects denied by safety policy: {', '.join(denied)}"
                rejected = CapabilityResult(
                    status="rejected",
                    error=error,
                    metadata={"denied_side_effects": denied},
                )
                rejected_event_id = str(uuid.uuid4())
                self._record(
                    WorkflowTraceEvent(
                        node=name,
                        attempt=attempt,
                        decision="rejected",
                        error=error,
                        elapsed_ms=elapsed_ms,
                        metadata={"denied_side_effects": denied},
                        phase="tool:result",
                        severity="error",
                        event_id=rejected_event_id,
                        detail_refs=self._record_tool_result(
                            spec,
                            rejected,
                            error=error,
                            event_id=rejected_event_id,
                        ),
                    )
                )
                return rejected
            if spec.kind in {"agent", "external"}:
                check_budget_before_call(spec.kind, name)

            # v0.10 execution window. Determine the enforcement mode FIRST (an object with an
            # async __call__ is async too — #4), so the RESOLVED, PERSISTED decision carries the
            # truthful enforcement (#2). Intersect the capability limit + run-remaining + any
            # per-task request the planner attached (#3).
            session = current_run_session()
            is_async = _handler_is_async(handler)
            enforcement = spec.resolved_timeout_enforcement(is_async=is_async)
            window = resolve_execution_window(
                ExecutionWindowInputs(
                    request=context.execution_request,
                    capability_timeout_s=spec.timeout_s,
                    run_remaining_s=session.run_remaining_s() if session is not None else None,
                    parent_soft_remaining_s=_parent_soft_remaining_s(),
                ),
                enforcement=enforcement,
            )
            context = context.model_copy(update={"execution_window": window})
            hard = window.hard_timeout_s

            # A run whose deadline has ALREADY passed must not start new work.
            if hard is not None and hard <= 0:
                raise _ExecutionTimeout(
                    f"run execution window exhausted before capability '{name}' could start",
                    window=window,
                )

            declared_bound = bool(
                {"task_request", "capability_limit"} & set(window.limiting_sources)
            )
            if enforcement == "none" and hard is not None and hard > 0 and declared_bound:
                raise _ExecutionTimeout(
                    f"capability '{name}' declares a finite execution window but cannot be "
                    f"interrupted (enforcement='none'); place work needing a hard bound behind "
                    f"a process-backed capability",
                    window=window,
                )

            # Publish THIS invocation's soft deadline so nested subworkflow/child-plan calls
            # are bounded by our remaining soft budget (they can only shorten it).
            soft = window.soft_timeout_s
            parent_token = (
                _ACTIVE_PARENT_SOFT_DEADLINE.set(time.monotonic() + soft)
                if soft is not None
                else None
            )
            try:
                result = handler(context, parsed_payload)
                if inspect.isawaitable(result):
                    if hard is not None and enforcement in ("cooperative", "process"):
                        result = await self._run_bounded(name, result, hard, window)
                    else:
                        result = await result
            finally:
                if parent_token is not None:
                    _ACTIVE_PARENT_SOFT_DEADLINE.reset(parent_token)
            output = self._normalize_result(spec, result)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result_event_id = str(uuid.uuid4())
            # v0.10: record the resolved window on the result event of a BOUNDED call so the
            # viewer/audit can project the effective soft/hard window + clamps + enforcement on
            # a node that finished normally too — not only on the timeout path. Byte-free
            # (durations + closed vocab), and only when bounded so unbounded traces stay lean.
            result_metadata = (
                {"execution_window": window.model_dump()} if window.is_bounded else {}
            )
            self._record(
                WorkflowTraceEvent(
                    node=name,
                    attempt=attempt,
                    decision=output.status,
                    artifacts=[artifact.artifact_id for artifact in output.artifacts],
                    elapsed_ms=elapsed_ms,
                    phase="tool:result",
                    severity="error" if output.status in {"failed", "rejected"} or output.error else "info",
                    event_id=result_event_id,
                    metadata=result_metadata,
                    detail_refs=[
                        *self._record_tool_result(
                            spec,
                            output,
                            error=output.error,
                            event_id=result_event_id,
                        ),
                        *self._record_artifact_previews(spec, output, event_id=result_event_id),
                    ],
                )
            )
            return output
        except _ExecutionTimeout as timeout_exc:
            # v0.10: timeout is honest PARTIAL machine data — the work was bounded and stopped,
            # its already-incurred usage stays counted, and the window rides in metadata.
            elapsed_ms = int((time.monotonic() - start) * 1000)
            error = str(timeout_exc)
            timeout_event_id = str(uuid.uuid4())
            window_meta = (
                timeout_exc.window.model_dump() if timeout_exc.window is not None else None
            )
            self._record(
                WorkflowTraceEvent(
                    node=name,
                    attempt=attempt,
                    decision="partial",
                    error=error,
                    elapsed_ms=elapsed_ms,
                    phase="tool:result",
                    severity="error",
                    event_id=timeout_event_id,
                    metadata={
                        "timeout_reason": "execution_window_exceeded",
                        **({"execution_window": window_meta} if window_meta else {}),
                    },
                    detail_refs=self._record_tool_error(
                        spec, decision="partial", error=error, event_id=timeout_event_id
                    ),
                )
            )
            return CapabilityResult(
                status="partial",
                error=error,
                metadata={
                    "timeout_reason": "execution_window_exceeded",
                    **({"execution_window": window_meta} if window_meta else {}),
                },
            )
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            elapsed_ms = int((time.monotonic() - start) * 1000)
            decision = getattr(exc, "decision", None) or "failed"
            error_event_id = str(uuid.uuid4())
            self._record(
                WorkflowTraceEvent(
                    node=name,
                    attempt=attempt,
                    decision=decision,
                    error=error,
                    elapsed_ms=elapsed_ms,
                    phase="tool:result",
                    severity="error",
                    event_id=error_event_id,
                    detail_refs=self._record_tool_error(
                        spec,
                        decision=decision,
                        error=error,
                        event_id=error_event_id,
                    ),
                )
            )
            return CapabilityResult(status="failed", error=error)

    async def _run_bounded(self, name: str, awaitable: Any, hard: float, window: Any) -> Any:
        """Run an awaitable under a hard window with honest cancellation containment.

        Within the window: return its result. At the boundary: cancel it and give a bounded
        grace to acknowledge. If it raises CancelledError (clean stop) → PARTIAL timeout. If it
        SUPPRESSES cancellation and returns anyway → a containment FAILURE (the engine will not
        claim a stop it did not perform). If it errors while cancelling → timeout/partial."""

        inner = asyncio.ensure_future(awaitable)
        done, _pending = await asyncio.wait({inner}, timeout=hard)
        if inner in done:
            # completed within the window — return its result (or re-raise its own exception,
            # which the caller maps to a normal failure, NOT a timeout).
            return inner.result()
        inner.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(inner), timeout=self._CANCEL_GRACE_S)
        except asyncio.CancelledError:
            raise _ExecutionTimeout(
                f"capability '{name}' exceeded its {hard:g}s execution window",
                window=window,
            )
        except asyncio.TimeoutError:
            raise _CancellationContainmentError(
                f"capability '{name}' did not acknowledge cancellation within "
                f"{self._CANCEL_GRACE_S:g}s of its execution window"
            )
        except Exception:
            raise _ExecutionTimeout(
                f"capability '{name}' exceeded its {hard:g}s execution window",
                window=window,
            )
        else:
            # cancelled but returned a value anyway → suppression → containment FAILURE.
            raise _CancellationContainmentError(
                f"capability '{name}' suppressed cancellation at its execution window and "
                f"returned anyway"
            )

    _CANCEL_GRACE_S = 1.0

    def _record(self, event: WorkflowTraceEvent) -> None:
        self.trace_sink.record(event)

    def _record_tool_payload(self, spec: CapabilitySpec, payload: Any, event_id: str | None = None) -> list[str]:
        event_id = event_id or str(uuid.uuid4())
        detail = self.observation.record_detail(
            event_id=event_id,
            kind="tool_payload",
            payload={
                "capability": spec.name,
                "kind": spec.kind,
                "payload": payload,
            },
        )
        return [detail.detail_id] if detail else []

    def _record_tool_result(
        self,
        spec: CapabilitySpec,
        output: CapabilityResult,
        *,
        error: str | None = None,
        event_id: str | None = None,
    ) -> list[str]:
        event_id = event_id or str(uuid.uuid4())
        artifact_refs = [
            {
                "artifact_id": artifact.artifact_id,
                "kind": artifact.kind,
                "owner_node": artifact.owner_node or spec.name,
                "source": artifact.source,
            }
            for artifact in output.artifacts
        ]
        detail = self.observation.record_detail(
            event_id=event_id,
            kind="tool_result",
            payload={
                "capability": spec.name,
                "kind": spec.kind,
                "status": output.status,
                "output": output.output,
                "error": output.error,
                "artifact_refs": artifact_refs,
                "metadata": output.metadata,
            },
        )
        return [detail.detail_id] if detail else []

    def _record_tool_error(
        self,
        spec: CapabilitySpec,
        *,
        decision: str,
        error: str,
        event_id: str | None = None,
    ) -> list[str]:
        event_id = event_id or str(uuid.uuid4())
        detail = self.observation.record_detail(
            event_id=event_id,
            kind="tool_result",
            payload={
                "capability": spec.name,
                "kind": spec.kind,
                "status": decision,
                "error": error,
            },
        )
        return [detail.detail_id] if detail else []

    def _record_artifact_previews(
        self,
        spec: CapabilitySpec,
        output: CapabilityResult,
        *,
        event_id: str | None = None,
    ) -> list[str]:
        event_id = event_id or str(uuid.uuid4())
        detail_refs: list[str] = []
        for artifact in output.artifacts:
            detail = self.observation.record_detail(
                event_id=event_id,
                kind="artifact_preview",
                payload={
                    "capability": spec.name,
                    "artifact": artifact,
                },
            )
            if detail:
                detail_refs.append(detail.detail_id)
        return detail_refs

    @staticmethod
    def _validate_payload(spec: CapabilitySpec, payload: Any) -> Any:
        if not spec.input_model:
            return payload
        if isinstance(payload, spec.input_model):
            return payload
        try:
            return spec.input_model.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"{spec.name} input validation failed: {exc}") from exc

    @staticmethod
    def _normalize_result(spec: CapabilitySpec, result: Any) -> CapabilityResult:
        if isinstance(result, CapabilityResult):
            output = result
        else:
            output_value = result
            if spec.output_model:
                if not isinstance(result, spec.output_model):
                    try:
                        output_value = spec.output_model.model_validate(result)
                    except ValidationError as exc:
                        raise ValueError(f"{spec.name} output validation failed: {exc}") from exc
            output = CapabilityResult(status="accepted", output=output_value)
        return output

    @staticmethod
    def _denied_side_effects(spec: CapabilitySpec, context: CapabilityContext) -> list[str]:
        if not spec.side_effects or context.plan is None:
            return []
        allowed = set(context.plan.safety.allowed_side_effects)
        return [side_effect for side_effect in spec.side_effects if side_effect not in allowed]


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

    semaphore = asyncio.Semaphore(max(1, max_parallel))

    async def invoke(call: CapabilityCall) -> CapabilityResult:
        async with semaphore:
            return await runtime.invoke(call.name, call.payload, context, attempt=call.attempt)

    return await asyncio.gather(*(invoke(call) for call in calls))


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
