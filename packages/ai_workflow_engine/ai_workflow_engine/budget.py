"""Workflow budgets: models, per-run usage context/scope, and enforcement gates."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional

from ai_workflow_engine.models import (
    RuntimeLimits,
    WorkflowRunContext,
    WorkflowUsageEvent,
    WorkflowUsageSummary,
)

logger = logging.getLogger(__name__)


class WorkflowBudgetExceeded(Exception):
    """Raised before/after a provider call would exceed a workflow budget."""

    def __init__(self, message: str, *, decision: Optional[str] = None):
        super().__init__(message)
        self.decision = decision


@dataclass(frozen=True)
class WorkflowBudget:
    max_text_calls: Optional[int] = None
    max_image_calls: Optional[int] = None
    max_estimated_usd: Optional[float] = None
    max_worker_calls: Optional[int] = None
    max_input_tokens_per_call: Optional[int] = None
    max_output_tokens_per_call: Optional[int] = None
    max_images_per_call: Optional[int] = None
    max_estimated_usd_per_call: Optional[float] = None


@dataclass
class WorkflowUsageContext:
    run_context: WorkflowRunContext
    summary: WorkflowUsageSummary
    budget: WorkflowBudget
    # Typed as Any: the UsageSink protocol lives in usage_events (avoids a module cycle).
    usage_sink: Optional[Any] = None
    # Typed as Any to keep the budget leaf independent from pricing implementation.
    notional_pricing_policy: Optional[Any] = None

    @property
    def worker_call_count(self) -> int:
        """Delegates to the serialized summary so resume keeps the cap cumulative."""

        return self.summary.worker_call_count

    @worker_call_count.setter
    def worker_call_count(self, value: int) -> None:
        self.summary.worker_call_count = value


_ACTIVE_USAGE: ContextVar[Optional[WorkflowUsageContext]] = ContextVar("workflow_usage", default=None)


@contextmanager
def workflow_usage_scope(context: WorkflowUsageContext):
    token = _ACTIVE_USAGE.set(context)
    try:
        yield context
    finally:
        _ACTIVE_USAGE.reset(token)


def current_usage_context() -> Optional[WorkflowUsageContext]:
    return _ACTIVE_USAGE.get()


def budget_from_limits(limits: Optional[RuntimeLimits]) -> WorkflowBudget:
    """Budgets come from typed ``profile.limits`` ONLY (v0.9 clean contract).

    The legacy host-config duck-read (``WORKFLOW_MAX_*`` attributes) and the
    ``WORKFLOW_USAGE_TRACKING_ENABLED`` silent kill-switch are gone: ceilings enforce
    whenever configured. ``0`` is an honest hard-zero cap; "no cap" = leave the field None.
    """

    if limits is None:
        return WorkflowBudget()
    if not isinstance(limits, RuntimeLimits):
        raise TypeError(
            f"budget_from_limits expects RuntimeLimits or None, got {type(limits).__name__} — "
            "host/app config objects are no longer a budget source; put ceilings in "
            "workflow.limits (profile or definition)"
        )
    return WorkflowBudget(
        max_text_calls=limits.max_text_calls,
        max_image_calls=limits.max_image_calls,
        max_estimated_usd=limits.max_estimated_usd,
        max_worker_calls=limits.max_worker_calls,
        max_input_tokens_per_call=limits.max_input_tokens_per_call,
        max_output_tokens_per_call=limits.max_output_tokens_per_call,
        max_images_per_call=limits.max_images_per_call,
        max_estimated_usd_per_call=limits.max_estimated_usd_per_call,
    )


def check_budget_before_call(operation: str, node: str) -> None:
    context = current_usage_context()
    if not context:
        return
    if operation == "chat" and context.budget.max_text_calls is not None:
        if context.summary.text_call_count >= context.budget.max_text_calls:
            raise WorkflowBudgetExceeded(
                f"Workflow max_text_calls budget exceeded before node {node}: "
                f"{context.summary.text_call_count}/{context.budget.max_text_calls}"
            )
    if operation == "image" and context.budget.max_image_calls is not None:
        if context.summary.image_call_count >= context.budget.max_image_calls:
            raise WorkflowBudgetExceeded(
                f"Workflow max_image_calls budget exceeded before node {node}: "
                f"{context.summary.image_call_count}/{context.budget.max_image_calls}"
            )
    if operation in {"chat", "agent", "external"} and context.budget.max_worker_calls is not None:
        if context.worker_call_count >= context.budget.max_worker_calls:
            raise WorkflowBudgetExceeded(
                f"Workflow max_worker_calls budget exceeded before {operation} node {node}: "
                f"{context.worker_call_count}/{context.budget.max_worker_calls}"
            )
        context.worker_call_count += 1


def check_input_tokens_per_call(input_tokens: int, node: str) -> None:
    context = current_usage_context()
    if not context or context.budget.max_input_tokens_per_call is None:
        return
    if input_tokens > context.budget.max_input_tokens_per_call:
        raise WorkflowBudgetExceeded(
            f"Workflow max_input_tokens_per_call budget exceeded before node {node}: "
            f"{input_tokens}/{context.budget.max_input_tokens_per_call}"
        )


def check_images_per_call(image_count: int, node: str) -> None:
    context = current_usage_context()
    if not context or context.budget.max_images_per_call is None:
        return
    if image_count > context.budget.max_images_per_call:
        raise WorkflowBudgetExceeded(
            f"Workflow max_images_per_call budget exceeded before node {node}: "
            f"{image_count}/{context.budget.max_images_per_call}"
        )


def _enforce_usd_budget(context: WorkflowUsageContext) -> None:
    max_usd = context.budget.max_estimated_usd
    total = context.summary.metered_usd
    if max_usd is not None and total is not None and total > max_usd:
        raise WorkflowBudgetExceeded(f"Workflow estimated cost exceeded: ${total:.6f} > ${max_usd:.6f}")


def _enforce_per_call_budget(event: WorkflowUsageEvent, context: WorkflowUsageContext) -> None:
    max_output = context.budget.max_output_tokens_per_call
    if max_output is not None and event.output_tokens > max_output:
        # Spec (engine-completion §5): the output is already produced and paid for — record the
        # truncation loudly (usage-event metadata + structured log), do NOT retroactively fail
        # the call. Runaway protection across turns comes from max_worker_calls / USD caps.
        event.metadata["truncated_by_budget"] = True
        event.metadata["output_tokens_over_cap"] = f"{event.output_tokens}/{max_output}"
        logger.warning(
            "workflow_output_tokens_over_cap node=%s tokens=%s cap=%s",
            event.node,
            event.output_tokens,
            max_output,
        )
    max_usd = context.budget.max_estimated_usd_per_call
    if (
        max_usd is not None
        and event.cost_class == "metered"
        and event.estimated_usd is not None
        and event.estimated_usd > max_usd
    ):
        raise WorkflowBudgetExceeded(
            f"Workflow max_estimated_usd_per_call budget exceeded after node {event.node}: "
            f"${event.estimated_usd:.6f} > ${max_usd:.6f}"
        )
