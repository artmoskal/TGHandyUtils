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
    "RunExecutionRequest",
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
#   process     — capability owns a killable subprocess tree; can be hard-stopped (kill/reap).
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

_COOPERATIVE_CLEANUP_RESERVE_S = 0.05
_PROCESS_CLEANUP_RESERVE_S = 1.0
_PROCESS_SETTLE_RESERVE_S = 0.25


def _engine_cleanup_reserve_s(hard_s: float, enforcement: TimeoutEnforcement) -> float:
    """Represent the runtime's own cancellation/reap room inside the hard deadline."""

    if enforcement == "cooperative":
        return min(_COOPERATIVE_CLEANUP_RESERVE_S, hard_s / 4.0)
    if enforcement == "process":
        return min(_PROCESS_CLEANUP_RESERVE_S, hard_s / 4.0)
    return 0.0


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


class RunExecutionRequest(BaseModel):
    """Invocation-local wall-clock limit for one complete engine run.

    This request narrows, but can never widen, the configured workflow/profile timeout.
    It is separate from :class:`TaskExecutionRequest`, which bounds one capability call.
    """

    model_config = ConfigDict(extra="forbid")

    timeout_s: float
    source: StrictStr = Field(default="run_caller", max_length=128)

    @field_validator("timeout_s", mode="before")
    @classmethod
    def _timeout_positive_finite(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError(f"timeout_s must be a finite positive number, got {value!r}")
        try:
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(
                f"timeout_s must be a finite positive number, got {value!r}"
            ) from None
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError(f"timeout_s must be a finite positive number, got {value!r}")
        return value

    @field_validator("source")
    @classmethod
    def _source_non_blank(cls, value: str) -> str:
        return _validated_source_label(value, label="run-execution-request source")


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

    @property
    def is_exhausted(self) -> bool:
        """Whether an enclosing run/parent consumed the entire window before invocation."""

        return self.hard_timeout_s == 0

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
        if self.hard_timeout_s == 0:
            # Zero is not a runnable timeout request. It is a terminal resolution produced
            # only when an enclosing run/parent has no time left, and lets the runtime refuse
            # the invocation with the same typed provenance as an in-flight timeout.
            if not ({"run_limit", "parent_window"} & set(expected_sources)):
                raise ValueError(
                    "a zero execution window must come from exhausted run/parent remaining time"
                )
            if self.soft_timeout_s != 0:
                raise ValueError("an exhausted execution window must have soft_timeout_s=0")
            if self.limiting_sources != expected_sources:
                raise ValueError(
                    f"limiting_sources must exactly match the winning bounds: {expected_sources!r}"
                )
            if self.clamps != expected_clamps:
                raise ValueError(
                    f"clamps must exactly match the applied bound reductions: {expected_clamps!r}"
                )
            return self
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

    ``completion_reserve_s`` is the larger of the task's requested reserve and the engine's
    small enforcement reserve (cooperative cancellation or process reap). It must be strictly
    below the resolved hard limit; an impossible window is rejected before any handler runs.
    """

    request = inputs.request
    requested = request.timeout_s if request else None
    requested_reserve = request.completion_reserve_s if request else 0.0

    hard, limiting_sources, clamps = _derive_resolution(
        requested_timeout_s=requested,
        capability_timeout_s=inputs.capability_timeout_s,
        run_remaining_s=inputs.run_remaining_s,
        parent_remaining_s=inputs.parent_soft_remaining_s,
    )

    if hard is None:
        # unbounded: no source proposed a hard limit
        if requested_reserve:
            raise ValueError(
                "completion_reserve_s was set but no execution window bounds it — a reserve "
                "is only meaningful inside a finite window"
            )
        return ExecutionWindowDecision(
            requested_timeout_s=requested,
            capability_timeout_s=inputs.capability_timeout_s,
            run_remaining_s=inputs.run_remaining_s,
            parent_remaining_s=inputs.parent_soft_remaining_s,
            completion_reserve_s=0.0,
            request_source=request.source if request else None,
            enforcement=enforcement,
        )

    if hard == 0:
        return ExecutionWindowDecision(
            requested_timeout_s=requested,
            capability_timeout_s=inputs.capability_timeout_s,
            run_remaining_s=inputs.run_remaining_s,
            parent_remaining_s=inputs.parent_soft_remaining_s,
            completion_reserve_s=0.0,
            request_source=request.source if request else None,
            enforcement=enforcement,
            hard_timeout_s=0,
            soft_timeout_s=0,
            limiting_sources=limiting_sources,
            clamps=clamps,
        )

    reserve = max(requested_reserve, _engine_cleanup_reserve_s(hard, enforcement))
    if reserve >= hard:
        raise ValueError(
            f"impossible execution window: completion_reserve_s={reserve} leaves no work time "
            f"under hard_timeout_s={hard} — reserve must be strictly less than the hard window"
        )

    # Build the final decision ONCE so the model_validator seals the complete object.
    return ExecutionWindowDecision(
        requested_timeout_s=requested,
        capability_timeout_s=inputs.capability_timeout_s,
        run_remaining_s=inputs.run_remaining_s,
        parent_remaining_s=inputs.parent_soft_remaining_s,
        completion_reserve_s=reserve,
        request_source=request.source if request else None,
        enforcement=enforcement,
        hard_timeout_s=hard,
        soft_timeout_s=hard - reserve,
        limiting_sources=limiting_sources,
        clamps=clamps,
    )


# --------------------------------------------------------------------------------------
# v0.10 Phase 5R: the ONE engine-owned invocation-window surface for anything that runs
# work OUTSIDE the Python interpreter (subprocesses, console CLIs). The capability runtime
# publishes the ACTIVE invocation's soft and hard deadlines here; every process-backed door
# (CliAgentCapability, ConsoleLLMClient, ConsoleChatModel, ExternalProcessCapability)
# resolves its subprocess bound through `resolve_invocation_bound` — an explicit product
# timeout may NARROW the engine bound but can never widen it, and cleanup time is
# represented in the bound (never a hidden margin racing the authoritative hard deadline).
# --------------------------------------------------------------------------------------

from contextvars import ContextVar as _ContextVar, Token as _Token
from dataclasses import dataclass as _dataclass
import time as _time

@_dataclass(frozen=True)
class InvocationWindowDeadlines:
    """Absolute monotonic deadlines for the active capability invocation."""

    soft_monotonic: float
    hard_monotonic: float


@_dataclass(frozen=True)
class InvocationWindowRemaining:
    """Remaining soft/hard durations sampled from one clock reading."""

    soft_s: float
    hard_s: float


# ContextVars keep concurrent runs/tasks isolated and propagate through asyncio.to_thread.
_ACTIVE_INVOCATION_WINDOW: "_ContextVar[Optional[InvocationWindowDeadlines]]" = _ContextVar(
    "ai_workflow_engine_active_invocation_window", default=None
)

# Cleanup headroom carved from WORK time when a process-backed bound has no completion
# reserve (soft == hard): terminate->kill->reap must finish BEFORE the outer hard deadline,
# so the child never outlives the engine's claim that it stopped. Bounded to a quarter of
# the hard window so tiny windows (short probes, tests) stay usable. This is a REPRESENTED
# reservation (recorded on the bound as ``headroom_s``), never a hidden margin.
PROCESS_MIN_CLEANUP_HEADROOM_S = _PROCESS_CLEANUP_RESERVE_S


def _process_cleanup_headroom_s(engine_hard_s: float) -> float:
    return min(PROCESS_MIN_CLEANUP_HEADROOM_S, engine_hard_s / 4.0)


def publish_invocation_window(
    *,
    soft_deadline_monotonic: float,
    hard_deadline_monotonic: float,
) -> "_Token[Optional[InvocationWindowDeadlines]]":
    """Publish the complete active invocation window.

    The runtime owns the publish/reset pairing. Process transports need both deadlines: soft
    limits work, while hard limits cleanup. Publishing only soft time makes it impossible to
    prove that terminate/kill/reap fits before the authoritative cutoff.
    """

    if not math.isfinite(soft_deadline_monotonic) or not math.isfinite(hard_deadline_monotonic):
        raise ValueError("invocation deadlines must be finite monotonic timestamps")
    if soft_deadline_monotonic > hard_deadline_monotonic:
        raise ValueError("invocation soft deadline must not exceed the hard deadline")
    return _ACTIVE_INVOCATION_WINDOW.set(
        InvocationWindowDeadlines(
            soft_monotonic=soft_deadline_monotonic,
            hard_monotonic=hard_deadline_monotonic,
        )
    )


def reset_invocation_window(token: "_Token[Optional[InvocationWindowDeadlines]]") -> None:
    _ACTIVE_INVOCATION_WINDOW.reset(token)


def invocation_window_remaining_s(
    clock=_time.monotonic,
) -> Optional[InvocationWindowRemaining]:
    """Sample remaining soft and hard time from the same monotonic clock reading."""

    deadlines = _ACTIVE_INVOCATION_WINDOW.get()
    if deadlines is None:
        return None
    now = clock()
    return InvocationWindowRemaining(
        soft_s=max(0.0, deadlines.soft_monotonic - now),
        hard_s=max(0.0, deadlines.hard_monotonic - now),
    )


@_dataclass(frozen=True)
class InvocationBound:
    """The resolved subprocess bound for one process-backed invocation.

    ``timeout_s`` bounds the child's WORK; ``kill_grace_s`` (terminate→kill→reap) plus the
    work bound and ``settle_reserve_s`` always fit BEFORE the engine hard deadline when one exists.
    ``headroom_s``
    is the cleanup time carved out of work when the window had no completion reserve —
    recorded so the reservation is visible, never a hidden margin. ``error`` is non-None
    only when no bound could be resolved but one is required, or the window is too small
    to do any work and still reap the child."""

    timeout_s: Optional[float] = None
    kill_grace_s: float = 10.0
    source: Optional[Literal["engine_window", "explicit"]] = None
    engine_soft_s: Optional[float] = None
    engine_hard_s: Optional[float] = None
    headroom_s: float = 0.0
    settle_reserve_s: float = 0.0
    error: Optional[str] = None

    def metadata(self) -> dict[str, object]:
        """Serializable enforcement truth suitable for trace/detail projection."""

        return {
            "work_timeout_s": self.timeout_s,
            "kill_grace_s": self.kill_grace_s,
            "source": self.source,
            "engine_soft_s": self.engine_soft_s,
            "engine_hard_s": self.engine_hard_s,
            "cleanup_headroom_s": self.headroom_s,
            "settle_reserve_s": self.settle_reserve_s,
        }


def resolve_invocation_bound(
    *,
    engine_soft_s: Optional[float],
    engine_hard_s: Optional[float] = None,
    explicit_timeout_s: Optional[float] = None,
    default_kill_grace_s: float = 10.0,
    require_bound: bool = False,
    owner: str = "invocation",
) -> InvocationBound:
    """Resolve one subprocess bound from the engine window and an explicit product timeout.

    Narrowing-only: the effective work bound is ``min`` of what exists — an explicit timeout
    can tighten an engine bound, never enlarge it. When ``engine_hard_s`` is known, cleanup
    (terminate grace plus reap/result settlement) must FIT before it: the reserve (hard − work)
    hosts it; with no reserve,
    ``PROCESS_MIN_CLEANUP_HEADROOM_S`` is carved from work time and recorded; a window too
    small to work and reap is rejected (``error``) rather than enforced dishonestly.
    """

    for label, value in (
        ("engine_soft_s", engine_soft_s),
        ("engine_hard_s", engine_hard_s),
        ("explicit_timeout_s", explicit_timeout_s),
        ("default_kill_grace_s", default_kill_grace_s),
    ):
        if value is not None and (not math.isfinite(value) or value < 0):
            return InvocationBound(
                error=f"{owner}: {label} must be finite and >= 0, got {value!r}"
            )
    if (
        engine_soft_s is not None
        and engine_hard_s is not None
        and engine_soft_s > engine_hard_s
    ):
        return InvocationBound(
            error=(
                f"{owner}: engine_soft_s={engine_soft_s!r} must not exceed "
                f"engine_hard_s={engine_hard_s!r}"
            ),
            engine_soft_s=engine_soft_s,
            engine_hard_s=engine_hard_s,
        )

    candidates = [b for b in (engine_soft_s, explicit_timeout_s) if b is not None]
    if not candidates:
        if require_bound:
            return InvocationBound(
                error=(
                    f"{owner} has no execution bound: the engine supplied no window and no "
                    "explicit timeout was set — declare an explicit positive timeout (a "
                    "missing window must never become a hidden default)"
                )
            )
        return InvocationBound(timeout_s=None, kill_grace_s=default_kill_grace_s)

    work = min(candidates)
    if work <= 0:
        return InvocationBound(
            error=f"{owner}: process work timeout must be positive, got {work!r}",
            engine_soft_s=engine_soft_s,
            engine_hard_s=engine_hard_s,
        )
    source: Literal["engine_window", "explicit"] = (
        "engine_window" if engine_soft_s is not None and work == engine_soft_s else "explicit"
    )
    headroom = 0.0

    if engine_hard_s is None:
        # No authoritative hard deadline in sight (standalone call, or ambient-soft-only —
        # where the enclosing capability's own reserve hosts the cleanup).
        return InvocationBound(
            timeout_s=work,
            kill_grace_s=default_kill_grace_s,
            source=source,
            engine_soft_s=engine_soft_s,
        )

    cleanup_budget = engine_hard_s - work
    if cleanup_budget <= 0:
        # soft == hard (no reserve): carve represented headroom from work time so the child
        # is reaped BEFORE the hard deadline instead of racing it.
        headroom = _process_cleanup_headroom_s(engine_hard_s)
        work = work - headroom
        cleanup_budget = engine_hard_s - work
        if work <= 0:
            return InvocationBound(
                error=(
                    f"{owner}: impossible process window — hard bound {engine_hard_s:g}s is too "
                    f"small to do any work and still terminate/reap the child within "
                    f"{headroom:g}s cleanup headroom"
                ),
                engine_soft_s=engine_soft_s,
                engine_hard_s=engine_hard_s,
            )

    settle_reserve = min(_PROCESS_SETTLE_RESERVE_S, cleanup_budget / 4.0)
    kill_grace = max(
        0.0,
        min(default_kill_grace_s, cleanup_budget - settle_reserve),
    )
    # `settle_reserve` is the deliberately protected minimum for kill/reap, stream settlement,
    # artifact salvage, and result construction. Any additional slack from a narrower explicit
    # timeout is not mislabeled as reserved settlement time.
    return InvocationBound(
        timeout_s=work,
        kill_grace_s=kill_grace,
        source=source,
        engine_soft_s=engine_soft_s,
        engine_hard_s=engine_hard_s,
        headroom_s=headroom,
        settle_reserve_s=settle_reserve,
    )


__all__ += [
    "InvocationBound",
    "InvocationWindowDeadlines",
    "InvocationWindowRemaining",
    "PROCESS_MIN_CLEANUP_HEADROOM_S",
    "invocation_window_remaining_s",
    "publish_invocation_window",
    "reset_invocation_window",
    "resolve_invocation_bound",
]
