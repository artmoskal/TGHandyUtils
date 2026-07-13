"""Preservation oracle for the v0.11 invocation refactor (Iteration 1, Phase I1.0).

The engine's ONE public door — ``CapabilityRuntime.invoke`` — is being split into internal
owners (contract / supervision / observation) behind a stable facade. This module is the
degradation oracle that must prove the refactor is BEHAVIOR-PRESERVING: it runs a deterministic
scenario corpus through the public door and emits a canonical, comparable semantic record per
scenario, covering every row of the plan's behavior-freeze inventory.

Version-agnostic ON PURPOSE: it imports ONLY the stable public surface, so the exact same corpus
runs against the immutable ``engine-v0.10.1`` baseline wheel and the candidate wheel in isolated
subprocesses (the live differential), and against the committed baseline fixture in-process (the
fast gate). A canonical record strips only intrinsically-volatile facts (ids, timestamps, elapsed,
tmp paths); any OTHER difference is a real behavior delta, never silently ignored.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Callable

# REVIEWED volatile normalization: a cooperative-timeout error embeds the REMAINING soft budget
# at the moment of cancellation (a monotonic-clock wall value with microsecond jitter, e.g.
# "0.249991s"). Round decimals inside strings to 2 places — this collapses sub-0.01 clock jitter
# while any real change >= 0.01 still differs. It is the ONLY string normalization.
_DECIMAL_IN_STRING = re.compile(r"\d+\.\d+")


def _round_decimals_in_string(text: str) -> str:
    return _DECIMAL_IN_STRING.sub(lambda m: f"{float(m.group()):.2f}", text)

# Stable public surface only.
from ai_workflow_engine import (
    CapabilityResult,
    CapabilitySpec,
    WorkflowArtifact,
)
from ai_workflow_engine.budget import current_usage_context
from ai_workflow_engine.engine.capabilities import (
    CapabilityRegistry,
    CapabilityRuntime,
    InMemoryDetailSink,
    InMemoryTraceSink,
)
from ai_workflow_engine.models import (
    CapabilityContext,
    RuntimePlan,
    SafetyPolicy,
    WorkflowGoal,
    WorkflowRunContext,
)

# Behavior-inventory rows (plan §"Behavior Inventory To Freeze"). Every row maps to >= 1 scenario;
# the completeness guard fails by NAME when a row has no scenario. Integrated rows (nested/
# concurrent/fanout/durable/process) are covered by dedicated multi-step scenarios below.
BEHAVIOR_ROWS = (
    "unknown_capability",
    "invalid_input",
    "side_effect_denied",
    "budget_denied",
    "sync_success",
    "async_success",
    "explicit_partial",
    "invalid_output",
    "handler_exception",
    "handler_owned_timeout",
    "cooperative_deadline",
    "caller_cancellation",
    "swallowed_cancellation",
    "process_timeout",
    "artifact_result",
    "capture_off",
    "capture_full",
    "trace_sink_failure",
    "detail_sink_failure",
    "nested_invocation",
    "concurrent_invocations",
)

_VOLATILE_METADATA_KEYS = frozenset({
    "timeout_s", "requested_timeout_s", "kill_grace_s",  # process bound wall values vary by host
})


def _fixed_context(*, allowed_side_effects=None, execution_request=None) -> CapabilityContext:
    """A deterministic context: fixed run id, injected safety policy, optional task window."""

    kwargs: dict[str, Any] = dict(
        goal=WorkflowGoal(workflow_type="oracle", objective="preserve"),
        run_context=WorkflowRunContext(workflow_id="oracle-run", workflow_type="oracle"),
        plan=RuntimePlan(
            workflow_type="oracle",
            safety=SafetyPolicy(allowed_side_effects=allowed_side_effects or []),
        ),
    )
    if execution_request is not None:
        kwargs["execution_request"] = execution_request
    return CapabilityContext(**kwargs)


def _build_alias_map(trace_events: Any, detail_records: Any, artifacts: Any) -> dict[str, str]:
    """Map every generated id (event/detail/artifact uuid) to a DETERMINISTIC position alias
    (E0/D0/A0...). Behavior-preserving runs produce ids in the same order, so aliases match across
    the baseline and candidate wheels; a broken/dropped/reordered link shifts the alias sequence and
    the comparator catches it. This preserves the reference GRAPH while neutralizing only the id
    values themselves — the opposite of reducing links to a bare count."""

    amap: dict[str, str] = {}
    for i, e in enumerate(trace_events or []):
        eid = getattr(e, "event_id", None)
        if eid and eid not in amap:
            amap[eid] = f"E{i}"
    for j, d in enumerate(detail_records or []):
        did = getattr(d, "detail_id", None)
        if did and did not in amap:
            amap[did] = f"D{j}"
    for k, a in enumerate(artifacts or []):
        aid = getattr(a, "artifact_id", None)
        if aid and aid not in amap:
            amap[aid] = f"A{k}"
    return amap


def _apply_aliases(text: str, amap: dict[str, str]) -> str:
    for _id, token in amap.items():
        if _id and _id in text:
            text = text.replace(_id, token)
    return text


def _canon(value: Any, amap: dict[str, str] | None = None) -> Any:
    """Canonicalize a value for cross-version comparison: sort dict keys, drop intrinsically
    volatile metadata leaves, alias generated ids, and stringify opaque objects deterministically."""

    amap = amap or {}
    if isinstance(value, dict):
        out = {}
        for k in sorted(value.keys(), key=str):
            if k in _VOLATILE_METADATA_KEYS:
                out[k] = "<volatile>"
            else:
                out[k] = _canon(value[k], amap)
        return out
    if isinstance(value, (list, tuple)):
        return [_canon(v, amap) for v in value]
    if isinstance(value, str):
        return _round_decimals_in_string(_apply_aliases(value, amap))
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    # opaque object (pydantic model, etc.): dump structurally when possible
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _canon(dump(mode="json"), amap)
        except Exception:
            return f"<obj:{type(value).__name__}>"
    return f"<obj:{type(value).__name__}>"


def _canon_artifact(artifact: Any, amap: dict[str, str]) -> dict[str, Any]:
    """A full artifact record (aliased id) — not just its kind. Field loss now shows as a path
    mismatch: path, kind, source, owner_node, cleanup_on_failure, and metadata are all compared."""

    return {
        "artifact": amap.get(getattr(artifact, "artifact_id", None)),
        "path": artifact.path,
        "kind": artifact.kind,
        "source": artifact.source,
        "owner_node": artifact.owner_node,
        "cleanup_on_failure": artifact.cleanup_on_failure,
        "metadata": _canon(dict(artifact.metadata or {}), amap),
    }


def _canon_result(result: "CapabilityResult", amap: dict[str, str] | None = None) -> dict[str, Any]:
    amap = amap or {}
    output = result.output
    output_repr: Any
    dump = getattr(output, "model_dump", None)
    if callable(dump):
        try:
            output_repr = _canon(dump(mode="json"), amap)
        except Exception:
            output_repr = f"<obj:{type(output).__name__}>"
    else:
        output_repr = _canon(output, amap)
    return {
        "kind": "result",
        "status": result.status,
        "output": output_repr,
        "error": _canon(result.error, amap) if result.error else result.error,
        "artifacts": [_canon_artifact(a, amap) for a in result.artifacts],
        "metadata": _canon(result.metadata, amap),
    }


def _canon_event(event: Any, amap: dict[str, str] | None = None) -> dict[str, Any]:
    """Canonical semantic trace event with the reference GRAPH preserved: node/attempt/phase/
    decision/severity/error/metadata, this event's alias, and its ORDERED detail/artifact edges
    (aliased ids, NOT bare counts) — a broken, dropped, or reordered link now shows as a path
    mismatch instead of surviving because the count happened to match."""

    amap = amap or {}
    return {
        "node": event.node,
        "attempt": event.attempt,
        "phase": event.phase,
        "decision": event.decision,
        "severity": event.severity,
        "error": _canon(event.error, amap) if event.error else event.error,
        "metadata": _canon(dict(event.metadata or {}), amap),
        "event": amap.get(getattr(event, "event_id", None)),
        "detail_refs": [amap.get(r, r) for r in (getattr(event, "detail_refs", []) or [])],
        "artifacts": [amap.get(a, a) for a in (getattr(event, "artifacts", []) or [])],
    }


def _digest_consistent(detail: Any) -> bool | None:
    """Whether the stored digest matches a re-digest of the stored json payload. Mirrors
    observability_capture.payload_digest (a stable helper untouched by v0.11); detects a projector
    that ever desyncs a detail's digest from its content. Deterministic within a run (uses the
    detail's OWN raw payload), so it is stable across the baseline and candidate wheels."""

    digest = getattr(detail, "digest", None)
    payload = getattr(detail, "json_value", None)
    if digest is None or payload is None:
        return None
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest() == digest


