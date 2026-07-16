"""Planner-node handler: plan-as-data validation, bounded recursive execution, replan.

Mechanical split of the executor god-file (spec §2c#5). Handlers receive a narrow
``NodeExecutionServices`` boundary object (``services``) — never the executor itself.
"""
from __future__ import annotations
import asyncio
from typing import Any, Dict, Optional
from pydantic import ValidationError
from ai_workflow_engine.models import CapabilityContext, CapabilityResult, WorkflowArtifact, WorkflowTraceEvent
from ai_workflow_engine.byte_safety import assert_byte_safe
from ai_workflow_engine.models import RuntimeLimits as _RL
_DEFAULT_LIMITS = _RL()
from ai_workflow_engine.planning import PlanArtifact, PlanTask
from ai_workflow_engine.workflow import WorkflowDefinition, WorkflowNode
from ai_workflow_engine._runtime_state import CONTEXT, RUNNING_PAYLOAD


def build_planner_node(services, definition: WorkflowDefinition, node: WorkflowNode):
    planner_capability = node.capability or node.id

    async def planner_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        context: CapabilityContext = state[CONTEXT]
        payload = services.node_input(state, node)
        attempt = state.get("attempts", {}).get(node.id, 0) + 1

        resume_plan = _coerce_plan_artifact(payload)
        if resume_plan is None:
            planner_result = await services.invoke_bound(
                node, planner_capability, payload, context, state, attempt=attempt
            , definition=definition)
            if planner_result.status in ("failed", "rejected"):
                return services.record(
                    state,
                    node,
                    planner_result,
                    attempts=attempt,
                    input_payload=payload,
                    force_status="failed",
                    error=planner_result.error or "planner failed",
                )
            plan_errors: list[str] = []
            plan = _coerce_plan_artifact(planner_result.output, errors=plan_errors)
            if plan is None:
                error = f"planner '{node.id}' did not emit a PlanArtifact-compatible output"
                if plan_errors:
                    error += f": {'; '.join(plan_errors)}"
                failed = CapabilityResult(
                    status="failed",
                    error=error,
                )
                return services.record(
                    state, node, failed, attempts=attempt, input_payload=payload
                )
            prior_plan = _coerce_plan_artifact(state.get("plan_artifact"))
            if prior_plan is not None:
                plan = _merge_replanned_plan(prior_plan, plan)
        else:
            plan = resume_plan
            planner_result = CapabilityResult(status="accepted", output=plan)

        _record_planner_output(services, node, plan, attempt)
        validation_errors = _validate_plan(services, node, plan, context)
        if validation_errors:
            error = "planner validation failed: " + "; ".join(validation_errors)
            services.runtime.trace_sink.record(
                WorkflowTraceEvent(
                    node=node.id,
                    attempt=attempt,
                    decision="plan:validation_failed",
                    error=error,
                    metadata={"errors": validation_errors},
                )
            )
            failed = CapabilityResult(status="failed", output=plan, error=error)
            update = services.record(
                state, node, failed, attempts=attempt, input_payload=payload
            )
            update["plan_artifact"] = plan
            return update

        executed_plan, task_outputs, task_artifacts, task_failures = await _execute_plan(services, 
            node=node,
            plan=plan,
            context=context,
            state=state,
        )
        if task_outputs:
            retained_outputs = _merge_task_outputs(
                executed_plan.metadata.get("task_outputs"), task_outputs
            )
            executed_plan = executed_plan.model_copy(
                update={
                    "metadata": {
                        **executed_plan.metadata,
                        "task_outputs": retained_outputs,
                    }
                }
            )
        partial_count = sum(1 for task in executed_plan.tasks if task.status == "partial")
        status = "partial" if (task_failures or partial_count) else "accepted"
        if executed_plan.tasks and all(task.status in ("failed", "skipped") for task in executed_plan.tasks):
            status = "failed"
        result = CapabilityResult(
            status=status,
            output=executed_plan,
            artifacts=task_artifacts,
            error="; ".join(task_failures) or planner_result.error,
            metadata={
                "tasks": len(executed_plan.tasks),
                "failed": len(task_failures),
                "partial": partial_count,
            },
        )
        update = services.record(
            state,
            node,
            result,
            attempts=attempt,
            input_payload=payload,
            force_status="failed" if status == "failed" else status,
            error=result.error if status == "failed" else None,
        )
        node_outputs = {**update.get("node_outputs", {})}
        node_outputs.update(task_outputs)
        update["node_outputs"] = node_outputs
        update["plan_artifact"] = executed_plan
        update[RUNNING_PAYLOAD] = executed_plan
        return update

    return planner_fn


