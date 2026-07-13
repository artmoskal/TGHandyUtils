"""Planner-node artifacts for executable workflows.

These models are product-neutral. A planner capability may be an LLM, deterministic function, or
external adapter, but once it emits a ``PlanArtifact`` the engine owns validation, bounded
execution, status mutation, trace emission, and optional context injection for downstream nodes.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from ai_workflow_engine.execution_window import TaskExecutionRequest

from pydantic import BaseModel, Field


PlanTaskStatus = Literal["pending", "in_progress", "done", "partial", "failed", "skipped"]


class PlanTask(BaseModel):
    """One executable task inside a planner-produced plan."""

    task_id: str
    description: str
    capability: str
    payload: Any = None
    status: PlanTaskStatus = "pending"
    error: Optional[str] = None
    output_ref: Optional[str] = None
    # v0.10 truthful terminal evidence: stable artifact ids and byte-safe result metadata
    # ride the task for partial/done outcomes; raw bytes and whole outputs never do —
    # ``output_ref`` keeps pointing into the plan's task-output map.
    artifact_refs: list[str] = Field(default_factory=list)
    result_metadata: dict[str, Any] = Field(default_factory=dict)
    # v0.10: optional per-task execution request (timeout/reserve/source). Absent -> the task
    # inherits only capability + run + parent bounds; simple plans set nothing.
    execution: Optional["TaskExecutionRequest"] = None


class PlanArtifact(BaseModel):
    """Checkpoint-safe execution plan and current task statuses."""

    plan_version: int = 1
    goal: str
    tasks: list[PlanTask]
    revision: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


def render_plan(plan: PlanArtifact) -> str:
    """Render a compact, stable plan summary for opt-in LLM context injection."""

    lines = [f"Goal: {plan.goal}", f"Revision: {plan.revision}"]
    for task in plan.tasks:
        line = f"- [{task.status}] {task.task_id}: {task.description} -> {task.capability}"
        if task.output_ref:
            line += f" (output: {task.output_ref})"
        if task.error:
            line += f" (error: {task.error})"
        lines.append(line)
    return "\n".join(lines)
