"""Phase I1.2 gate: the pure capability contract helpers own validation/normalization/async-
detection/side-effect comparison, import nothing forbidden, and preserve v0.10.1 semantics."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import BaseModel

from ai_workflow_engine.engine.capability_contract import (
    denied_side_effects,
    handler_is_async,
    normalize_result,
    validate_payload,
)
from ai_workflow_engine.models import (
    CapabilityContext,
    CapabilityResult,
    CapabilitySpec,
    RuntimePlan,
    SafetyPolicy,
    WorkflowGoal,
    WorkflowRunContext,
)

pytestmark = [pytest.mark.unit]

_MODULE = Path(__file__).parents[1] / "ai_workflow_engine" / "engine" / "capability_contract.py"


def test_contract_imports_nothing_forbidden():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    forbidden = (
        "ai_workflow_engine.engine.capabilities",
        "ai_workflow_engine.engine.invocation_supervision",
        "ai_workflow_engine.engine.capability_observation",
        "ai_workflow_engine.observation_bundle", "ai_workflow_tools",
        "ai_workflow_engine.budget", "ai_workflow_engine.usage",
        "ai_workflow_engine.executor", "ai_workflow_engine.engine.external",
        "ai_workflow_engine.engine.process_io", "ai_workflow_engine.execution_window",
        "ai_workflow_engine.nodes", "ai_workflow_viewer",
        "ai_workflow_engine.observability_capture",
    )
    hits = [m for m in imported for f in forbidden if m == f or m.startswith(f + ".")]
    assert not hits, f"contract helpers reached forbidden dependencies: {hits}"


class _In(BaseModel):
    x: int


class _Out(BaseModel):
    y: int


def test_validate_payload_identity_coerce_and_loud_error():
    assert validate_payload(CapabilitySpec(name="c", kind="deterministic"), {"any": 1}) == {"any": 1}
    spec = CapabilitySpec(name="c", kind="deterministic", input_model=_In)
    assert validate_payload(spec, {"x": 5}).x == 5           # coerced
    assert validate_payload(spec, _In(x=9)).x == 9           # passthrough of a model instance
    with pytest.raises(ValueError, match="c input validation failed"):
        validate_payload(spec, {"x": "no"})


def test_normalize_result_wraps_validates_and_passes_through():
    spec = CapabilitySpec(name="c", kind="deterministic", output_model=_Out)
    wrapped = normalize_result(spec, {"y": 7})
    assert isinstance(wrapped, CapabilityResult) and wrapped.status == "accepted"
    passthrough = CapabilityResult(status="partial", output={"half": 1})
    assert normalize_result(spec, passthrough) is passthrough
    with pytest.raises(ValueError, match="c output validation failed"):
        normalize_result(spec, {"y": "no"})


def test_denied_side_effects_compares_against_policy():
    ctx = CapabilityContext(
        goal=WorkflowGoal(workflow_type="t", objective="o"),
        run_context=WorkflowRunContext(workflow_id="r", workflow_type="t"),
        plan=RuntimePlan(workflow_type="t", safety=SafetyPolicy(allowed_side_effects=["read"])),
    )
    assert denied_side_effects(CapabilitySpec(name="c", kind="tool"), ctx) == []  # none declared
    spec = CapabilitySpec(name="c", kind="tool", side_effects=["read", "network"])
    assert denied_side_effects(spec, ctx) == ["network"]  # read allowed, network denied


def test_handler_is_async_detects_functions_and_callable_objects():
    async def af(_c, _p):
        return 1

    def sf(_c, _p):
        return 1

    class AObj:
        async def __call__(self, _c, _p):
            return 1

    class SObj:
        def __call__(self, _c, _p):
            return 1

    assert handler_is_async(af) is True
    assert handler_is_async(sf) is False
    assert handler_is_async(AObj()) is True   # async __call__ — the case a bare check misses
    assert handler_is_async(SObj()) is False
