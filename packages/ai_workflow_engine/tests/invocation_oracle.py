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

# REVIEWED volatile normalization (the ONLY one). The single genuinely-volatile fact in the whole
# corpus — proven by running run_corpus() twice — is the engine's cooperative-deadline message,
# whose seconds token is the REMAINING soft budget sampled from the monotonic clock (sub-ms
# jitter, e.g. "0.249988s" vs "0.249981s"). Rounding (2 decimals = 10ms resolution, magnitude-
# preserving) applies ONLY to that token inside that exact message shape, at the two observed
# leaves — an unrelated decimal anywhere (even inside the same error string, e.g. "model 3.141")
# is a FACT and still mismatches.
_DEADLINE_JITTER = re.compile(r"(exceeded its )(\d+\.\d+)(s execution window)")


def _round_deadline_seconds(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    return _DEADLINE_JITTER.sub(
        lambda m: f"{m.group(1)}{float(m.group(2)):.2f}{m.group(3)}", text
    )


def _round_cooperative_deadline_leaves(record: Any) -> Any:
    """Normalize the deadline-jitter token at its two OBSERVED leaves only: ``result.error`` and
    ``trace[*].error``. No recursion into details/metadata/exception — any other error field that
    changes is a behavior delta and must fail."""

    if not isinstance(record, dict):
        return record
    result = record.get("result")
    if isinstance(result, dict) and isinstance(result.get("error"), str):
        result["error"] = _round_deadline_seconds(result["error"])
    for event in record.get("trace") or []:
        if isinstance(event, dict) and isinstance(event.get("error"), str):
            event["error"] = _round_deadline_seconds(event["error"])
    return record


# Exact-path volatile normalizers, keyed by scenario. Empty for every scenario except the one with
# proven wall jitter. A new entry is a REVIEWED decision, not a blanket rule.
_EXACT_PATH_NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "cooperative_deadline": _round_cooperative_deadline_leaves,
}