def _record_planner_output(services, node: WorkflowNode, plan: PlanArtifact, attempt: int) -> None:
    services.runtime.observation.record(
        node=node.id,
        attempt=attempt,
        decision="planner:output",
        phase="planner:output",
        kind="planner_output",
        payload={
            "goal": plan.goal,
            "revision": plan.revision,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "description": task.description,
                    "capability": task.capability,
                    "status": task.status,
                    "output_ref": task.output_ref,
                    "error": task.error,
                }
                for task in plan.tasks
            ],
            "metadata": plan.metadata,
        },
        metadata={
            "revision": plan.revision,
            "task_count": len(plan.tasks),
        },
        digest_metadata_key="plan_digest",
    )


def _coerce_plan_artifact(value: Any, *, errors: Optional[list[str]] = None) -> Optional[PlanArtifact]:
    if value is None:
        return None
    if isinstance(value, PlanArtifact):
        return value
    if isinstance(value, dict):
        try:
            return PlanArtifact.model_validate(value)
        except ValidationError as exc:
            if errors is not None:
                errors.append(str(exc))
            return None
    return None

def _merge_replanned_plan(prior: PlanArtifact, proposed: PlanArtifact) -> PlanArtifact:
    immutable = {
        task.task_id: task
        for task in prior.tasks
        if task.status in ("done", "partial", "failed", "skipped")
    }
    merged: list[PlanTask] = []
    seen: set[str] = set()
    for old_task in prior.tasks:
        if old_task.task_id in immutable:
            merged.append(old_task)
            seen.add(old_task.task_id)
    for task in proposed.tasks:
        if task.task_id in immutable:
            continue
        status = task.status if task.status in ("pending", "in_progress") else "pending"
        merged.append(task.model_copy(update={"status": status, "error": None, "output_ref": None}))
        seen.add(task.task_id)
    metadata = {**proposed.metadata}
    if "task_outputs" in prior.metadata:
        metadata["task_outputs"] = _merge_task_outputs(
            prior.metadata.get("task_outputs"),
            proposed.metadata.get("task_outputs"),
        )
    return proposed.model_copy(update={"tasks": merged, "revision": prior.revision + 1, "metadata": metadata})


def _merge_task_outputs(*sources: Any) -> Dict[str, Any]:
    """Merge round output maps in order; later evidence replaces the same key once."""

    merged: Dict[str, Any] = {}
    for source in sources:
        if isinstance(source, dict):
            merged.update(source)
    return merged

def _validate_plan(
    services,
    node: WorkflowNode,
    plan: PlanArtifact,
    context: CapabilityContext,
    plan_depth: int = 1,
) -> list[str]:
    errors: list[str] = []
    if len(plan.tasks) > node.max_tasks:
        errors.append(f"plan has {len(plan.tasks)} tasks, exceeds max_tasks={node.max_tasks}")
    seen: set[str] = set()
    allowed = set(context.plan.safety.allowed_side_effects) if context.plan is not None else set()
    for index, task in enumerate(plan.tasks):
        label = task.task_id or f"#{index + 1}"
        if not task.task_id:
            errors.append(f"task {index + 1} has empty task_id")
        elif task.task_id in seen:
            errors.append(f"task '{task.task_id}' has duplicate task_id")
        seen.add(task.task_id)
        try:
            spec, handler = services.runtime.registry.get(task.capability)
        except KeyError:
            errors.append(f"task '{label}' capability '{task.capability}' is not registered")
            continue
        if task.capability == (node.capability or node.id):
            errors.append(f"task '{label}' cannot call its planner capability '{task.capability}'")
        elif spec.is_planner:
            # Typed marker only (R3 clean v0.9 — metadata/handler reads removed).
            # Bounded recursion: planner-bound tasks are legal ONLY while depth budget remains
            # (pre-set max_plan_depth=1 keeps plans flat unless a node opts in explicitly).
            if plan_depth >= node.max_plan_depth:
                errors.append(
                    f"task '{label}' is a planner capability at plan depth {plan_depth} — "
                    f"exceeds max_plan_depth={node.max_plan_depth} (recursion is opt-in and bounded)"
                )
        denied = sorted(effect for effect in spec.side_effects if effect not in allowed)
        if denied:
            errors.append(
                f"task '{label}' capability '{task.capability}' side effects denied: "
                f"{', '.join(denied)}"
            )
    return errors

