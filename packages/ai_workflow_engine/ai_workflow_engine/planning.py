"""Planner-node artifacts for executable workflows.

These models are product-neutral. A planner capability may be an LLM, deterministic function, or
external adapter, but once it emits a ``PlanArtifact`` the engine owns validation, bounded
execution, status mutation, trace emission, and optional context injection for downstream nodes.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


PlanTaskStatus = Literal["pending", "in_progress", "done", "failed", "skipped"]


class PlanTask(BaseModel):
    """One executable task inside a planner-produced plan."""

    task_id: str
    description: str
    capability: str
    payload: Any = None
    status: PlanTaskStatus = "pending"
    error: Optional[str] = None
    output_ref: Optional[str] = None


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