def _canon_detail(detail: Any, amap: dict[str, str] | None = None) -> dict[str, Any]:
    """Full byte-safe detail record with the reference graph preserved via aliases: content
    (text/json/metadata), classification (kind/privacy/redaction_state/content_type), the event
    this detail hangs off, the artifact it describes, and digest integrity — payload corruption,
    digest drift, and broken linkage can no longer pass as EQUAL."""

    amap = amap or {}
    return {
        "kind": detail.kind,
        "privacy": getattr(detail, "privacy", None),
        "redaction_state": getattr(detail, "redaction_state", None),
        "content_type": getattr(detail, "content_type", None),
        "event": amap.get(getattr(detail, "event_id", None)),        # EDGE: detail -> its event
        "artifact": amap.get(getattr(detail, "artifact_id", None)),  # EDGE: detail -> its artifact
        "text": _canon(getattr(detail, "text", None), amap),
        "json": _canon(getattr(detail, "json_value", None), amap),
        "metadata": _canon(dict(getattr(detail, "metadata", {}) or {}), amap),
        "has_digest": bool(getattr(detail, "digest", None)),
        "digest_consistent": _digest_consistent(detail),
    }


def _canon_usage(context: Any) -> dict[str, Any] | None:
    """Canonical usage summary + events from the ambient usage scope (None when no scope is
    active). Strips volatile per-event facts (request_id/elapsed_ms/sequence/run_id); keeps the
    metered classification, call counts, tokens, and cost that invoke's budget admission governs."""

    if context is None:
        return None
    summary = getattr(context, "summary", None)
    if summary is None:
        return None
    events = getattr(summary, "events", None) or []
    return {
        "worker_call_count": getattr(summary, "worker_call_count", None),
        "text_call_count": getattr(summary, "text_call_count", None),
        "image_call_count": getattr(summary, "image_call_count", None),
        "tool_call_count": getattr(summary, "tool_call_count", None),
        "total_tokens": getattr(summary, "total_tokens", None),
        "metered_usd": getattr(summary, "metered_usd", None),
        "notional_usd": getattr(summary, "notional_usd", None),
        "events": [
            {
                "provider": getattr(ev, "provider", None),
                "operation": getattr(ev, "operation", None),
                "cost_class": getattr(ev, "cost_class", None),
                "node": getattr(ev, "node", None),
                "model": getattr(ev, "model", None),
                "input_tokens": getattr(ev, "input_tokens", None),
                "output_tokens": getattr(ev, "output_tokens", None),
                "total_tokens": getattr(ev, "total_tokens", None),
                "success": getattr(ev, "success", None),
                "error": getattr(ev, "error", None),
            }
            for ev in events
        ],
    }


