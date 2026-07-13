"""Internal workflow runtime-state keys shared by executor and node handlers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

# Reserved by the executor; kept out of product state space.
RUNNING_PAYLOAD = "payload"
CONTEXT = "engine_context"

# A5 guard surface: every state-channel key the engine owns. The contract-guard test asserts
# this set matches WorkflowState EXACTLY (adding a state key without updating this set fails
# the build) and that node handlers write only these keys into their state updates.
RESERVED_STATE_KEYS = frozenset(
    {
        RUNNING_PAYLOAD,
        CONTEXT,
        "node_outputs",
        "node_inputs",
        "node_results",
        "node_status",
        "branch_decisions",
        "routes",
        "transition_counts",
        "eval_counters",
        "attempts",
        "artifacts",
        "status",
        "error",
        "fallback_reason",
        "workflow_context",
        "workflow_goal",
        "usage_summary",
        "plan_artifact",
        "resume_suspended_node",
        "resume_event",
        "machine_replay_done",
        "pending_retrace_provenance",
    }
)

_ACTIVE_WORKFLOW_CONTEXT: ContextVar[Optional[Any]] = ContextVar(
    "workflow_run_context",
    default=None,
)
_ACTIVE_OBSERVATION_CAPTURE: ContextVar[Optional[Any]] = ContextVar(
    "workflow_observation_capture",
    default=None,
)

# v0.10: the retrace provenance for the CURRENTLY-executing target node invocation, set by
# the generic node wrapper and read at the generic capability-invocation boundary — so any
# retrace target (step/planner/fanout/...) receives typed provenance exactly once, cleared
# on every return path.
_ACTIVE_RETRACE_PROVENANCE: ContextVar[Optional[Any]] = ContextVar(
    "workflow_active_retrace_provenance",
    default=None,
)


@contextmanager
def workflow_run_context_scope(context: Any) -> Iterator[Any]:
    token = _ACTIVE_WORKFLOW_CONTEXT.set(context)
    try:
        yield context
    finally:
        _ACTIVE_WORKFLOW_CONTEXT.reset(token)


def current_workflow_run_context() -> Optional[Any]:
    return _ACTIVE_WORKFLOW_CONTEXT.get()


_ACTIVE_RUN_SESSION: ContextVar[Optional[Any]] = ContextVar(
    "workflow_run_session",
    default=None,
)


@contextmanager
def run_session_scope(session: Any) -> Iterator[Any]:
    token = _ACTIVE_RUN_SESSION.set(session)
    try:
        yield session
    finally:
        _ACTIVE_RUN_SESSION.reset(token)


def current_run_session() -> Optional[Any]:
    return _ACTIVE_RUN_SESSION.get()


@contextmanager
def observation_capture_scope(capture: Any) -> Iterator[Any]:
    token = _ACTIVE_OBSERVATION_CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _ACTIVE_OBSERVATION_CAPTURE.reset(token)


def current_observation_capture() -> Optional[Any]:
    return _ACTIVE_OBSERVATION_CAPTURE.get()
