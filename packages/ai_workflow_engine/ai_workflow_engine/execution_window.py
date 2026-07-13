"""Engine-owned execution windows (v0.10) — the ONE source of time-limit truth.

A planned task, a direct node, a capability spec, the remaining run budget, and any parent
invocation window all propose a duration. This module intersects them into one serializable
:class:`ExecutionWindowDecision` (minimum hard limit wins) and records WHY — which bounds
limited it and where each clamp came from — using closed typed vocabularies, never prose.

The resolver is PURE: it receives explicit remaining-time numbers (an injected clock lives at
the call site, not here), so it is table-testable without sleeps. Enforcement, session
deadlines, and interruptibility live in the runtime; this leaf owns only the arithmetic and
the contract shapes. Dependency-light on purpose (typing + pydantic only).
"""

from __future__ import annotations

import math
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "ExecutionWindowBoundSource",
    "ExecutionWindowClamp",
    "TimeoutEnforcement",
    "TaskExecutionRequest",
    "ExecutionWindowInputs",
    "ExecutionWindowDecision",
    "resolve_execution_window",
]

# Where a duration came from, and which clamp reduced the effective hard limit. Closed
# vocabularies so observation/tests assert on machine data, never free text.
ExecutionWindowBoundSource = Literal[
    "task_request", "capability_limit", "run_limit", "parent_window"
]
ExecutionWindowClamp = Literal["capability", "run_remaining", "parent_soft_remaining"]

# Interruptibility honesty (v0.10): the engine must never claim a hard stop it cannot perform.
#   process     — capability owns a killable child process; can be hard-stopped (kill/reap).
#   cooperative — async handler receives cancellation at the boundary; must ack within grace.
#   none        — inline synchronous work; boundary checks only, a finite hard window is REJECTED.
TimeoutEnforcement = Literal["process", "cooperative", "none"]


def _finite_non_negative(value: Optional[float], label: str) -> Optional[float]:
    if value is None:
        return None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite non-negative number, got {value!r}")
    return value


class TaskExecutionRequest(BaseModel):
    """Optional per-task execution request carried on a ``PlanTask`` / node.

    ``source`` is a bounded, non-blank OBSERVATION label (e.g. ``"llm_planner"``); it is
    recorded but never participates in resolution or control.
    """

    model_config = ConfigDict(extra="forbid")

    timeout_s: Optional[float] = None
    completion_reserve_s: float = 0.0
    source: str = "planner_task"

    @field_validator("timeout_s")
    @classmethod
    def _timeout_positive_finite(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"timeout_s must be a finite positive number, got {value!r}")
        return value

    @field_validator("completion_reserve_s")
    @classmethod
    def _reserve_finite_non_negative(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"completion_reserve_s must be finite and >= 0, got {value!r}")
        return value

    @field_validator("source")
    @classmethod
    def _source_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("execution-request source must be a non-blank observation label")
        return value


class ExecutionWindowInputs(BaseModel):
    """Everything the resolver intersects. Remaining-run/parent values are supplied by the
    caller from its injected clock — the resolver never reads a clock itself."""

    model_config = ConfigDict(extra="forbid")

    request: Optional[TaskExecutionRequest] = None
    capability_timeout_s: Optional[float] = None
    run_remaining_s: Optional[float] = None
    parent_soft_remaining_s: Optional[float] = None

    @field_validator("capability_timeout_s", "run_remaining_s", "parent_soft_remaining_s")
    @classmethod
    def _bounds_finite_non_negative(cls, value: Optional[float]) -> Optional[float]:
        return _finite_non_negative(value, "execution-window bound")