async def _execute_plan(
    services,
    *,
    node: WorkflowNode,
    plan: PlanArtifact,
    context: CapabilityContext,
    state: Dict[str, Any],
    plan_depth: int = 1,
    task_budget: Optional[Dict[str, int]] = None,
) -> tuple[PlanArtifact, Dict[str, Any], list[WorkflowArtifact], list[str]]:
    task_outputs: Dict[str, Any] = {}
    artifacts: list[WorkflowArtifact] = []
    failures: list[str] = []
    tasks = list(plan.tasks)
    # Cumulative cap across ALL plan levels: depth can multiply work, the budget cannot.
    if task_budget is None:
        task_budget = {"remaining": node.max_total_planned_tasks}
    if node.execution == "fanout":
        # B3: fanout shares the SAME cumulative task budget as sequential execution —
        # parallelism must not bypass max_total_planned_tasks.
        return await _execute_plan_fanout(services, node, plan, context, state, task_budget)

    current_plan = plan
    for index, task in enumerate(tasks):
        if task.status in ("done", "partial", "failed", "skipped"):
            continue
        if task_budget["remaining"] <= 0:
            exhausted = task.model_copy(update={
                "status": "failed",
                "error": f"cumulative plan-task budget exhausted (max_total_planned_tasks={node.max_total_planned_tasks})",
            })
            tasks[index] = exhausted
            current_plan = current_plan.model_copy(update={"tasks": list(tasks)})
            failures.append(f"{exhausted.task_id}: {exhausted.error}")
            _trace_plan_task(services, node, exhausted, "plan:task_failed", error=exhausted.error)
            continue
        task_budget["remaining"] -= 1
        started = task.model_copy(update={"status": "in_progress", "error": None})
        tasks[index] = started
        current_plan = current_plan.model_copy(update={"tasks": list(tasks)})
        _trace_plan_task(services, node, started, "plan:task_started")
        result = await _invoke_plan_task(services, node, started, context, state, current_plan)
        result = await _maybe_execute_child_plan(services, 
            node, started, result, context, state, plan_depth, task_budget,
            task_outputs, artifacts, failures,
        )
        finished, output_key = _finish_plan_task(node, started, result)
        tasks[index] = finished
        current_plan = current_plan.model_copy(update={"tasks": list(tasks)})
        if output_key and result.output is not None:
            task_outputs[output_key] = result.output
        artifacts.extend(result.artifacts)
        _record_finished_task(services, node, finished, failures)
    return current_plan, task_outputs, artifacts, failures

