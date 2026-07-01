"""Step-node handler: capability dispatch, retries, scheduling/cancellation, policy denial.

Mechanical split of the executor god-file (spec §2c#5). Handlers receive a narrow
``NodeExecutionServices`` boundary object (``services``) — never the executor itself.
"""
from __future__ import annotations
import asyncio
import uuid
from typing import Any, Dict, List, Optional
from ai_workflow_engine.models import CapabilityContext, CapabilityResult, WorkflowTraceEvent
from ai_workflow_engine.workflow import WorkflowDefinition, WorkflowNode
from ai_workflow_engine._runtime_state import CONTEXT


def build_step_node(services, definition: WorkflowDefinition, node: WorkflowNode):
    capability = node.capability or node.id

    async def step_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        context: CapabilityContext = state[CONTEXT]
        payload = services.node_input(state, node)

        # Engine-owned pre-invocation gate: forbidden side effect / raw-media export /
        # budget exhaustion are blocked BEFORE the handler runs (fail-closed, traced).
        denial = _policy_denial(services, node, capability, state, context)
        if denial:
            rejected = CapabilityResult(
                status="rejected",
                error=f"denied by policy: {', '.join(denial)}",
                metadata={"denied": denial},
            )
            services.runtime.trace_sink.record(
                WorkflowTraceEvent(node=node.id, decision="denied", error=rejected.error, metadata={"denied": denial})
            )
            return services.record(
                state, node, rejected, attempts=1, input_payload=payload, force_status="failed", error=rejected.error
            )

        # Backend scheduling/backpressure: gate this node through the shared scheduler. The
        # backend slot is held until the worker actually completes (released in `finally`),
        # so a concurrent second call is dropped/queued and cancellation cannot free it early.
        sched = node.scheduling
        if sched is not None and sched.backend_key:
            lane = sched.backend_key
            run_id = uuid.uuid4().hex
            decision = services.scheduling.submit(key=lane, run_id=run_id, payload=payload, policy=sched)
            schedule_metadata = {
                "lane": lane,
                "reason": decision.reason,
                "run_id": run_id,
                "previous_run_id": decision.previous_run_id,
            }
            services.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=node.id,
                    decision=f"schedule:{decision.action}",
                    metadata={k: v for k, v in schedule_metadata.items() if v is not None},
                )
            )
            if decision.action in ("drop", "coalesce"):
                denied = CapabilityResult(
                    status="rejected",
                    error=f"scheduling {decision.action}: {decision.reason}",
                    metadata={"scheduling": decision.action, "lane": lane},
                )
                return services.record(
                    state, node, denied, attempts=1, input_payload=payload,
                    force_status="failed", error=denied.error,
                )
            if decision.action == "cancel_previous":
                cancel_error = await _cancel_previous_scheduled_run(services, 
                    lane=lane,
                    previous_run_id=decision.previous_run_id,
                    superseding_run_id=run_id,
                    node=node,
                )
                if cancel_error is not None:
                    denied = CapabilityResult(
                        status="failed",
                        error=cancel_error,
                        metadata={"scheduling": "cancel_previous", "lane": lane},
                    )
                    return services.record(
                        state, node, denied, attempts=1, input_payload=payload,
                        force_status="failed", error=cancel_error,
                    )
                if services.scheduling.active_run_id(lane) != run_id:
                    superseded = CapabilityResult(
                        status="failed",
                        error=f"scheduling superseded: run {run_id} was not promoted",
                        metadata={"scheduling": "superseded", "lane": lane, "run_id": run_id},
                    )
                    return services.record(
                        state, node, superseded, attempts=1, input_payload=payload,
                        force_status="failed", error=superseded.error,
                    )
                services.runtime.trace_sink.record(
                    WorkflowTraceEvent(
                        node=node.id,
                        decision="schedule:promote",
                        metadata={"lane": lane, "run_id": run_id, "after_run_id": decision.previous_run_id},
                    )
                )
            result = await _invoke_scheduled_bound(services, 
                node=node,
                capability=capability,
                payload=payload,
                context=context,
                state=state,
                lane=lane,
                run_id=run_id,
                attempt=1,
                definition=definition,
            )
            if result.status == "rejected":
                return services.record(
                    state, node, result, attempts=1, input_payload=payload,
                    force_status="failed", error=result.error or "rejected by policy",
                )
            return services.record(state, node, result, attempts=1, input_payload=payload)

        attempt_base = state.get("attempts", {}).get(node.id, 0)
        max_attempts = node.retry.max_attempts if node.retry else 1
        result: CapabilityResult = CapabilityResult(status="failed", error="not run")
        attempts = 0
        for offset in range(max_attempts):
            attempts = attempt_base + offset + 1
            result = await services.invoke_bound(node, capability, payload, context, state, attempt=attempts, definition=definition)
            if result.status != "failed":
                break
        if result.status == "rejected":
            # Runtime-level denial (defense in depth) is also fail-closed.
            return services.record(
                state, node, result, attempts=attempts, input_payload=payload,
                force_status="failed", error=result.error or "rejected by policy",
            )
        return services.record(state, node, result, attempts=attempts, input_payload=payload)

    return step_fn

