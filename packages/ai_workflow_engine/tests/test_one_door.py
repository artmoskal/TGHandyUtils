"""Phase I2.1 gate: the one-door rule is LOCKED, not just currently true.

``CapabilityRuntime.invoke`` (and its fanout companion ``gather_capabilities``, owned by the same
module) is the only way engine code may EXECUTE a registered capability handler. Other modules may
look a capability up (``registry.get``) to inspect its spec, markers, or handler attributes —
authoring validation, planner allow-lists, viz prompt extraction, the model-profile conflict check
all do — but calling the looked-up handler would bypass validation, admission, budget, supervision,
and observation. These guards fail on the PATTERN, so a future bypass cannot land silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_PKG = Path(__file__).parents[1] / "ai_workflow_engine"
_ONE_DOOR_OWNER = _PKG / "engine" / "capabilities.py"


def _registry_handler_names(func: ast.AST) -> set[str]:
    """Names bound to the HANDLER slot of a ``spec, handler = <...>registry.get(...)`` unpack
    (or ``pair = registry.get(...)`` whole-tuple binds) within one function scope."""

    bound: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        f = call.func
        is_registry_get = (
            isinstance(f, ast.Attribute)
            and f.attr == "get"
            and (
                (isinstance(f.value, ast.Name) and "registry" in f.value.id.lower())
                or (isinstance(f.value, ast.Attribute) and "registry" in f.value.attr.lower())
            )
        )
        if not is_registry_get:
            continue
        for target in node.targets:
            if isinstance(target, ast.Tuple) and len(target.elts) == 2:
                second = target.elts[1]
                if isinstance(second, ast.Name):
                    bound.add(second.id)
            elif isinstance(target, ast.Name):
                bound.add(target.id)  # whole pair kept — calling pair[1](...) is also flagged
    return bound


def _direct_handler_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        handlers = _registry_handler_names(func)
        if not handlers:
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name) and f.id in handlers:
                violations.append(f"{path.name}:{node.lineno} calls looked-up handler '{f.id}'")
            # pair[1](...) / getattr-free subscript call on a bound whole pair
            if (
                isinstance(f, ast.Subscript)
                and isinstance(f.value, ast.Name)
                and f.value.id in handlers
            ):
                violations.append(
                    f"{path.name}:{node.lineno} calls looked-up handler via '{f.value.id}[...]'"
                )
    return violations


def test_no_engine_module_calls_a_registry_handler_directly():
    violations: list[str] = []
    for path in sorted(_PKG.rglob("*.py")):
        if path == _ONE_DOOR_OWNER:
            continue  # the door itself executes handlers under full supervision
        violations.extend(_direct_handler_calls(path))
    assert not violations, (
        "second invocation door detected — handlers looked up from the registry must only be "
        "executed by CapabilityRuntime.invoke/gather_capabilities:\n" + "\n".join(violations)
    )


def test_every_engine_caller_family_routes_through_the_facade():
    """The seven caller families (executor + step/branch/evaluate/human/planner/fanout nodes +
    agent/loop/evaluator/runner engines) execute capabilities via ``.invoke(`` or the
    capabilities-owned ``gather_capabilities`` — never a private path."""

    families = [
        _PKG / "executor.py",
        _PKG / "nodes" / "step.py",
        _PKG / "nodes" / "branch.py",
        _PKG / "nodes" / "evaluate.py",
        _PKG / "nodes" / "human.py",
        _PKG / "nodes" / "planner.py",
        _PKG / "nodes" / "fanout.py",
        _PKG / "engine" / "agent.py",
        _PKG / "engine" / "loop.py",
        _PKG / "engine" / "evaluator.py",
        _PKG / "engine" / "runner.py",
    ]
    missing = [str(p) for p in families if not p.exists()]
    assert not missing, f"caller-family inventory is stale: {missing}"

    for path in families:
        source = path.read_text(encoding="utf-8")
        uses_door = ".invoke(" in source or "gather_capabilities(" in source
        executes_anything = "registry.get(" in source or uses_door
        if executes_anything:
            assert uses_door or not _direct_handler_calls(path), (
                f"{path.name} touches capabilities without routing through the facade"
            )
