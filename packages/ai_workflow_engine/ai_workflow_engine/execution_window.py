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

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

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

_BOUND_ORDER: tuple[ExecutionWindowBoundSource, ...] = (
    "task_request",
    "capability_limit",
    "run_limit",
    "parent_window",
)
_CLAMP_BY_SOURCE: dict[ExecutionWindowBoundSource, ExecutionWindowClamp] = {
    "capability_limit": "capability",
    "run_limit": "run_remaining",
    "parent_window": "parent_soft_remaining",
}


def _finite_non_negative(value: Optional[float], label: str) -> Optional[float]:
    if value is None:
        return None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite non-negative number, got {value!r}")
    return value


def _validated_source_label(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must be a non-blank observation label")
    if not normalized.isprintable():
        raise ValueError(f"{label} must be printable (no control characters)")
    return normalized


def _derive_resolution(
    *,
    requested_timeout_s: Optional[float],
    capability_timeout_s: Optional[float],
    run_remaining_s: Optional[float],
    parent_remaining_s: Optional[float],
) -> tuple[
    Optional[float],
    list[ExecutionWindowBoundSource],
    list[ExecutionWindowClamp],
]:
    """Derive canonical hard/source/clamp truth from the four proposed bounds."""

    by_source: dict[ExecutionWindowBoundSource, float] = {
        source: value
        for source, value in (
            ("task_request", requested_timeout_s),
            ("capability_limit", capability_timeout_s),
            ("run_limit", run_remaining_s),
            ("parent_window", parent_remaining_s),
        )
        if value is not None
    }
    if not by_source:
        return None, [], []

    hard = min(by_source.values())
    limiting_sources = [
        source for source in _BOUND_ORDER if by_source.get(source) == hard
    ]
    ceiling = math.inf
    clamps: list[ExecutionWindowClamp] = []
    for source in _BOUND_ORDER:
        value = by_source.get(source)
        if value is None or value >= ceiling:
            continue
        clamp = _CLAMP_BY_SOURCE.get(source)
        if clamp is not None:
            clamps.append(clamp)
        ceiling = value
    return hard, limiting_sources, clamps


class TaskExecutionRequest(BaseModel):
    """Optional per-task execution request carried on a ``PlanTask`` / node.

    ``source`` is a bounded, non-blank OBSERVATION label (e.g. ``"llm_planner"``); it is
    recorded but never participates in resolution or control.
    """

    model_config = ConfigDict(extra="forbid")

    timeout_s: Optional[float] = None
    completion_reserve_s: float = 0.0
    # bounded STRICT observation label: max 128 chars, non-blank, no control chars — it is
    # recorded into persisted decisions, never a control value.
    source: StrictStr = Field(default="planner_task", max_length=128)

    @field_validator("timeout_s")
    @classmethod
    def _timeout_positive_finite(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"timeout_s must be a finite positive number, got {value!r}")
        return _finite_non_negative(value, "execution-window duration")

    @field_validator("completion_reserve_s")
    @classmethod
    def _reserve_finite_non_negative(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"completion_reserve_s must be finite and >= 0, got {value!r}")
        return value

    @field_validator("source")
    @classmethod
    def _source_non_blank(cls, value: str) -> str:
        return _validated_source_label(value, label="execution-request source")


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
    request_source: Optional[StrictStr] = Field(default=None, max_length=128)
    limiting_sources: List[ExecutionWindowBoundSource] = Field(default_factory=list)
    clamps: List[ExecutionWindowClamp] = Field(default_factory=list)
    enforcement: TimeoutEnforcement = "none"

    @property
    def is_bounded(self) -> bool:
        return self.hard_timeout_s is not None

    @field_validator(
        "requested_timeout_s",
        "capability_timeout_s",
        "run_remaining_s",
        "parent_remaining_s",
        "soft_timeout_s",
        "hard_timeout_s",
        "completion_reserve_s",
    )
    @classmethod
    def _durations_finite_non_negative(cls, value: Optional[float]) -> Optional[float]:
        # This object is PERSISTED and ENFORCED — every duration must be a finite,
        # non-negative number even when the model is constructed directly.
        return _finite_non_negative(value, "execution-window duration")

    @field_validator("request_source")
    @classmethod
    def _request_source_safe(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _validated_source_label(value, label="execution-window request_source")

    @model_validator(mode="after")
    def _seal_invariants(self) -> "ExecutionWindowDecision":
        # A decision is internally consistent or it is a bug — seal it so no caller can
        # construct a lying window (soft above hard, negative reserve, reserve eating the
        # whole window, bounded label without a hard limit, or a source-less bound).
        expected_hard, expected_sources, expected_clamps = _derive_resolution(
            requested_timeout_s=self.requested_timeout_s,
            capability_timeout_s=self.capability_timeout_s,
            run_remaining_s=self.run_remaining_s,
            parent_remaining_s=self.parent_remaining_s,
        )
        if expected_hard is None:
            if (
                self.hard_timeout_s is not None
                or self.soft_timeout_s is not None
                or self.completion_reserve_s != 0
                or self.limiting_sources
                or self.clamps
            ):
                raise ValueError(
                    "unbounded window must carry no hard/soft limit, reserve, sources, or clamps"
                )
            return self
        if self.hard_timeout_s is None:
            raise ValueError("a proposed execution bound requires a hard_timeout_s")
        if self.hard_timeout_s != expected_hard:
            raise ValueError(
                f"hard_timeout_s={self.hard_timeout_s!r} must equal the minimum proposed "
                f"bound {expected_hard!r}"
            )
        if self.hard_timeout_s <= 0:
            raise ValueError(f"hard_timeout_s must be > 0, got {self.hard_timeout_s!r}")
        if self.soft_timeout_s is None:
            raise ValueError("a bounded window must have a soft_timeout_s")
        if not (0 <= self.completion_reserve_s < self.hard_timeout_s):
            raise ValueError(
                f"completion_reserve_s={self.completion_reserve_s!r} must satisfy "
                f"0 <= reserve < hard_timeout_s={self.hard_timeout_s!r}"
            )
        if not (0 <= self.soft_timeout_s <= self.hard_timeout_s):
            raise ValueError(
                f"soft_timeout_s={self.soft_timeout_s!r} must satisfy 0 <= soft <= "
                f"hard_timeout_s={self.hard_timeout_s!r}"
            )
        if not math.isclose(self.soft_timeout_s, self.hard_timeout_s - self.completion_reserve_s):
            raise ValueError("soft_timeout_s must equal hard_timeout_s minus completion_reserve_s")
        if self.limiting_sources != expected_sources:
            raise ValueError(
                f"limiting_sources must exactly match the winning bounds: {expected_sources!r}"
            )
        if self.clamps != expected_clamps:
            raise ValueError(
                f"clamps must exactly match the applied bound reductions: {expected_clamps!r}"
            )
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

    common = dict(
        requested_timeout_s=requested,
        capability_timeout_s=inputs.capability_timeout_s,
        run_remaining_s=inputs.run_remaining_s,
        parent_remaining_s=inputs.parent_soft_remaining_s,
        completion_reserve_s=reserve,
        request_source=request.source if request else None,
        enforcement=enforcement,
    )

    hard, limiting_sources, clamps = _derive_resolution(
        requested_timeout_s=requested,
        capability_timeout_s=inputs.capability_timeout_s,
        run_remaining_s=inputs.run_remaining_s,
        parent_remaining_s=inputs.parent_soft_remaining_s,
    )

    if hard is None:
        # unbounded: no source proposed a hard limit
        if reserve:
            raise ValueError(
                "completion_reserve_s was set but no execution window bounds it — a reserve "
                "is only meaningful inside a finite window"
            )
        return ExecutionWindowDecision(**common)

    if reserve >= hard:
        raise ValueError(
            f"impossible execution window: completion_reserve_s={reserve} leaves no work time "
            f"under hard_timeout_s={hard} — reserve must be strictly less than the hard window"
        )

    # Build the final decision ONCE so the model_validator seals the complete object.
    return ExecutionWindowDecision(
        **common,
        hard_timeout_s=hard,
        soft_timeout_s=hard - reserve,
        limiting_sources=limiting_sources,
        clamps=clamps,
    )