class ExecutionWindowDecision(BaseModel):
    """The one resolved, serializable time-limit decision for an invocation.

    ``hard_timeout_s`` is the outer cutoff; ``soft_timeout_s`` = hard minus the completion
    reserve (the work deadline that leaves room for salvage/cleanup). ``None`` hard means no
    bound applied — an unbounded invocation. ``limiting_sources``/``clamps`` name exactly why
    the hard limit is what it is; ``enforcement`` is how it will be enforced.
    """

    model_config = ConfigDict(extra="forbid")

    requested_timeout_s: Optional[float] = None
    capability_timeout_s: Optional[float] = None
    run_remaining_s: Optional[float] = None
    parent_remaining_s: Optional[float] = None
    soft_timeout_s: Optional[float] = None
    hard_timeout_s: Optional[float] = None
    completion_reserve_s: float = 0.0
    request_source: Optional[str] = None
    limiting_sources: List[ExecutionWindowBoundSource] = Field(default_factory=list)
    clamps: List[ExecutionWindowClamp] = Field(default_factory=list)
    enforcement: TimeoutEnforcement = "none"

    @property
    def is_bounded(self) -> bool:
        return self.hard_timeout_s is not None

    @model_validator(mode="after")
    def _seal_invariants(self) -> "ExecutionWindowDecision":
        # A decision is internally consistent or it is a bug — seal it so no caller can
        # construct a lying window (bounded label without a hard limit, soft above hard,
        # reserve mismatch, or a limiting source that names no real bound).
        if self.hard_timeout_s is None:
            if self.soft_timeout_s is not None or self.limiting_sources or self.clamps:
                raise ValueError("unbounded window must carry no hard/soft limit, sources, or clamps")
            return self
        if not math.isfinite(self.hard_timeout_s) or self.hard_timeout_s <= 0:
            raise ValueError(f"hard_timeout_s must be finite and > 0, got {self.hard_timeout_s!r}")
        if self.soft_timeout_s is None:
            raise ValueError("a bounded window must have a soft_timeout_s")
        if not math.isclose(self.soft_timeout_s, self.hard_timeout_s - self.completion_reserve_s):
            raise ValueError("soft_timeout_s must equal hard_timeout_s minus completion_reserve_s")
        if not self.limiting_sources:
            raise ValueError("a bounded window must name at least one limiting source")
        return self


def resolve_execution_window(
    inputs: ExecutionWindowInputs,
    *,
    enforcement: TimeoutEnforcement = "none",
) -> ExecutionWindowDecision:
    """Intersect all proposed durations into one decision. Minimum hard limit wins; every
    lower bound and clamp is named. No bound anywhere → an unbounded window (``None``).

    ``completion_reserve_s`` (from the task request) must be strictly below the resolved hard
    limit — a reserve that meets or exceeds the window leaves no work time and is an impossible
    window (raised here, before any handler runs).
    """

    request = inputs.request
    requested = request.timeout_s if request else None
    reserve = request.completion_reserve_s if request else 0.0

    # candidate hard bounds keyed by their typed source
    candidates: list[tuple[float, ExecutionWindowBoundSource]] = []
    if requested is not None:
        candidates.append((requested, "task_request"))
    if inputs.capability_timeout_s is not None:
        candidates.append((inputs.capability_timeout_s, "capability_limit"))
    if inputs.run_remaining_s is not None:
        candidates.append((inputs.run_remaining_s, "run_limit"))
    if inputs.parent_soft_remaining_s is not None:
        candidates.append((inputs.parent_soft_remaining_s, "parent_window"))

    common = dict(
        requested_timeout_s=requested,
        capability_timeout_s=inputs.capability_timeout_s,
        run_remaining_s=inputs.run_remaining_s,
        parent_remaining_s=inputs.parent_soft_remaining_s,
        completion_reserve_s=reserve,
        request_source=request.source if request else None,
        enforcement=enforcement,
    )

    if not candidates:
        # unbounded: no source proposed a hard limit
        if reserve:
            raise ValueError(
                "completion_reserve_s was set but no execution window bounds it — a reserve "
                "is only meaningful inside a finite window"
            )
        return ExecutionWindowDecision(**common)

    hard = min(value for value, _ in candidates)
    if reserve >= hard:
        raise ValueError(
            f"impossible execution window: completion_reserve_s={reserve} leaves no work time "
            f"under hard_timeout_s={hard} — reserve must be strictly less than the hard window"
        )

    # every source at the winning minimum is a limiting source (ties are all named)
    limiting_sources = [source for value, source in candidates if value == hard]
    # A clamp names each non-request bound that ACTUALLY reduced the effective ceiling as
    # bounds are applied in precedence order (task -> capability -> run -> parent). A bound
    # looser than the ceiling already reached clamps nothing and is not named — the running
    # ceiling prevents a false "capability clamped" when run was tighter.
    clamp_of: dict[ExecutionWindowBoundSource, ExecutionWindowClamp] = {
        "capability_limit": "capability",
        "run_limit": "run_remaining",
        "parent_window": "parent_soft_remaining",
    }
    by_source = {source: value for value, source in candidates}
    ceiling = math.inf
    clamps: list[ExecutionWindowClamp] = []
    for source in ("task_request", "capability_limit", "run_limit", "parent_window"):
        if source not in by_source:
            continue
        value = by_source[source]
        if value < ceiling:
            if source in clamp_of:
                clamps.append(clamp_of[source])
            ceiling = value

    # Build the final decision ONCE so the model_validator seals the complete object.
    return ExecutionWindowDecision(
        **common,
        hard_timeout_s=hard,
        soft_timeout_s=hard - reserve,
        limiting_sources=limiting_sources,
        clamps=clamps,
    )