async def _maybe_execute_child_plan(
    services,
    node: WorkflowNode,
    task: PlanTask,
    result: CapabilityResult,
    context: CapabilityContext,
    state: Dict[str, Any],
    plan_depth: int,
    task_budget: Dict[str, int],
    task_outputs: Dict[str, Any],
    artifacts: list[WorkflowArtifact],
    failures: list[str],
) -> CapabilityResult:
    """Bounded recursive planning: when a planned task IS a planner, its emitted child plan
    is validated at depth+1 (same allow-lists, loud abort) and executed inline, sharing the
    run budget and the cumulative task budget. Child results ride under the parent task."""

    if result.status not in ("accepted", "partial"):
        return result
    child_plan = _coerce_plan_artifact(result.output)
    if child_plan is None:
        return result
    try:
        spec, handler = services.runtime.registry.get(task.capability)
    except KeyError:
        return result
    if not spec.is_planner:
        return result

    child_depth = plan_depth + 1
    validation_errors = _validate_plan(services, node, child_plan, context, plan_depth=child_depth)
    if validation_errors:
        error = (
            f"child plan (depth {child_depth}) validation failed: " + "; ".join(validation_errors)
        )
        services.runtime.trace_sink.record(
            WorkflowTraceEvent(
                node=node.id,
                decision="plan:subplan_validation_failed",
                error=error,
                metadata={"parent_task": task.task_id, "depth": child_depth, "errors": validation_errors},
            )
        )
        return CapabilityResult(status="failed", output=child_plan, error=error)
    services.runtime.trace_sink.record(
        WorkflowTraceEvent(
            node=node.id,
            decision="plan:subplan_started",
            metadata={"parent_task": task.task_id, "depth": child_depth, "tasks": len(child_plan.tasks)},
        )
    )
    executed_child, child_outputs, child_artifacts, child_failures = await _execute_plan(services, 
        node=node,
        plan=child_plan,
        context=context,
        state=state,
        plan_depth=child_depth,
        task_budget=task_budget,
    )
    for key, value in child_outputs.items():
        task_outputs[f"{task.task_id}.{key}"] = value
    artifacts.extend(child_artifacts)
    child_partials = [t.task_id for t in executed_child.tasks if t.status == "partial"]
    if child_failures:
        failures.extend(f"{task.task_id}>{item}" for item in child_failures)
    services.runtime.trace_sink.record(
        WorkflowTraceEvent(
            node=node.id,
            decision=(
                "plan:subplan_done"
                if not (child_failures or child_partials)
                else "plan:subplan_partial"
            ),
            metadata={
                "parent_task": task.task_id,
                "depth": child_depth,
                "failed": len(child_failures),
            },
        )
    )
    status = "accepted" if not (child_failures or child_partials) else "partial"
    return CapabilityResult(status=status, output=executed_child, artifacts=child_artifacts)

async def _execute_plan_fanout(
    services,
    node: WorkflowNode,
    plan: PlanArtifact,
    context: CapabilityContext,
    state: Dict[str, Any],
    task_budget: Optional[Dict[str, int]] = None,
) -> tuple[PlanArtifact, Dict[str, Any], list[WorkflowArtifact], list[str]]:
    if task_budget is None:
        task_budget = {"remaining": node.max_total_planned_tasks}
    pending = [
        index for index, task in enumerate(plan.tasks)
        if task.status not in ("done", "partial", "failed", "skipped")
    ]
    tasks = list(plan.tasks)
    budget_failures: list[str] = []
    # Cumulative budget gate (B3): tasks beyond the remaining budget are failed loudly and
    # never invoked — identical semantics to the sequential path.
    pending_indexes: list[int] = []
    for index in pending:
        if task_budget["remaining"] <= 0:
            exhausted = tasks[index].model_copy(update={
                "status": "failed",
                "error": (
                    f"cumulative plan-task budget exhausted "
                    f"(max_total_planned_tasks={node.max_total_planned_tasks})"
                ),
            })
            tasks[index] = exhausted
            budget_failures.append(f"{exhausted.task_id}: {exhausted.error}")
            _trace_plan_task(services, node, exhausted, "plan:task_failed", error=exhausted.error)
            continue
        task_budget["remaining"] -= 1
        pending_indexes.append(index)
    for index in pending_indexes:
        tasks[index] = tasks[index].model_copy(update={"status": "in_progress", "error": None})
        _trace_plan_task(services, node, tasks[index], "plan:task_started")
    started_plan = plan.model_copy(update={"tasks": list(tasks)})
    # R13: distinguish None (unset -> profile bound) from a configured value; the default
    # comes from the RuntimeLimits model, never a falsy `or 4` rewrite.
    if node.max_parallel is not None:
        limit = node.max_parallel
    elif context.limits is not None:
        limit = context.limits.max_parallel_children
    else:
        limit = _DEFAULT_LIMITS.max_parallel_children
    semaphore = asyncio.Semaphore(max(1, limit))

    async def invoke(index: int) -> tuple[int, CapabilityResult]:
        async with semaphore:
            result = await _invoke_plan_task(services, node, tasks[index], context, state, started_plan)
            return index, result

    results = await asyncio.gather(*(invoke(index) for index in pending_indexes))
    task_outputs: Dict[str, Any] = {}
    artifacts: list[WorkflowArtifact] = []
    failures: list[str] = list(budget_failures)
    for index, result in results:
        finished, output_key = _finish_plan_task(node, tasks[index], result)
        tasks[index] = finished
        if output_key and result.output is not None:
            task_outputs[output_key] = result.output
        artifacts.extend(result.artifacts)
        _record_finished_task(services, node, finished, failures)
    return plan.model_copy(update={"tasks": tasks}), task_outputs, artifacts, failures

