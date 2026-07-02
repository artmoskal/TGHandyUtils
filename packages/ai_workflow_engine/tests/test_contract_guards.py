"""Contract guards — the load-bearing seams, each with one fail-build check.

Style follows test_examples_contain_no_product_orchestration_loops: dependency-free
source/AST inspection; failures name the offending file:line so the fix is obvious.
Widening an allow-list or a status vocabulary is a deliberate reviewed decision made in
THIS file, never a silent drift.
"""

import ast
from pathlib import Path
import tomllib
from typing import get_args

import pytest

import ai_workflow_engine
from ai_workflow_engine.models import CapabilityStatus, WorkflowResultStatus
from ai_workflow_engine.workflow import WorkflowBuilder, WorkflowValidationError

pytestmark = pytest.mark.unit

ENGINE_ROOT = Path(__file__).resolve().parents[1] / "ai_workflow_engine"
DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# G-int1: the engine is product-neutral — importing any of these repo-level product
# packages from engine code breaks the boundary.
FORBIDDEN_PRODUCT_MODULES = {
    "services",
    "handlers_modular",
    "handlers",
    "database",
    "platforms",
    "composition",
    "core",
    "config",
    "models",  # repo-root product models package (engine models are ai_workflow_engine.models)
    "bot",
    "main",
}

# G-int2: the ONLY surface node handlers may use on the services boundary object.
ALLOWED_NODE_SERVICE_ATTRS = {
    "runtime",
    "subworkflows",
    "scheduling",
    "node_input",
    "record",
    "invoke_bound",
    "context_for_node",
    "sequential_predecessor",
    "child_context",
    "run_child",
}


def _python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_engine_imports_no_product_code():
    """G-int1: no module under ai_workflow_engine/ imports repo-level product packages."""

    offenders = []
    for path in _python_files(ENGINE_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name in FORBIDDEN_PRODUCT_MODULES:
                    offenders.append(f"{path.relative_to(ENGINE_ROOT.parent)}:{node.lineno} imports {name}")
    assert not offenders, "engine imports product code:\n" + "\n".join(offenders)


def test_release_version_matches_current_pin():
    """Release guard: a pinned tag must not build a wheel that reports the previous version."""

    expected = "0.6.5"
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == expected
    assert ai_workflow_engine.__version__ == expected


def test_nodes_use_only_the_node_services_surface():
    """G-int2: node handlers depend on NodeExecutionServices, never executor internals."""

    nodes_root = ENGINE_ROOT / "nodes"
    offenders = []
    for path in _python_files(nodes_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # No import of the executor module in any form.
            if isinstance(node, ast.ImportFrom) and node.module and "executor" in node.module.split("."):
                offenders.append(f"{path.name}:{node.lineno} imports {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "executor" in alias.name.split("."):
                        offenders.append(f"{path.name}:{node.lineno} imports {alias.name}")
            # services.<attr> must stay inside the declared protocol surface.
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "services"
                and node.attr not in ALLOWED_NODE_SERVICE_ATTRS
            ):
                offenders.append(f"{path.name}:{node.lineno} uses services.{node.attr} (not in protocol)")
    assert not offenders, "nodes reach past NodeExecutionServices:\n" + "\n".join(offenders)


def test_capability_status_vocabulary_is_closed():
    """G-ext3: the run-state vocabulary is a closed set — widening it is a reviewed act."""

    assert set(get_args(CapabilityStatus)) == {"accepted", "failed", "partial", "rejected"}


def test_workflow_result_status_carries_suspension_semantics():
    """G-ext3: workflow-level statuses keep the suspension/partial/failure vocabulary."""

    statuses = set(get_args(WorkflowResultStatus))
    assert {"completed", "partial", "failed", "requires_user_input"} <= statuses


def test_handoff_docs_carry_the_adopter_contract_ac():
    """G-ext3: consumer handoffs must keep the adopter AC template (projection guards live
    in adopter repos; the engine ships the template)."""

    for name in ("mageqa-handoff.md", "gopro-handoff.md"):
        text = (DOCS_ROOT / name).read_text(encoding="utf-8")
        assert "Adopter contract AC" in text, f"{name} lost the adopter AC template"
        assert "One provider door" in text, f"{name} lost the one-door rule"


def test_reserved_state_keys_match_the_workflow_state_schema():
    """A5: the reserved-key set and the executor's state schema may never drift apart."""

    from ai_workflow_engine._runtime_state import RESERVED_STATE_KEYS
    from ai_workflow_engine.executor import WorkflowState

    assert set(WorkflowState.__annotations__) == set(RESERVED_STATE_KEYS)


def test_node_handlers_write_only_reserved_state_keys():
    """A5: a typo'd control key in a node's state update must fail the build, not no-op."""

    from ai_workflow_engine._runtime_state import RESERVED_STATE_KEYS

    offenders = []
    for path in _python_files(ENGINE_ROOT / "nodes"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # update["key"] = ... / state["key"] = ... with a literal key
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in {"update", "state"}
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                    and target.slice.value not in RESERVED_STATE_KEYS
                ):
                    offenders.append(f"{path.name}:{node.lineno} writes state key {target.slice.value!r}")
    assert not offenders, "node handlers write non-reserved state keys:\n" + "\n".join(offenders)


def test_inject_machine_branch_requires_describe_for_every_label():
    """G-desc: an injected machine card may not contain an undescribed option (goblin rule)."""

    with pytest.raises(WorkflowValidationError) as exc:
        (
            WorkflowBuilder("g_desc_incomplete")
            .step("prepare")
            .branch(
                "gate",
                {"go": "onward", "stop": "halt_note"},
                describe={"go": "proceed with the plan"},  # "stop" left undescribed on purpose
                inject_machine=True,
            )
            .step("onward")
            .step("halt_note")
            .build()
        )
    assert "stop" in str(exc.value) and "inject_machine" in str(exc.value)


def test_inject_machine_branch_with_full_describe_validates():
    definition = (
        WorkflowBuilder("g_desc_complete")
        .step("prepare")
        .branch(
            "gate",
            {"go": "onward", "stop": "halt_note"},
            describe={"go": "proceed with the plan", "stop": "record why we cannot proceed"},
            inject_machine=True,
        )
        .step("onward")
        .step("halt_note")
        .build()
    )
    assert definition.validate_graph() == []


def test_branch_without_inject_machine_keeps_describe_optional():
    definition = (
        WorkflowBuilder("g_desc_optional")
        .step("prepare")
        .branch("gate", {"go": "onward", "stop": "halt_note"})
        .step("onward")
        .step("halt_note")
        .build()
    )
    assert definition.validate_graph() == []