async def capture_invocation(
    *,
    spec: "CapabilitySpec",
    handler: Callable,
    payload: Any,
    context: CapabilityContext | None = None,
    name: str | None = None,
    capture: str = "off",
    trace_sink: Any = None,
    detail_sink: Any = None,
    invoke_name: str | None = None,
) -> dict[str, Any]:
    """Run ONE public-door invocation and return its canonical semantic record. Counts handler
    calls, captures the terminal result (or the loud exception), and the full trace/detail."""

    registry = CapabilityRegistry()
    calls = {"n": 0}

    def _counting(fn):
        if asyncio.iscoroutinefunction(fn):
            async def _aw(ctx, p):
                calls["n"] += 1
                return await fn(ctx, p)
            return _aw

        def _sw(ctx, p):
            calls["n"] += 1
            return fn(ctx, p)
        return _sw

    registry.register(spec, _counting(handler))
    trace = trace_sink if trace_sink is not None else InMemoryTraceSink()
    details = detail_sink if detail_sink is not None else (InMemoryDetailSink() if capture == "full" else None)
    runtime = CapabilityRuntime(
        registry, trace, detail_sink=details, capture_detail_text=(capture == "full")
    )
    ctx = context if context is not None else _fixed_context()
    raw_exc: BaseException | None = None
    result_obj: Any = None
    target = invoke_name if invoke_name is not None else (name or spec.name)
    try:
        result_obj = await runtime.invoke(target, payload, ctx)
    except BaseException as e:  # noqa: BLE001 - the oracle must record loud paths too
        raw_exc = e

    trace_events = getattr(trace, "events", None) or []
    detail_records = getattr(details, "details", None) or [] if details is not None else []
    artifacts = list(getattr(result_obj, "artifacts", []) or []) if result_obj is not None else []
    amap = _build_alias_map(trace_events, detail_records, artifacts)
    return {
        "result": _canon_result(result_obj, amap) if result_obj is not None else None,
        "exception": (
            {"type": type(raw_exc).__name__,
             "message": _round_decimals_in_string(_apply_aliases(str(raw_exc), amap))}
            if raw_exc is not None else None
        ),
        "handler_calls": calls["n"],
        "trace": [_canon_event(e, amap) for e in trace_events],
        "details": [_canon_detail(d, amap) for d in detail_records],
        "usage": _canon_usage(current_usage_context()),
    }


