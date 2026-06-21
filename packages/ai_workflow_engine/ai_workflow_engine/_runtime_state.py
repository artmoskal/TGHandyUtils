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


@contextmanager
def workflow_run_context_scope(context: Any) -> Iterator[Any]:
    token = _ACTIVE_WORKFLOW_CONTEXT.set(context)
    try:
        yield context
    finally:
        _ACTIVE_WORKFLOW_CONTEXT.reset(token)


def current_workflow_run_context() -> Optional[Any]:
    return _ACTIVE_WORKFLOW_CONTEXT.get()