async def _invoke_plan_task(
    services,
    node: WorkflowNode,
    task: PlanTask,
    context: CapabilityContext,
    state: Dict[str, Any],
    plan: PlanArtifact,
) -> CapabilityResult:
    denial = _planned_task_denial(services, task, state, context)
    if denial:
        return CapabilityResult(
            status="rejected",
            error=f"denied by policy: {', '.join(denial)}",
            metadata={"denied": denial},
        )
    task_context = services.context_for_node(node, context, state, plan=plan)
    if task.execution is not None:
        # v0.10 #3: the planned task's execution request (timeout/reserve/source) feeds the
        # engine window resolver as request= for THIS invocation.
        task_context = task_context.model_copy(update={"execution_request": task.execution})
    return await services.runtime.invoke(task.capability, task.payload, task_context, attempt=1)

def _planned_task_denial(
    services,
    task: PlanTask,
    state: Dict[str, Any],
    context: CapabilityContext,
) -> list[str]:
    plan = context.plan
    if plan is None:
        return []
    try:
        spec, _handler = services.runtime.registry.get(task.capability)
    except KeyError:
        return []
    if spec.metered and plan.limits and plan.limits.max_estimated_usd is not None:
        usage = state.get("usage_summary")
        spent = (getattr(usage, "estimated_usd", None) or 0.0) if usage is not None else 0.0
        if spent >= plan.limits.max_estimated_usd:
            return ["budget_exhausted"]
    return []

def _record_finished_task(services, node: WorkflowNode, finished: PlanTask, failures: list[str]) -> None:
    """One owner of finished-task trace/failure semantics for BOTH execution paths —
    sequential and fanout must never classify the same outcome differently."""

    if finished.status == "failed":
        failures.append(f"{finished.task_id}: {finished.error or 'failed'}")
        _trace_plan_task(services, node, finished, "plan:task_failed", error=finished.error)
    elif finished.status == "partial":
        _trace_plan_task(services, node, finished, "plan:task_partial", error=finished.error)
    else:
        _trace_plan_task(services, node, finished, "plan:task_done")


def _finish_plan_task(
    node: WorkflowNode,
    task: PlanTask,
    result: CapabilityResult,
) -> tuple[PlanTask, Optional[str]]:
    """EXACT terminal mapping (v0.10 truth contract): accepted -> done, partial -> partial
    (error and evidence PRESERVED), anything else -> failed. Partial is never promoted to
    done — a plan of unfinished work must read unfinished on every surface."""

    # v0.10: task evidence is PERSISTED into plan state (snapshots/bundles) — result_metadata
    # must be byte-safe (no raw bytes/media/transport refs) exactly like every checkpointed
    # value, or the run fails loudly rather than smuggling bytes into durable state.
    metadata = dict(result.metadata or {})
    assert_byte_safe(metadata, mode="persist", path=f"plan_task[{task.task_id}].result_metadata")
    evidence = {
        "artifact_refs": [artifact.artifact_id for artifact in result.artifacts],
        "result_metadata": metadata,
    }
    if result.status == "accepted":
        output_key = f"{node.id}.{task.task_id}" if result.output is not None else None
        return (
            task.model_copy(
                update={"status": "done", "error": None, "output_ref": output_key, **evidence}
            ),
            output_key,
        )
    if result.status == "partial":
        output_key = f"{node.id}.{task.task_id}" if result.output is not None else None
        return (
            task.model_copy(
                update={
                    "status": "partial",
                    "error": result.error,
                    "output_ref": output_key or task.output_ref,
                    **evidence,
                }
            ),
            output_key,
        )
    return (
        task.model_copy(
            update={
                "status": "failed",
                "error": result.error or f"task capability returned {result.status}",
                "output_ref": None,
                **evidence,
            }
        ),
        None,
    )

def _trace_plan_task(
    services,
    node: WorkflowNode,
    task: PlanTask,
    decision: str,
    *,
    error: Optional[str] = None,
) -> None:
    services.runtime.trace_sink.record(
        WorkflowTraceEvent(
            node=node.id,
            decision=decision,
            error=error,
            metadata={
                "task_id": task.task_id,
                "capability": task.capability,
                "status": task.status,
                "output_ref": task.output_ref,
            },
        )
    )