# --------------------------------------------------------------------------------------
# Deterministic scenario corpus. Each entry is an async fn returning a canonical record.
# Handlers/specs use only the stable public surface so the SAME corpus runs against the
# v0.10.1 baseline wheel and the candidate wheel.
# --------------------------------------------------------------------------------------

from pydantic import BaseModel as _BM  # noqa: E402


class _In(_BM):
    x: int


class _Out(_BM):
    y: int


def _spec(name: str, **kw) -> "CapabilitySpec":
    kw.setdefault("kind", "deterministic")
    return CapabilitySpec(name=name, **kw)


def _noop(_ctx, _p):
    return {"ok": 1}


async def _sc_unknown_capability():
    return await capture_invocation(spec=_spec("known"), handler=_noop, payload={}, invoke_name="ghost")


async def _sc_invalid_input():
    return await capture_invocation(spec=_spec("needs_in", input_model=_In), handler=_noop, payload={"x": "no"})


async def _sc_side_effect_denied():
    return await capture_invocation(
        spec=_spec("writer", side_effects=["network"]), handler=_noop, payload={},
        context=_fixed_context(allowed_side_effects=[]),
    )


async def _sc_budget_denied():
    from ai_workflow_engine.budget import WorkflowBudget
    from ai_workflow_engine.usage import WorkflowUsageContext, workflow_usage_scope
    from ai_workflow_engine.models import WorkflowUsageSummary

    ctx = _fixed_context()
    usage = WorkflowUsageContext(ctx.run_context, WorkflowUsageSummary(), WorkflowBudget(max_worker_calls=0))
    with workflow_usage_scope(usage):
        return await capture_invocation(spec=_spec("agent_worker", kind="agent"), handler=_noop, payload={})


