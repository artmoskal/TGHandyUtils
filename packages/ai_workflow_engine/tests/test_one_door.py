"""Phase I2.1 gate (hardened in I2.R): the one-door rule is LOCKED, not just currently true.

``CapabilityRuntime.invoke`` (and its fanout companion ``gather_capabilities``, owned by the same
module) is the only way engine code may EXECUTE a registered capability handler. Other modules may
look a capability up (``registry.get``) to inspect its spec, markers, or handler attributes —
authoring validation, planner allow-lists, viz prompt extraction, the model-profile conflict check
all do — but calling the looked-up handler would bypass validation, admission, budget, supervision,
and observation.

Scope honesty (codex I2 review): this is an ACCIDENTAL-BYPASS lock over the ordinary Python call
shapes — direct chained calls, unpacked or subscript-bound handlers, whole-pair calls, and simple
function-local registry aliases — enforced by AST analysis and proven by permanent positive
probes. It is not a whole-program security proof; a determined obfuscation could evade static
analysis, and that boundary is documented rather than overclaimed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_PKG = Path(__file__).parents[1] / "ai_workflow_engine"
_ONE_DOOR_OWNER = _PKG / "engine" / "capabilities.py"


def _mentions_registry(expr: ast.AST) -> bool:
    """True when a Name/Attribute chain textually involves a registry object."""

    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and "registry" in node.id.lower():
            return True
        if isinstance(node, ast.Attribute) and "registry" in node.attr.lower():
            return True
    return False


def _registry_aliases(func: ast.AST) -> set[str]:
    """Function-local names assigned FROM a registry expression (``r = services.runtime.registry``).
    One level of simple aliasing — enough for ordinary code; obfuscation is out of scope."""

    aliases: set[str] = set()
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, (ast.Name, ast.Attribute))
            and _mentions_registry(node.value)
        ):
            aliases.add(node.targets[0].id)
    return aliases


def _is_registry_get(call: ast.AST, aliases: set[str]) -> bool:
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
        return False
    if call.func.attr != "get":
        return False
    target = call.func.value
    if _mentions_registry(target):
        return True
    return isinstance(target, ast.Name) and target.id in aliases


def _handler_names(func: ast.AST, aliases: set[str]) -> set[str]:
    """Names that hold an EXECUTABLE handler (or the whole (spec, handler) pair) obtained from a
    registry lookup, across the three ordinary binding shapes:
    ``spec, handler = registry.get(...)``, ``pair = registry.get(...)``, and
    ``handler = registry.get(...)[1]``."""

    bound: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        # handler = registry.get(...)[<idx>]
        if isinstance(value, ast.Subscript) and _is_registry_get(value.value, aliases):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
            continue
        if not _is_registry_get(value, aliases):
            continue
        for target in node.targets:
            if isinstance(target, ast.Tuple) and len(target.elts) == 2:
                second = target.elts[1]
                if isinstance(second, ast.Name):
                    bound.add(second.id)
            elif isinstance(target, ast.Name):
                bound.add(target.id)  # whole pair kept — pair[1](...) is also flagged
    return bound


def find_handler_call_violations(source: str, label: str) -> list[str]:
    """AST scan of one module's source for handler executions outside the door."""

    tree = ast.parse(source)
    violations: list[str] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        aliases = _registry_aliases(func)
        handlers = _handler_names(func, aliases)
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            # registry.get(...)[1](...)  — direct chained call, no binding at all
            if isinstance(f, ast.Subscript) and _is_registry_get(f.value, aliases):
                violations.append(
                    f"{label}:{node.lineno} directly calls a registry.get(...)[...] handler"
                )
                continue
            if not handlers:
                continue
            if isinstance(f, ast.Name) and f.id in handlers:
                violations.append(f"{label}:{node.lineno} calls looked-up handler '{f.id}'")
            elif (
                isinstance(f, ast.Subscript)
                and isinstance(f.value, ast.Name)
                and f.value.id in handlers
            ):
                violations.append(
                    f"{label}:{node.lineno} calls looked-up handler via '{f.value.id}[...]'"
                )
    return violations


def _scan_file(path: Path) -> list[str]:
    return find_handler_call_violations(path.read_text(encoding="utf-8"), path.name)


def test_no_engine_module_calls_a_registry_handler_directly():
    violations: list[str] = []
    for path in sorted(_PKG.rglob("*.py")):
        if path == _ONE_DOOR_OWNER:
            continue  # the door itself executes handlers under full supervision
        violations.extend(_scan_file(path))
    assert not violations, (
        "second invocation door detected — handlers looked up from the registry must only be "
        "executed by CapabilityRuntime.invoke/gather_capabilities:\n" + "\n".join(violations)
    )


# --------------------------------------------------------------------------------------
# Permanent analyzer probes (codex I2 review): every ordinary bypass family MUST be detected,
# and inspection-only lookup MUST stay legal. These run the analyzer on crafted snippets, so the
# guard's own strength is regression-locked without planting violations in production files.
# --------------------------------------------------------------------------------------

_BYPASS_UNPACKED = """
def f(registry, name, ctx, payload):
    spec, handler = registry.get(name)
    return handler(ctx, payload)
"""

_BYPASS_DIRECT_CHAINED = """
def f(registry, name, ctx, payload):
    return registry.get(name)[1](ctx, payload)
"""

_BYPASS_SUBSCRIPT_BOUND = """
def f(registry, name, ctx, payload):
    handler = registry.get(name)[1]
    return handler(ctx, payload)
"""

_BYPASS_WHOLE_PAIR = """
def f(registry, name, ctx, payload):
    pair = registry.get(name)
    return pair[1](ctx, payload)
"""

_BYPASS_VIA_ALIAS = """
def f(services, name, ctx, payload):
    r = services.runtime.registry
    spec, handler = r.get(name)
    return handler(ctx, payload)
"""

_LEGAL_INSPECTION_ONLY = """
def f(registry, name, sink):
    spec, handler = registry.get(name)
    if getattr(handler, "accepts_model_profile", None) is False:
        sink.append(spec.kind)
    frozen = (spec, handler)          # storing is legal
    describe(handler)                 # passing as an argument is legal
    return frozen
"""


@pytest.mark.parametrize(
    "snippet, family",
    [
        (_BYPASS_UNPACKED, "unpacked handler"),
        (_BYPASS_DIRECT_CHAINED, "direct chained registry.get(...)[1](...)"),
        (_BYPASS_SUBSCRIPT_BOUND, "handler bound from registry.get(...)[1]"),
        (_BYPASS_WHOLE_PAIR, "whole-pair subscript call"),
        (_BYPASS_VIA_ALIAS, "registry alias then unpacked handler"),
    ],
)
def test_analyzer_detects_every_ordinary_bypass_family(snippet, family):
    violations = find_handler_call_violations(snippet, "probe")
    assert violations, f"analyzer missed the '{family}' bypass family"


def test_analyzer_keeps_inspection_only_lookup_legal():
    violations = find_handler_call_violations(_LEGAL_INSPECTION_ONLY, "probe")
    assert violations == [], (
        f"inspection-only lookup must stay legal, got: {violations}"
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
        uses_door = any(
            door in source
            for door in (".invoke(", "gather_capabilities(", ".gather_bound(")
        )
        executes_anything = "registry.get(" in source or uses_door
        if executes_anything:
            assert uses_door or not _scan_file(path), (
                f"{path.name} touches capabilities without routing through the facade"
            )
