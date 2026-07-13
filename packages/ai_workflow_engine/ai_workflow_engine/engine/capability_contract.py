"""Pure capability contract helpers (v0.11 iteration 1, Phase I1.2).

The input/output schema contract, async-handler detection, and side-effect admission comparison
are pure rules over a ``CapabilitySpec`` and its inputs — no runtime state, no sinks, no clocks,
no lifecycle control. Centralizing them here gives that contract one owner and keeps
``CapabilityRuntime.invoke`` focused on coordinating stages. Dependency-light: models + stdlib
only — no registry, sink, executor, window, provider, product, or process imports.

Behavior is IDENTICAL to engine-v0.10.1 (proven by the invocation preservation oracle): the error
text and ``CapabilityResult`` semantics are unchanged.
"""

from __future__ import annotations

import inspect
from typing import Any

from pydantic import ValidationError

from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    CapabilitySpec,
)

__all__ = [
    "validate_payload",
    "normalize_result",
    "denied_side_effects",
    "handler_is_async",
]


def validate_payload(spec: CapabilitySpec, payload: Any) -> Any:
    """Validate/coerce the payload against the spec's input model (identity when none)."""

    if not spec.input_model:
        return payload
    if isinstance(payload, spec.input_model):
        return payload
    try:
        return spec.input_model.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"{spec.name} input validation failed: {exc}") from exc


def normalize_result(spec: CapabilitySpec, result: Any) -> CapabilityResult:
    """Wrap a raw handler return into an accepted ``CapabilityResult`` (validating against the
    spec's output model), or pass a ``CapabilityResult`` through unchanged."""

    if isinstance(result, CapabilityResult):
        output = result
    else:
        output_value = result
        if spec.output_model:
            if not isinstance(result, spec.output_model):
                try:
                    output_value = spec.output_model.model_validate(result)
                except ValidationError as exc:
                    raise ValueError(f"{spec.name} output validation failed: {exc}") from exc
        output = CapabilityResult(status="accepted", output=output_value)
    return output


def denied_side_effects(spec: CapabilitySpec, context: CapabilityContext) -> list[str]:
    """The declared side effects NOT permitted by the run's safety policy (empty == all allowed)."""

    if not spec.side_effects or context.plan is None:
        return []
    allowed = set(context.plan.safety.allowed_side_effects)
    return [side_effect for side_effect in spec.side_effects if side_effect not in allowed]


def handler_is_async(handler: Any) -> bool:
    """True when the handler runs asynchronously — including a plain object whose ``__call__`` is
    a coroutine function (a bare ``iscoroutinefunction(handler)`` misses capability objects)."""

    if inspect.iscoroutinefunction(handler):
        return True
    call = getattr(handler, "__call__", None)
    return bool(call is not None and inspect.iscoroutinefunction(call))