async def _sc_worker_call_accounting():
    """Complements budget_denied for P-06: a worker-kind capability that SUCCEEDS under a generous
    budget. invoke's pre-call admission (check_budget_before_call) counts the worker call, so the
    captured usage summary must show worker_call_count == 1 — proving invoke still accounts worker
    calls and that the usage capture is non-empty, not just locking zeros."""

    from ai_workflow_engine.budget import WorkflowBudget
    from ai_workflow_engine.usage import WorkflowUsageContext, workflow_usage_scope
    from ai_workflow_engine.models import WorkflowUsageSummary

    ctx = _fixed_context()
    usage = WorkflowUsageContext(ctx.run_context, WorkflowUsageSummary(), WorkflowBudget(max_worker_calls=5))
    with workflow_usage_scope(usage):
        return await capture_invocation(spec=_spec("counted_worker", kind="agent"), handler=_noop, payload={})


async def _sc_sync_success():
    return await capture_invocation(spec=_spec("sync_ok"), handler=_noop, payload={})


async def _sc_async_success():
    async def h(_ctx, _p):
        return {"ok": 1}
    return await capture_invocation(spec=_spec("async_ok"), handler=h, payload={})


async def _sc_explicit_partial():
    def h(_ctx, _p):
        return CapabilityResult(status="partial", output={"half": 1}, error="cut short",
                                metadata={"pages_done": 3})
    return await capture_invocation(spec=_spec("partial_cap"), handler=h, payload={})


async def _sc_invalid_output():
    def h(_ctx, _p):
        return {"wrong": "shape"}
    return await capture_invocation(spec=_spec("bad_out", output_model=_Out), handler=h, payload={})


async def _sc_handler_exception():
    def h(_ctx, _p):
        raise ValueError("boom")
    return await capture_invocation(spec=_spec("raiser"), handler=h, payload={})


async def _sc_handler_owned_timeout():
    def h(_ctx, _p):
        raise TimeoutError("my own timeout, not the engine's")
    return await capture_invocation(spec=_spec("self_timeout"), handler=h, payload={})


def _windowed_ctx(timeout_s: float):
    from ai_workflow_engine.execution_window import TaskExecutionRequest
    return _fixed_context(execution_request=TaskExecutionRequest(timeout_s=timeout_s))


async def _sc_cooperative_deadline():
    async def h(_ctx, _p):
        await asyncio.sleep(5.0)
        return {"unreached": True}
    return await capture_invocation(
        spec=_spec("slow_async"), handler=h, payload={}, context=_windowed_ctx(0.3)
    )


async def _sc_swallowed_cancellation():
    async def h(_ctx, _p):
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            return {"swallowed": True}  # refuses to stop → containment failure
        return {"unreached": True}
    return await capture_invocation(
        spec=_spec("swallower"), handler=h, payload={}, context=_windowed_ctx(0.3)
    )


async def _sc_caller_cancellation():
    started = asyncio.Event()

    async def h(_ctx, _p):
        started.set()
        await asyncio.sleep(30.0)
        return {"unreached": True}

    registry = CapabilityRegistry()
    registry.register(_spec("blocker"), h)
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    task = asyncio.ensure_future(runtime.invoke("blocker", {}, _fixed_context()))
    await started.wait()
    task.cancel()
    cancelled = False
    try:
        await task
    except asyncio.CancelledError:
        cancelled = True
    except BaseException:
        cancelled = False
    amap = _build_alias_map(trace.events, [], [])
    return {
        "result": None,
        "exception": {"type": "CancelledError"} if cancelled else {"type": "other"},
        "handler_calls": 1,
        "trace": [_canon_event(e, amap) for e in trace.events],
        "details": [],
        "usage": None,
    }


