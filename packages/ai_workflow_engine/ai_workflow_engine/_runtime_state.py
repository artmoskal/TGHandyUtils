"""Internal workflow runtime-state keys shared by executor and node handlers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

# Reserved by the executor; kept out of product state space.
RUNNING_PAYLOAD = "payload"
CONTEXT = "engine_context"

_ACTIVE_WORKFLOW_CONTEXT: ContextVar[Optional[Any]] = ContextVar(
    "workflow_run_context",
    default=None,
)
_ACTIVE_OBSERVATION_CAPTURE: ContextVar[Optional[Any]] = ContextVar(
    "workflow_observation_capture",
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
