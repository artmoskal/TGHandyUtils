"""One-call façade (G-S): the trivial case in 2–3 honest lines, on the REAL engine.

``run_single_llm`` / ``run_single_step`` build a one-node ``WorkflowDefinition`` via
``WorkflowBuilder``, register it, and ``await engine.run(...)`` — so a "simple" call still
gets budget gates, usage metering, trace, structured parse/repair, and loud failures.
There is NO shadow execution path here: this module composes the public API and nothing
else. Intended for one-shot calls and prototyping; for volume, pass ``engine=`` to reuse
one configured engine (an ephemeral engine per call re-pays setup on every call).

Reuse contract: on a shared engine each façade ``name`` owns ONE registered capability —
a stable dispatcher whose per-call worker is swapped under a lock (the registry's
duplicate-registration guard stays intact; same-name calls serialize instead of racing).
Reusing a name with a different ``output_model``/``kind``/``side_effects`` fails loudly —
pass a distinct ``name=`` for a different contract.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, Mapping, Optional
from weakref import WeakKeyDictionary

from ai_workflow_engine.builder import WorkflowEngine
from ai_workflow_engine.executor import WorkflowRunResult
from ai_workflow_engine.workflow import WorkflowBuilder

__all__ = ["run_single_llm", "run_single_step"]


@dataclass
class _SingleCallSlot:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    worker: Any = None  # StructuredLLMNode (llm) or normalized handler (step)
    contract: tuple = ()  # what must stay identical across reuses of this name


_SLOTS: "WeakKeyDictionary[WorkflowEngine, Dict[str, _SingleCallSlot]]" = WeakKeyDictionary()


def _slot_for(engine: WorkflowEngine, name: str) -> _SingleCallSlot:
    slots = _SLOTS.setdefault(engine, {})
    if name not in slots:
        slots[name] = _SingleCallSlot()
    return slots[name]


def _require_same_contract(slot: _SingleCallSlot, contract: tuple, *, name: str) -> None:
    if slot.contract and slot.contract != contract:
        raise ValueError(
            f"single-call name {name!r} is already bound to a different contract on this "
            "engine — pass a distinct name= instead of changing output_model/kind/side_effects"
        )
    slot.contract = contract


async def run_single_llm(
    llm: Any,
    prompt_template: str,
    output_model: type,
    values: Optional[Mapping[str, Any]] = None,
    *,
    engine: Optional[WorkflowEngine] = None,
    name: str = "single_llm",
    temperature: float = 0.1,
    max_repair_rounds: int = 1,
    pre_parse: Optional[Callable[[str], str]] = None,
    validator: Optional[Any] = None,
    return_result: bool = False,
) -> Any:
    """One structured LLM call with full engine semantics; returns the parsed model.

    ``llm`` is any ``LLMCallable`` (or LangChain-shaped client) accepted by
    ``StructuredLLMNode`` — it is REQUIRED: there is no default model here, ever.
    Raises ``RuntimeError`` when the run does not complete (parse/repair exhausted,
    budget denied, …) — never a silent fallback.

    ``return_result=True`` returns the full ``WorkflowRunResult`` instead (``.output``,
    ``.usage``, ``.trace``, ``.observation_bundle_path``, …) — the usage/trace/budget
    proof stays reachable even on an ephemeral engine; nothing is hidden.
    """

    if llm is None:
        raise ValueError("run_single_llm requires an llm client — there is no default model")
    # Call-time import (F1.1): a deterministic run_single_step consumer never pays for the
    # LangChain-backed structured worker.
    from ai_workflow_engine.engine.llm_node import StructuredLLMNode

    resolved_values: Dict[str, Any] = dict(values or {})
    node = StructuredLLMNode(
        name=name,
        config=SimpleNamespace(),
        output_model=output_model,
        prompt_template=prompt_template,
        input_variables=tuple(resolved_values),
        llm=llm,
        temperature=temperature,
        max_repair_rounds=max_repair_rounds,
        pre_parse=pre_parse,
        validator=validator,
    )

    resolved_engine = engine or WorkflowEngine()
    slot = _slot_for(resolved_engine, name)
    _require_same_contract(slot, ("llm", output_model), name=name)
    if name not in resolved_engine.registry.names():

        async def _dispatch(_context: Any, payload: Any) -> Any:
            return await slot.worker.run(dict(payload or {}))

        resolved_engine.register_capability(name, _dispatch, kind="llm", output_model=output_model)
        resolved_engine.register_workflow(WorkflowBuilder(f"{name}_flow").step(name).build())

    async with slot.lock:
        slot.worker = node
        result = await resolved_engine.run(f"{name}_flow", resolved_values)
    return _unwrap(result, what=name, return_result=return_result)


async def run_single_step(
    handler: Callable[..., Any],
    payload: Any,
    *,
    engine: Optional[WorkflowEngine] = None,
    name: str = "single_step",
    kind: str = "deterministic",
    side_effects: Optional[list[str]] = None,
    return_result: bool = False,
) -> Any:
    """Run one capability call through the engine; returns the capability output.

    ``handler`` may be the standard two-arg ``(context, payload)`` capability callable
    or a plain one-arg callable (sync or async) — it is normalized, never bypassed.
    ``return_result=True`` returns the full ``WorkflowRunResult`` (usage/trace reachable
    even on an ephemeral engine).
    """

    normalized = _normalize_handler(handler)
    resolved_engine = engine or WorkflowEngine()
    slot = _slot_for(resolved_engine, name)
    _require_same_contract(slot, ("step", kind, tuple(side_effects or ())), name=name)
    if name not in resolved_engine.registry.names():

        async def _dispatch(context: Any, step_payload: Any) -> Any:
            return await slot.worker(context, step_payload)

        resolved_engine.register_capability(name, _dispatch, kind=kind, side_effects=side_effects)
        resolved_engine.register_workflow(WorkflowBuilder(f"{name}_flow").step(name).build())

    async with slot.lock:
        slot.worker = normalized
        result = await resolved_engine.run(f"{name}_flow", payload)
    return _unwrap(result, what=name, return_result=return_result)


def _normalize_handler(handler: Callable[..., Any]) -> Callable[[Any, Any], Any]:
    if _positional_param_count(handler) >= 2:

        async def two_arg(context: Any, payload: Any) -> Any:
            outcome = handler(context, payload)
            return await outcome if inspect.isawaitable(outcome) else outcome

        return two_arg

    async def one_arg(_context: Any, payload: Any) -> Any:
        outcome = handler(payload)
        return await outcome if inspect.isawaitable(outcome) else outcome

    return one_arg


def _positional_param_count(handler: Callable[..., Any]) -> int:
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return 2  # builtins/odd callables: assume the standard capability shape
    count = 0
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            count += 1
        elif parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            return 2
    return count


def _unwrap(result: WorkflowRunResult, *, what: str, return_result: bool = False) -> Any:
    if result.status != "completed":
        raise RuntimeError(
            f"single-call {what!r} did not complete: status={result.status!r}; "
            f"error={result.error or 'unknown'}"
        )
    return result if return_result else result.output