async def _invoke_scheduled_bound(
    services,
    *,
    node: WorkflowNode,
    capability: str,
    payload: Any,
    context: CapabilityContext,
    state: Dict[str, Any],
    lane: str,
    run_id: str,
    attempt: int,
    definition: WorkflowDefinition,
) -> CapabilityResult:
    current_task = asyncio.current_task()
    if current_task is not None:
        services.scheduling.register_task(lane, run_id, current_task)
    try:
        result = await services.invoke_bound(node, capability, payload, context, state, attempt=attempt, definition=definition)
        superseded_by = services.scheduling.pop_superseded(lane, run_id)
        if superseded_by:
            return _cancelled_scheduled_result(services, 
                node=node, lane=lane, run_id=run_id, superseded_by=superseded_by
            )
        return result
    except asyncio.CancelledError:
        superseded_by = services.scheduling.pop_superseded(lane, run_id)
        if not superseded_by:
            raise
        return _cancelled_scheduled_result(services, 
            node=node, lane=lane, run_id=run_id, superseded_by=superseded_by
        )
    finally:
        services.scheduling.unregister_task(lane, run_id)
        services.scheduling.complete(key=lane, run_id=run_id)

async def _cancel_previous_scheduled_run(
    services,
    *,
    lane: str,
    previous_run_id: Optional[str],
    superseding_run_id: str,
    node: WorkflowNode,
) -> Optional[str]:
    if not previous_run_id:
        return f"scheduling cancel_previous had no previous run for lane {lane}"
    registered = services.scheduling.registered_task(lane)
    if registered is None or registered[0] != previous_run_id:
        # The previous worker already exited (its finally freed the slot and may have promoted
        # us) — there is nothing left to cancel. This is success for our purposes; the
        # promotion check downstream decides whether this run proceeds or is superseded.
        # Treating it as an error here would fail a possibly-already-promoted run without
        # ever releasing its slot, leaving the lane permanently stuck.
        services.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                decision="schedule:cancel_skipped",
                metadata={
                    "lane": lane,
                    "previous_run_id": previous_run_id,
                    "reason": "previous run already exited",
                },
            )
        )
        return None
    _run_id, task = registered
    services.scheduling.mark_superseded(lane, previous_run_id, superseding_run_id)
    services.runtime.trace_sink.record(
        WorkflowTraceEvent(
            node=node.id,
            decision="schedule:cancel_request",
            metadata={
                "lane": lane,
                "previous_run_id": previous_run_id,
                "superseding_run_id": superseding_run_id,
            },
        )
    )
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # Expected terminal state: the previous task ended cancelled. Scheduled nodes convert
        # engine-owned cancellation into a failed node record themselves; what matters here is
        # only that the worker has truly exited before promotion.
        services.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                decision="schedule:cancel_confirmed",
                metadata={"lane": lane, "previous_run_id": previous_run_id, "exit": "cancelled"},
            )
        )
    finally:
        # The cancelled run normally consumes its marker; if it exited through a path that
        # could not (already past its pop points), clean up so the dict cannot accumulate.
        services.scheduling.pop_superseded(lane, previous_run_id)
    return None

def _cancelled_scheduled_result(
    services,
    *,
    node: WorkflowNode,
    lane: str,
    run_id: str,
    superseded_by: str,
) -> CapabilityResult:
    error = f"cancelled: superseded by {superseded_by}"
    services.runtime.trace_sink.record(
        WorkflowTraceEvent(
            node=node.id,
            decision="schedule:cancelled",
            error=error,
            metadata={"lane": lane, "run_id": run_id, "superseded_by": superseded_by},
        )
    )
    return CapabilityResult(
        status="failed",
        error=error,
        metadata={
            "scheduling": "cancelled",
            "lane": lane,
            "run_id": run_id,
            "superseded_by": superseded_by,
        },
    )

def _policy_denial(
    services,
    node: WorkflowNode,
    capability: str,
    state: Dict[str, Any],
    context: CapabilityContext,
) -> List[str]:
    """Return the policy reasons this node may not be invoked (empty == allowed)."""

    plan = context.plan
    if plan is None:
        return []
    try:
        spec, _handler = services.runtime.registry.get(capability)
    except KeyError:
        return []  # missing capability is surfaced by pre-flight, not here
    # The profile's allowed_side_effects is the deployment ceiling. A node may never exceed it.
    allowed = set(plan.safety.allowed_side_effects)
    needed = set(spec.side_effects) | set(node.required_side_effects)
    denied = sorted(effect for effect in needed if effect not in allowed)
    # Raw-media export is privacy-sensitive: it additionally requires an explicit per-node
    # opt-in even when the profile permits it (double consent), and the node opt-in can never
    # grant it when the profile forbids it.
    if "raw_media_export" in spec.side_effects and not node.allow_raw_media_export:
        if "raw_media_export" not in denied:
            denied.append("raw_media_export:node-opt-in-required")
    if denied:
        return denied
    # Budget: a metered capability cannot run once the run's spend has reached the cap.
    if spec.metered and plan.limits and plan.limits.max_estimated_usd is not None:
        usage = state.get("usage_summary")
        spent = (getattr(usage, "estimated_usd", None) or 0.0) if usage is not None else 0.0
        if spent >= plan.limits.max_estimated_usd:
            return ["budget_exhausted"]
    return []