def _normalize_record(name: str, record: Any) -> Any:
    fn = _EXACT_PATH_NORMALIZERS.get(name)
    return fn(record) if fn else record

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
    "bounded_caller_cancellation",
    "swallowed_cancellation",
    "unacknowledged_cancellation",
    "process_timeout",
    "artifact_result",
    "capture_off",
    "capture_full",
    "trace_sink_failure",
    "detail_sink_failure",
    "nested_invocation",
    "concurrent_invocations",
    "worker_call_accounting",
    "usage_event_semantics",
    "authored_fanout_flow",
    "durable_suspend_resume",
    "human_bound_context",
)

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
    """Canonicalize a value for cross-version comparison: sort dict keys, alias generated ids, and
    stringify opaque objects deterministically. It does NOT erase keys or round decimals — that
    over-broad normalization (which made 1s and 99s, and 3.141 and 3.144, compare equal) is gone.
    The ONLY volatile-fact normalization now happens at reviewed EXACT PATHS in _normalize_paths."""

    amap = amap or {}
    if isinstance(value, dict):
        return {k: _canon(value[k], amap) for k in sorted(value.keys(), key=str)}
    if isinstance(value, (list, tuple)):
        return [_canon(v, amap) for v in value]
    if isinstance(value, str):
        return _apply_aliases(value, amap)
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
        # QRF.5 TYPED terminal lifecycle — the viewer reads THIS, so it is a frozen fact
        # (None on non-terminal events is itself the fact to preserve).
        "node_status": getattr(event, "node_status", None),
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
    active). Drops only each event's generated UUID; all caller-supplied event facts and every
    public summary aggregate remain part of the preservation contract."""

    if context is None:
        return None
    return _canon_usage_summary(getattr(context, "summary", None))


# REVIEWED volatile drop-list for usage events — the inverse of a hand-picked keep-list, so any
# NEW model field is automatically a compared fact. The only entry is the per-event generated
# uuid; run_id/metadata enrichment is deterministic under the oracle's fixed run context, and
# request_id/elapsed_ms/sequence are caller-supplied (fixed) in every scenario.
_VOLATILE_USAGE_EVENT_FIELDS = frozenset({"event_id"})


def _canon_usage_event(event: Any) -> dict[str, Any]:
    dumped = event.model_dump(mode="json")
    return _canon({k: v for k, v in dumped.items() if k not in _VOLATILE_USAGE_EVENT_FIELDS})


def _canon_usage_summary(summary: Any) -> dict[str, Any] | None:
    """Canonical form of a WorkflowUsageSummary (engine-run envelopes carry one directly): the
    full aggregation surface plus EVERY semantic field of every event (model_dump minus the
    reviewed volatile drop-list) — attempt, token details, per-event costs, metadata, and
    cost-class classification are all compared facts."""

    if summary is None:
        return None
    events = getattr(summary, "events", None) or []
    return {
        "worker_call_count": getattr(summary, "worker_call_count", None),
        "text_call_count": getattr(summary, "text_call_count", None),
        "image_call_count": getattr(summary, "image_call_count", None),
        "tool_call_count": getattr(summary, "tool_call_count", None),
        "tool_character_count": getattr(summary, "tool_character_count", None),
        "input_tokens": getattr(summary, "input_tokens", None),
        "output_tokens": getattr(summary, "output_tokens", None),
        "total_tokens": getattr(summary, "total_tokens", None),
        "cached_input_tokens": getattr(summary, "cached_input_tokens", None),
        "metered_usd": getattr(summary, "metered_usd", None),
        "notional_usd": getattr(summary, "notional_usd", None),
        "estimated_usd": getattr(summary, "estimated_usd", None),
        "events": [_canon_usage_event(ev) for ev in events],
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
            {"type": type(raw_exc).__name__, "message": _apply_aliases(str(raw_exc), amap)}
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
    from ai_workflow_engine.budget import WorkflowUsageContext, workflow_usage_scope
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
    from ai_workflow_engine.budget import WorkflowUsageContext, workflow_usage_scope
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


async def _sc_bounded_caller_cancellation():
    """Caller cancellation of a BOUNDED invocation (I2.0 gap found by mutation m3): the bounded
    supervisor owns a child when the caller cancels, and the cancellation must propagate
    UNTRANSLATED (never laundered into a partial timeout) after the child is settled. The
    unbounded variant is the caller_cancellation row; this one enters the bounded-await path."""

    started = asyncio.Event()

    async def h(_ctx, _p):
        started.set()
        await asyncio.sleep(30.0)
        return {"unreached": True}

    registry = CapabilityRegistry()
    registry.register(_spec("blocker_bounded"), h)
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    task = asyncio.ensure_future(runtime.invoke("blocker_bounded", {}, _windowed_ctx(0.8)))
    await started.wait()
    task.cancel()
    cancelled = False
    result_rec = None
    try:
        result = await task
        result_rec = _canon_result(result)
    except asyncio.CancelledError:
        cancelled = True
    except BaseException:
        cancelled = False
    amap = _build_alias_map(trace.events, [], [])
    return {
        "result": result_rec,
        "exception": {"type": "CancelledError"} if cancelled else {"type": "other"},
        "handler_calls": 1,
        "trace": [_canon_event(e, amap) for e in trace.events],
        "details": [],
        "usage": None,
    }


async def _sc_unacknowledged_cancellation():
    """A child that refuses to acknowledge cancellation within the grace window (I2.0 gap found
    by mutation m4): it swallows every CancelledError and keeps working past the boundary, so the
    engine must report a containment FAILURE ("did not acknowledge cancellation within ...s"),
    never a clean partial timeout. The child is stubborn for a bounded wall time so the corpus
    does not leak a spinning task."""

    import time as _time

    async def h(_ctx, _p):
        deadline = _time.monotonic() + 1.0
        while _time.monotonic() < deadline:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                pass  # refuse to stop while the "critical section" runs
        return {"late": True}

    return await capture_invocation(
        spec=_spec("unacknowledging"), handler=h, payload={}, context=_windowed_ctx(0.3)
    )


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
    """Nested public-door invocation: the inheriting child sees the parent-soft clamp, a child
    with its OWN small window sees that bound while inside it, and — the I1.R4 restoration proof —
    the parent's AMBIENT window is back after each child returns (the child's published scope was
    reset, not leaked). All facts are booleans/structures stable across wall-clock jitter."""

    from ai_workflow_engine.execution_window import TaskExecutionRequest, invocation_window_remaining_s

    child_windows: dict[str, Any] = {}
    ambient: dict[str, Any] = {}

    def inheriting_child(ctx, _p):
        win = ctx.execution_window
        child_windows["sources"] = list(win.limiting_sources) if win is not None else None
        child_windows["clamps"] = list(win.clamps) if win is not None else None
        child_windows["hard"] = win.hard_timeout_s if win is not None else None
        return {"child": 1}

    def bounded_child(_ctx, _p):
        rem = invocation_window_remaining_s()
        ambient["inside_bounded_child_hard_s"] = rem.hard_s if rem is not None else None
        return {"child": 2}

    registry = CapabilityRegistry()
    registry.register(_spec("inherit_child"), inheriting_child)
    registry.register(_spec("bounded_child"), bounded_child)

    async def parent(ctx, _p):
        before = invocation_window_remaining_s()
        ambient["parent_before"] = before.hard_s if before is not None else None
        r1 = await runtime.invoke("inherit_child", {}, ctx.model_copy(update={"execution_request": None}))
        r2 = await runtime.invoke(
            "bounded_child", {},
            ctx.model_copy(update={"execution_request": TaskExecutionRequest(timeout_s=0.5)}),
        )
        after = invocation_window_remaining_s()
        ambient["parent_after"] = after.hard_s if after is not None else None
        return {"child_statuses": [r1.status, r2.status]}

    registry.register(_spec("parent"), parent)
    trace = InMemoryTraceSink()
    runtime = CapabilityRuntime(registry, trace)
    result = await runtime.invoke("parent", {}, _windowed_ctx(5.0))
    par_before = ambient.get("parent_before")
    par_after = ambient.get("parent_after")
    inside_bounded = ambient.get("inside_bounded_child_hard_s")
    return {
        "result": {"kind": "result", "status": result.status,
                   "child_statuses": (result.output or {}).get("child_statuses") if isinstance(result.output, dict) else None},
        "child_window": {"sources": child_windows.get("sources"), "clamps": child_windows.get("clamps"),
                         "hard_positive_and_bounded": bool(child_windows.get("hard") and 0 < child_windows["hard"] <= 5.0)},
        "ambient_scope": {
            # parent ambient present and parent-sized before children
            "parent_before_is_parent_sized": bool(par_before and 1.0 < par_before <= 5.0),
            # the bounded child saw ITS OWN 0.5s window while inside it
            "bounded_child_saw_own_window": bool(inside_bounded is not None and inside_bounded <= 0.5),
            # RESTORE: after both children the ambient window is the parent's again — a leaked
            # child scope would leave <= 0.5s here
            "parent_restored_after_children": bool(par_after and 1.0 < par_after <= 5.0),
        },
        "exception": None,
        "handler_calls": 3,
        "trace_node_set": sorted({e.node for e in trace.events}),
    }


async def _sc_concurrent_invocations():
    """Concurrent public-door invocations with a BARRIER, not a sleep: each handler waits for the
    OTHER to have started before reading its window, so both are provably mid-flight together —
    completion itself certifies real interleaving (a serialized runtime would deadlock the barrier
    and fail loudly via wait_for). Frozen facts: per-invocation window isolation AND ambient
    ContextVar isolation (each task's published scope shows its own bound while both are alive)."""

    from ai_workflow_engine.execution_window import TaskExecutionRequest, invocation_window_remaining_s

    wide_started, tight_started = asyncio.Event(), asyncio.Event()
    seen: dict[str, Any] = {}

    async def cap(ctx, p):
        tag = p["tag"]
        if tag == "wide":
            wide_started.set()
            await asyncio.wait_for(tight_started.wait(), timeout=5.0)
        else:
            tight_started.set()
            await asyncio.wait_for(wide_started.wait(), timeout=5.0)
        # both invocations are in flight PAST this line
        rem = invocation_window_remaining_s()
        win = ctx.execution_window
        seen[tag] = {
            "window_hard": win.hard_timeout_s if win is not None else None,
            "ambient_hard_s": rem.hard_s if rem is not None else None,
        }
        return {"tag": tag}

    registry = CapabilityRegistry()
    registry.register(_spec("c"), cap)
    runtime = CapabilityRuntime(registry, InMemoryTraceSink())

    async def one(tag, timeout_s):
        ctx = _fixed_context(execution_request=TaskExecutionRequest(timeout_s=timeout_s))
        return await runtime.invoke("c", {"tag": tag}, ctx)

    wide, tight = await asyncio.gather(one("wide", 5.0), one("tight", 0.8))
    wide_seen, tight_seen = seen.get("wide") or {}, seen.get("tight") or {}
    return {
        "wide_status": wide.status, "tight_status": tight.status,
        "interleaved": bool(wide_started.is_set() and tight_started.is_set()),
        "wide_window_ok": bool(wide_seen.get("window_hard") and 1.0 < wide_seen["window_hard"] <= 5.0),
        "tight_window_ok": bool(tight_seen.get("window_hard") and 0 < tight_seen["window_hard"] <= 0.8),
        "window_isolation_ok": wide_seen.get("window_hard") != tight_seen.get("window_hard"),
        # ContextVar isolation while BOTH are alive: each task's ambient scope carries its own
        # bound; cross-contamination would show the other invocation's remaining time.
        "ambient_isolation_ok": bool(
            wide_seen.get("ambient_hard_s") is not None
            and tight_seen.get("ambient_hard_s") is not None
            and wide_seen["ambient_hard_s"] > 1.0
            and tight_seen["ambient_hard_s"] <= 0.8
        ),
    }


async def _sc_usage_event_semantics():
    """P-06 depth: a worker capability that EMITS real usage events through the engine's one
    usage door (record_usage_event, unchanged since v0.10.1) while inside the invocation scope.
    Every semantic WorkflowUsageEvent field is deterministic and frozen: metered AND
    subscription_notional classification, attempt attribution, cached/reasoning token details,
    per-event estimated/notional USD, request/elapsed facts, a failure event's success/error, and
    the door's own metadata enrichment (run/workflow identity under the fixed oracle context)."""

    from ai_workflow_engine.budget import WorkflowBudget
    from ai_workflow_engine.budget import WorkflowUsageContext, workflow_usage_scope
    from ai_workflow_engine.models import WorkflowUsageEvent, WorkflowUsageSummary
    from ai_workflow_engine.usage_events import record_usage_event

    def metered_worker(_ctx, _p):
        record_usage_event(WorkflowUsageEvent(
            provider="openai", operation="chat", cost_class="metered", node="metered_worker",
            model="gpt-fixed", attempt=2,
            input_tokens=100, output_tokens=40, total_tokens=140,
            input_token_details={"cached_tokens": 25},
            output_token_details={"reasoning_tokens": 15},
            estimated_usd=0.0123, request_id="req-fixed", elapsed_ms=7,
        ))
        record_usage_event(WorkflowUsageEvent(
            provider="chatgpt-web", operation="chat", cost_class="subscription_notional",
            node="metered_worker", model="subscription-fixed", attempt=1,
            input_tokens=10, output_tokens=5, total_tokens=15,
            notional_usd=0.002, request_id="req-sub", elapsed_ms=3,
        ))
        record_usage_event(WorkflowUsageEvent(
            provider="openai", operation="tool", cost_class="metered", node="metered_worker",
            model="tool-fixed", attempt=1, success=False, error="tool call failed: quota",
            input_token_details={"characters": 17},
            request_id="req-tool", elapsed_ms=5,
        ))
        return {"ok": 1}

    ctx = _fixed_context()
    usage = WorkflowUsageContext(ctx.run_context, WorkflowUsageSummary(), WorkflowBudget(max_worker_calls=5))
    with workflow_usage_scope(usage):
        return await capture_invocation(
            spec=_spec("metered_worker", kind="agent"), handler=metered_worker, payload={}, context=ctx
        )


async def _sc_authored_fanout_flow():
    """Behavior row 'authored flow/fanout' through the public door: an AI-authored FlowArtifact
    containing a bounded fanout node is validated + compiled + run by run_authored_flow. Frozen
    facts: parent/child statuses, per-child outcomes (one child fails → partial-failure isolation),
    ordered capability-door trace for the fanout children, and the flow:authored provenance mark."""

    from ai_workflow_engine import WorkflowEngineBuilder

    builder = WorkflowEngineBuilder()
    builder.register_capability("seed", lambda ctx, p: {"items": ["alpha", "bad", "gamma"]})

    def shout(_ctx, item):
        if item == "bad":
            raise ValueError("cannot shout 'bad'")
        return {"up": str(item).upper()}

    builder.register_capability("shout", shout)
    engine = builder.build()

    artifact = {
        "flow_id": "fan_flow",
        "goal": "seed then shout each item",
        "nodes": [
            {"kind": "step", "id": "seed"},
            {"kind": "fanout", "id": "shout", "items_key": "seed.items",
             "max_parallel": 1, "max_items": 8, "output_key": "ups"},
        ],
    }
    result = await engine.run_authored_flow(artifact, {"text": "hi"})

    trace = list(result.trace)
    amap = _build_alias_map(trace, [], list(result.artifacts))
    door_events = [_canon_event(e, amap) for e in trace if e.node in ("seed", "shout")]
    return {
        "result": {
            "kind": "result",
            "status": result.status,
            "output": _canon(result.output, amap),
            "error": _canon(result.error, amap) if result.error else result.error,
        },
        "node_results": [[n.node_id, n.status] for n in result.node_results],
        "authored_provenance": any(e.decision == "flow:authored" for e in trace),
        "capability_door_trace": door_events,
        "usage": _canon_usage_summary(result.usage),
        "exception": None,
    }


async def _sc_durable_suspend_resume():
    """Behavior row 'durable suspend/resume' through the public door: a DurableWaitPolicy human
    gate suspends the run (wait_handle, NO local snapshot), deliver_wait_event resumes it with the
    claimed signal, and the grouped lifecycle completes. Frozen facts: both segment statuses, the
    wait identity (workflow/node), the delivery outcome kind, the post-wait idempotency contract
    (wait_id:event_id visible to post-wait capabilities), node results across BOTH segments, the
    coordinator's terminal wait status/resolution, and the cumulative usage summary."""

    from ai_workflow_engine import (
        DurableWaitPolicy,
        InMemoryWaitCoordinator,
        WorkflowBuilder,
        WorkflowEngineBuilder,
    )
    from pydantic import BaseModel as _GateBM

    class Gate(_GateBM):
        status: str
        value: str = ""

    def gate(ctx, _p):
        event = ctx.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending")
        return Gate(status="answered", value=str(event))

    def finish(ctx, p):
        return {"answer": p.value, "wait_idempotency": ctx.metadata.get("wait_idempotency")}

    import datetime as _dt

    def _frozen_clock() -> _dt.datetime:
        # deterministic time source: every wait stamp/deadline is identical across runs AND wheels
        return _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)

    coordinator = InMemoryWaitCoordinator(clock=_frozen_clock)
    builder = WorkflowEngineBuilder().with_wait_coordinator(coordinator, clock=_frozen_clock)
    builder.register_capability("gate", gate)
    builder.register_capability("finish", finish)
    builder.register_capability("escalate", lambda ctx, p: {"escalated": True})
    builder.register_workflow(
        WorkflowBuilder("dur_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=60), timeout_to="escalate")
        .step("finish")
        .step("escalate")
        .build()
    )
    engine = builder.build()

    first = await engine.run("dur_flow", {})
    handle = first.wait_handle
    outcome = await engine.deliver_wait_event(
        handle.wait_id, {"kind": "signal", "event_id": "evt-1", "payload": "approved"}
    )
    stored = await coordinator.get(handle.wait_id)
    final = outcome.run_result
    return {
        "first_status": first.status,
        "suspension_door": {
            "has_wait_handle": handle is not None,
            "has_local_snapshot": first.snapshot is not None,
            "workflow_id": handle.workflow_id,
            "suspended_node": handle.suspended_node,
        },
        "delivery": {
            "kind": outcome.kind,
            "final_status": final.status,
            "answer": (final.output or {}).get("answer") if isinstance(final.output, dict) else None,
            "idempotency_contract_held": (
                isinstance(final.output, dict)
                and final.output.get("wait_idempotency") == f"{handle.wait_id}:evt-1"
            ),
        },
        "node_results": [[n.node_id, n.status] for n in final.node_results],
        "wait_terminal": {"status": stored.status, "resolution_kind": stored.resolution_kind},
        "usage": _canon_usage_summary(final.usage),
        "exception": None,
    }


async def _sc_human_bound_context():
    """Behavior row 'human bound context' (B0, v0.11.3): human nodes are DECLARED nodes — the
    bound invocation door composes ALL FOUR node decorations (plan card, machine card, agent-
    memory config, model binding) with retrace provenance, criticism payload, and resume events
    across two suspend/resume cycles. Frozen facts: the per-visit decoration/provenance/resume
    matrix (4 visits: ask, answer, retraced re-ask, answer), the verbatim visit-1 cards, the
    per-segment model_binding trace counts, all three segment statuses, final node results, the
    terminal output, and the cumulative usage summary."""

    from pydantic import BaseModel as _AskBM

    from ai_workflow_engine import (
        CapabilityResult,
        CapabilitySpec,
        LocalWaitPolicy,
        PlanArtifact,
        PlanTask,
        WorkflowBuilder,
        WorkflowEngineBuilder,
    )
    from ai_workflow_engine.models import (
        ModelProfile,
        RuntimeLimits,
        SafetyPolicy,
        WorkflowProfile,
    )
    from ai_workflow_engine.workflow import Retrace

    class Ask(_AskBM):
        status: str
        value: Any = None

    visits: list[dict[str, Any]] = []
    visit1_cards: dict[str, Any] = {}

    def make_plan(_ctx, _p):
        return PlanArtifact(
            goal="approve the launch brief",
            tasks=[
                PlanTask(
                    task_id="t1",
                    description="draft the brief",
                    capability="draft",
                    payload={"value": 1},
                )
            ],
        )

    def draft(_ctx, p):
        return {"brief": "launch brief v1"}

    def ask(ctx, payload):
        prov = getattr(ctx, "retrace_provenance", None)
        if not visits:
            # verbatim visit-1 cards: pure deterministic functions of the definition + plan
            visit1_cards["plan"] = ctx.metadata.get("plan")
            visit1_cards["machine"] = ctx.metadata.get("machine")
        visits.append(
            {
                "provenance": None
                if prov is None
                else {
                    "round": prov.round,
                    "evaluator": prov.evaluator_node,
                    "target": prov.target_node,
                },
                "criticism": isinstance(payload, dict) and "_criticism" in payload,
                "resume_event": ctx.metadata.get("resume_event"),
                "agent_memory": ctx.metadata.get("agent_memory"),
                "model_profile": getattr(ctx.model_profile, "name", None),
                "has_plan_card": isinstance(ctx.metadata.get("plan"), str),
                "has_machine_card": isinstance(ctx.metadata.get("machine"), str),
            }
        )
        event = ctx.metadata.get("resume_event")
        if event is None:
            return Ask(status="pending")
        return Ask(status="answered", value=event)

    gate_calls = {"n": 0}

    def gate(_ctx, _p):
        gate_calls["n"] += 1
        return CapabilityResult(
            status="accepted" if gate_calls["n"] > 1 else "rejected",
            error="needs one more round",
        )

    def fin(_ctx, _p):
        return {"done": True}

    builder = WorkflowEngineBuilder()
    builder.with_profile(
        WorkflowProfile(
            workflow_type="human_bound",
            limits=RuntimeLimits(timeout_s=None),
            safety=SafetyPolicy(allowed_side_effects=[]),
        )
    )
    builder.with_model_profile(
        ModelProfile(name="human_probe", provider="ollama", model="probe-1", temperature=0.0)
    )
    builder.register_capability(
        "make_plan", make_plan, spec=CapabilitySpec(name="make_plan", kind="llm", is_planner=True)
    )
    builder.register_capability("draft", draft)
    builder.register_capability("ask", ask)
    builder.register_capability("gate", gate)
    builder.register_capability("fin", fin)
    builder.register_workflow(
        WorkflowBuilder("human_bound")
        .plan("plan_node", capability="make_plan")
        .step("draft")
        .human(
            "ask",
            wait_policy=LocalWaitPolicy(),
            inject_plan=True,
            inject_machine=True,
            memory="structured_state",
            model_profile="human_probe",
            description="bound approval gate",
        )
        .evaluate("qa", target="ask", evaluator="gate", on_reject=Retrace("ask"))
        .step("fin")
        .build()
    )
    engine = builder.build()

    first = await engine.run("human_bound", {"question": "approve?"})
    second = await engine.resume(first.snapshot, {"answer": "revise"})
    third = await engine.resume(second.snapshot, {"answer": "ship"})

    def _bindings(result) -> int:
        return sum(
            1
            for e in result.trace
            if e.decision == "model_binding"
            and e.node == "ask"
            and e.metadata.get("model_profile_requested") == "human_probe"
        )

    return {
        "statuses": [first.status, second.status, third.status],
        "visit_matrix": visits,
        "visit1_cards": visit1_cards,
        "binding_trace_per_segment": [_bindings(first), _bindings(second), _bindings(third)],
        "node_results": [[n.node_id, n.status] for n in third.node_results],
        "final_output": third.output,
        "usage": _canon_usage_summary(third.usage),
        "exception": None,
    }


SCENARIOS: dict[str, Callable] = {
    "unknown_capability": _sc_unknown_capability,
    "invalid_input": _sc_invalid_input,
    "side_effect_denied": _sc_side_effect_denied,
    "budget_denied": _sc_budget_denied,
    "worker_call_accounting": _sc_worker_call_accounting,
    "usage_event_semantics": _sc_usage_event_semantics,
    "sync_success": _sc_sync_success,
    "async_success": _sc_async_success,
    "explicit_partial": _sc_explicit_partial,
    "invalid_output": _sc_invalid_output,
    "handler_exception": _sc_handler_exception,
    "handler_owned_timeout": _sc_handler_owned_timeout,
    "cooperative_deadline": _sc_cooperative_deadline,
    "caller_cancellation": _sc_caller_cancellation,
    "bounded_caller_cancellation": _sc_bounded_caller_cancellation,
    "swallowed_cancellation": _sc_swallowed_cancellation,
    "unacknowledged_cancellation": _sc_unacknowledged_cancellation,
    "process_timeout": _sc_process_timeout,
    "artifact_result": _sc_artifact_result,
    "capture_off": _sc_capture_off,
    "capture_full": _sc_capture_full,
    "trace_sink_failure": _sc_trace_sink_failure,
    "detail_sink_failure": _sc_detail_sink_failure,
    "nested_invocation": _sc_nested_invocation,
    "concurrent_invocations": _sc_concurrent_invocations,
    "authored_fanout_flow": _sc_authored_fanout_flow,
    "durable_suspend_resume": _sc_durable_suspend_resume,
    "human_bound_context": _sc_human_bound_context,
}


def missing_behavior_rows() -> list[str]:
    """Completeness guard: every behavior-inventory row must map to a scenario."""
    return [row for row in BEHAVIOR_ROWS if row not in SCENARIOS]


def public_surface_snapshot() -> dict[str, Any]:
    """Version-agnostic snapshot of the stable public surface (hardened per review finding 4):
    package exports as a KEY-ADDRESSABLE map (a removed/added export names itself in the delta
    path), the CapabilityRuntime constructor/invoke signatures, and the FULL normalized JSON
    schema of EVERY exported pydantic model — so any public model's validator/default/constraint
    drift (MachineSnapshot, LLMRequest, ObservationConfig, ...) is a sealed fact, not a blind
    spot. Runs identically in the sealer venv and the gate container."""

    import inspect
    import ai_workflow_engine as pkg
    from pydantic import BaseModel as _PydanticBase

    def _sig(obj) -> str:
        try:
            return str(inspect.signature(obj))
        except (TypeError, ValueError):
            return "<no-signature>"

    export_names = sorted(getattr(pkg, "__all__", []) or [n for n in dir(pkg) if not n.startswith("_")])
    schemas: dict[str, Any] = {}
    for name in export_names:
        try:
            obj = getattr(pkg, name)
        except Exception as exc:  # a broken lazy export is itself a sealed fact
            schemas[name] = f"<unresolvable: {type(exc).__name__}>"
            continue
        if isinstance(obj, type) and issubclass(obj, _PydanticBase):
            try:
                schemas[name] = obj.model_json_schema()
            except Exception as exc:
                schemas[name] = f"<schema-error: {type(exc).__name__}>"

    return {
        "exports": {name: "export" for name in export_names},
        "signatures": {
            "CapabilityRuntime.__init__": _sig(CapabilityRuntime.__init__),
            "CapabilityRuntime.invoke": _sig(CapabilityRuntime.invoke),
            # B0 (v0.11.3): the human-node builder contract is sealed so future signature
            # drift (decorations added/removed/renamed) is a visible public delta.
            "WorkflowBuilder.human": _sig(pkg.WorkflowBuilder.human),
        },
        "schemas": schemas,
    }


async def run_corpus() -> dict[str, Any]:
    """Run every scenario and return {name: canonical record}. Deterministic + serial. Exact-path
    volatile normalization (only the cooperative-deadline wall jitter) is applied per scenario."""
    out: dict[str, Any] = {}
    for name in sorted(SCENARIOS):
        out[name] = _normalize_record(name, await SCENARIOS[name]())
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