async def _sc_artifact_result():
    def h(_ctx, _p):
        return CapabilityResult(
            status="accepted", output={"ok": 1},
            artifacts=[WorkflowArtifact(path="a.png", kind="media", source="x", owner_node="art")],
        )
    return await capture_invocation(spec=_spec("art"), handler=h, payload={}, capture="full")


async def _sc_capture_off():
    return await capture_invocation(spec=_spec("cap_off"), handler=_noop, payload={}, capture="off")


async def _sc_capture_full():
    return await capture_invocation(spec=_spec("cap_full"), handler=_noop, payload={}, capture="full")


async def _sc_trace_sink_failure():
    class _Boom:
        def record(self, _event):
            raise RuntimeError("trace sink down")
    return await capture_invocation(spec=_spec("t_boom"), handler=_noop, payload={}, trace_sink=_Boom())


async def _sc_detail_sink_failure():
    class _BoomDetail:
        def record(self, _detail):
            raise RuntimeError("detail sink down")
        def clear(self):
            pass
    return await capture_invocation(
        spec=_spec("d_boom"), handler=_noop, payload={}, capture="full", detail_sink=_BoomDetail()
    )


async def _sc_process_timeout():
    import sys
    from ai_workflow_engine.engine.external import ExternalProcessCapability, ExternalProcessRequest
    cap = ExternalProcessCapability(name="proc")
    registry = CapabilityRegistry()
    registry.register(cap.spec, cap)
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    req = ExternalProcessRequest(command=[sys.executable, "-c", "import time; time.sleep(30)"],
                                 timeout_s=0.4, kill_grace_s=0.5)
    result = await runtime.invoke("proc", req, _fixed_context(allowed_side_effects=["external_call"]))
    return {
        "result": {
            "kind": "result", "status": result.status,
            "output_returncode": (result.output or {}).get("returncode") if isinstance(result.output, dict) else None,
            "output_stdout_len": len((result.output or {}).get("stdout", "")) if isinstance(result.output, dict) else None,
            "error_has_timeout": "timed out" in (result.error or ""),
            "has_process_io": isinstance(result.metadata, dict) and "process_io" in result.metadata,
        },
        "exception": None,
        "handler_calls": 1,
        "trace": [_canon_event(e, _build_alias_map(trace.events, [], [])) for e in trace.events],
        "details": [],
        "usage": None,
    }


async def _sc_nested_invocation():
    from ai_workflow_engine.execution_window import TaskExecutionRequest
    child_windows: dict[str, Any] = {}

    def child(ctx, _p):
        win = ctx.execution_window
        child_windows["hard"] = win.hard_timeout_s if win is not None else None
        child_windows["sources"] = list(win.limiting_sources) if win is not None else None
        child_windows["clamps"] = list(win.clamps) if win is not None else None
        return {"child": 1}

    registry = CapabilityRegistry()
    registry.register(_spec("child"), child)

    async def parent(ctx, _p):
        child_ctx = ctx.model_copy(update={"execution_request": None})
        r = await runtime.invoke("child", {}, child_ctx)
        return {"child_status": r.status}

    registry.register(_spec("parent"), parent)
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    result = await runtime.invoke("parent", {}, _windowed_ctx(5.0))
    return {
        "result": {"kind": "result", "status": result.status,
                   "child_status": (result.output or {}).get("child_status") if isinstance(result.output, dict) else None},
        "child_window": {"sources": child_windows.get("sources"), "clamps": child_windows.get("clamps"),
                         "hard_positive_and_bounded": bool(child_windows.get("hard") and 0 < child_windows["hard"] <= 5.0)},
        "exception": None,
        "handler_calls": 2,
        "trace_node_set": sorted({e.node for e in trace.events}),
    }


