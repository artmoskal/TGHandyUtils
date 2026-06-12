"""Generic capability runtime for reusable workflow execution."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
import time
from typing import Any, Callable, Iterable, NamedTuple, Protocol

from pydantic import ValidationError

from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    CapabilitySpec,
    RuntimePlan,
    RuntimeLimits,
    WorkflowArtifact,
    WorkflowGoal,
    WorkflowProfile,
    WorkflowRunContext,
    WorkflowTraceEvent,
    WorkflowUsageSummary,
)
from ai_workflow_engine.usage import check_budget_before_call

CapabilityHandler = Callable[[CapabilityContext, Any], Any]


class CapabilityCall(NamedTuple):
    name: str
    payload: Any
    attempt: int = 1


class TraceSink(Protocol):
    """Receives trace events from the runtime."""

    def record(self, event: WorkflowTraceEvent) -> None:
        """Store one trace event."""


class InMemoryTraceSink:
    """Simple trace sink suitable for tests and short in-process runs."""

    def __init__(self) -> None:
        self.events: list[WorkflowTraceEvent] = []

    def record(self, event: WorkflowTraceEvent) -> None:
        self.events.append(event)


class JsonlTraceSink:
    """Append trace events to a JSONL file for handoff/debuggable workflow runs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: WorkflowTraceEvent) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json())
            fh.write("\n")


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
    ) -> None:
        self.registry = registry
        self.trace_sink = trace_sink or InMemoryTraceSink()

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
        self._record(WorkflowTraceEvent(node=name, attempt=attempt, decision="start"))
        try:
            parsed_payload = self._validate_payload(spec, payload)
            denied = self._denied_side_effects(spec, context)
            if denied:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                error = f"Capability side effects denied by safety policy: {', '.join(denied)}"
                self._record(
                    WorkflowTraceEvent(
                        node=name,
                        attempt=attempt,
                        decision="rejected",
                        error=error,
                        elapsed_ms=elapsed_ms,
                        metadata={"denied_side_effects": denied},
                    )
                )
                return CapabilityResult(
                    status="rejected",
                    error=error,
                    metadata={"denied_side_effects": denied},
                )
            if spec.kind in {"agent", "external"}:
                check_budget_before_call(spec.kind, name)
            result = handler(context, parsed_payload)
            if inspect.isawaitable(result):
                if spec.timeout_s:
                    result = await asyncio.wait_for(result, timeout=spec.timeout_s)
                else:
                    result = await result
            output = self._normalize_result(spec, result)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._record(
                WorkflowTraceEvent(
                    node=name,
                    attempt=attempt,
                    decision=output.status,
                    artifacts=[artifact.artifact_id for artifact in output.artifacts],
                    elapsed_ms=elapsed_ms,
                )
            )
            return output
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            elapsed_ms = int((time.monotonic() - start) * 1000)
            decision = getattr(exc, "decision", None) or "failed"
            self._record(
                WorkflowTraceEvent(
                    node=name,
                    attempt=attempt,
                    decision=decision,
                    error=error,
                    elapsed_ms=elapsed_ms,
                )
            )
            return CapabilityResult(status="failed", error=error)

    def _record(self, event: WorkflowTraceEvent) -> None:
        self.trace_sink.record(event)

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
            if value in (None, "", [], {}):
                continue
            rendered = str(value).replace("\n", " ")
            if len(rendered) > max_value_len:
                rendered = rendered[: max_value_len] + "…"
            lines.append(f"   • {key}: {rendered}")
    if usage is not None:
        cost = usage.estimated_usd or 0.0
        lines.append(
            f"— {usage.text_call_count} text / {usage.image_call_count} image / "
            f"{usage.tool_call_count} tool calls, {usage.total_tokens} tokens, ${cost:.4f}"
        )
    text = "\n".join(lines)
    if len(text) > max_total_len:
        text = text[: max_total_len] + "\n…(trace truncated)"
    return text