async def _sc_concurrent_invocations():
    from ai_workflow_engine.execution_window import TaskExecutionRequest
    seen: dict[str, Any] = {}

    def cap(ctx, p):
        win = ctx.execution_window
        seen[p["tag"]] = win.hard_timeout_s if win is not None else None
        return {"tag": p["tag"]}

    registry = CapabilityRegistry()
    registry.register(_spec("c"), cap)
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())

    async def one(tag, timeout_s):
        await asyncio.sleep(0.02)
        ctx = _fixed_context(execution_request=TaskExecutionRequest(timeout_s=timeout_s))
        return await runtime.invoke("c", {"tag": tag}, ctx)

    wide, tight = await asyncio.gather(one("wide", 5.0), one("tight", 0.8))
    return {
        "wide_status": wide.status, "tight_status": tight.status,
        "wide_window_ok": bool(seen.get("wide") and 1.0 < seen["wide"] <= 5.0),
        "tight_window_ok": bool(seen.get("tight") and 0 < seen["tight"] <= 0.8),
        "isolation_ok": seen.get("wide") != seen.get("tight"),
    }


SCENARIOS: dict[str, Callable] = {
    "unknown_capability": _sc_unknown_capability,
    "invalid_input": _sc_invalid_input,
    "side_effect_denied": _sc_side_effect_denied,
    "budget_denied": _sc_budget_denied,
    "worker_call_accounting": _sc_worker_call_accounting,
    "sync_success": _sc_sync_success,
    "async_success": _sc_async_success,
    "explicit_partial": _sc_explicit_partial,
    "invalid_output": _sc_invalid_output,
    "handler_exception": _sc_handler_exception,
    "handler_owned_timeout": _sc_handler_owned_timeout,
    "cooperative_deadline": _sc_cooperative_deadline,
    "caller_cancellation": _sc_caller_cancellation,
    "swallowed_cancellation": _sc_swallowed_cancellation,
    "process_timeout": _sc_process_timeout,
    "artifact_result": _sc_artifact_result,
    "capture_off": _sc_capture_off,
    "capture_full": _sc_capture_full,
    "trace_sink_failure": _sc_trace_sink_failure,
    "detail_sink_failure": _sc_detail_sink_failure,
    "nested_invocation": _sc_nested_invocation,
    "concurrent_invocations": _sc_concurrent_invocations,
}


def missing_behavior_rows() -> list[str]:
    """Completeness guard: every behavior-inventory row must map to a scenario."""
    return [row for row in BEHAVIOR_ROWS if row not in SCENARIOS]


async def run_corpus() -> dict[str, Any]:
    """Run every scenario and return {name: canonical record}. Deterministic + serial."""
    out: dict[str, Any] = {}
    for name in sorted(SCENARIOS):
        out[name] = await SCENARIOS[name]()
    return out


def compare_records(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    """Structural equality by path. Returns a list of human-readable mismatches (empty == equal).
    A key present in only one side, or any leaf difference, is a mismatch — never auto-ignored."""

    mismatches: list[str] = []

    def _walk(path: str, a: Any, b: Any) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b), key=str):
                if k not in a:
                    mismatches.append(f"{path}.{k}: only in candidate = {b[k]!r}")
                elif k not in b:
                    mismatches.append(f"{path}.{k}: only in baseline = {a[k]!r}")
                else:
                    _walk(f"{path}.{k}", a[k], b[k])
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                mismatches.append(f"{path}: length {len(a)} != {len(b)}")
            for i, (x, y) in enumerate(zip(a, b)):
                _walk(f"{path}[{i}]", x, y)
        elif a != b:
            mismatches.append(f"{path}: {a!r} != {b!r}")

    for name in sorted(set(baseline) | set(candidate)):
        if name not in baseline:
            mismatches.append(f"{name}: scenario only in candidate")
        elif name not in candidate:
            mismatches.append(f"{name}: scenario only in baseline")
        else:
            _walk(name, baseline[name], candidate[name])
    return mismatches
