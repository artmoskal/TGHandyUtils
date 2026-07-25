"""Contract guards — the load-bearing seams, each with one fail-build check.

Style follows test_examples_contain_no_product_orchestration_loops: dependency-free
source/AST inspection; failures name the offending file:line so the fix is obvious.
Widening an allow-list or a status vocabulary is a deliberate reviewed decision made in
THIS file, never a silent drift.
"""

import ast
import re
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
REPO_ROOT = Path(__file__).resolve().parents[3]

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
    "gather_bound",
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

    expected = "0.11.13"
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


def test_extracted_runtime_owners_never_import_the_executor():
    """Ownership moves one way: collaborators may not regain the executor as a locator."""

    owner_names = {
        "machine_compiler.py",
        "node_replay.py",
        "node_services.py",
        "suspension.py",
        "result_assembly.py",
    }
    offenders = []
    for path in (ENGINE_ROOT / name for name in sorted(owner_names)):
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            elif isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            else:
                continue
            for module in imported:
                if module == "ai_workflow_engine.executor" or module.startswith(
                    "ai_workflow_engine.executor."
                ):
                    offenders.append(f"{path.name}:{node.lineno} imports {module}")
    assert not offenders, "runtime owner imports executor:\n" + "\n".join(offenders)


def test_observation_bundle_owners_have_one_way_dependencies():
    """Bundle truth flows contract -> retention/writer -> canonical composition door."""

    modules = {
        name: ast.parse((ENGINE_ROOT / name).read_text(encoding="utf-8"))
        for name in (
            "observation_contract.py",
            "observation_retention.py",
            "observation_writer.py",
            "observation_bundle.py",
        )
    }

    def imports(tree: ast.AST) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
        return found

    contract_imports = imports(modules["observation_contract.py"])
    assert not {
        name
        for name in contract_imports
        if name.startswith("ai_workflow_engine.") or name.startswith("ai_workflow_viewer")
    }, "persisted observation contract must stay dependency-light"

    retention_imports = imports(modules["observation_retention.py"])
    assert "ai_workflow_engine.observation_contract" in retention_imports
    assert not {
        "ai_workflow_engine.observation_writer",
        "ai_workflow_engine.observation_bundle",
    } & retention_imports

    writer_imports = imports(modules["observation_writer.py"])
    assert {
        "ai_workflow_engine.observation_contract",
        "ai_workflow_engine.observation_retention",
    } <= writer_imports
    assert not any(name.startswith("ai_workflow_viewer") for name in writer_imports)

    facade = modules["observation_bundle.py"]
    implementations = [
        node.name
        for node in facade.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert implementations == [], (
        "canonical observation_bundle door must compose owners, not regrow implementations: "
        f"{implementations}"
    )


def test_executor_contains_no_compiler_implementation_twins():
    """The public compile/preflight doors delegate; compiler mechanics have one owner."""

    tree = ast.parse((ENGINE_ROOT / "executor.py").read_text(encoding="utf-8"))
    executor = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WorkflowExecutor"
    )
    forbidden = {
        "_evict_compiled",
        "_wire_edges",
        "_route_for",
        "_nested_suspension_error",
        "_model_profile_error",
        "_required_capabilities",
        "_project_terminal_status",
    }
    present = {
        node.name
        for node in executor.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not (present & forbidden), f"compiler twins remain on executor: {sorted(present & forbidden)}"


def test_node_service_owns_behavior_without_an_executor_back_reference():
    """The concrete node boundary is real ownership, not private-method forwarding."""

    source = (ENGINE_ROOT / "node_services.py").read_text(encoding="utf-8")
    assert "_executor" not in source
    tree = ast.parse((ENGINE_ROOT / "executor.py").read_text(encoding="utf-8"))
    executor = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WorkflowExecutor"
    )
    forbidden = {
        "_sequential_predecessor",
        "_invoke_bound",
        "_context_for_node",
        "_child_context",
        "_node_input",
        "_record",
    }
    present = {
        node.name
        for node in executor.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not (present & forbidden), f"node-service twins remain: {sorted(present & forbidden)}"

    bypasses = []
    for path in _python_files(ENGINE_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            reaches_executor = (
                isinstance(node, ast.Attribute)
                and (
                    isinstance(node.value, ast.Name) and node.value.id == "executor"
                    or isinstance(node.value, ast.Attribute) and node.value.attr == "executor"
                )
            )
            if reaches_executor and node.attr in forbidden:
                bypasses.append(f"{path.name}:{node.lineno} reaches {node.attr}")
    assert not bypasses, "engine code reaches removed node-service internals:\n" + "\n".join(bypasses)

    assigned = {
        target.attr
        for node in ast.walk(executor)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    }
    assert not ({"_scheduled_tasks", "_scheduled_cancellations"} & assigned)


def test_executor_is_only_the_run_resume_coordinator():
    """Snapshot, wait registration, and envelope projection have dedicated owners."""

    executor_path = ENGINE_ROOT / "executor.py"
    tree = ast.parse(executor_path.read_text(encoding="utf-8"))
    executor = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WorkflowExecutor"
    )
    forbidden = {
        "_suspension_occurrence",
        "_build_snapshot",
        "_register_durable_or_fold",
        "_maybe_register_durable_wait",
        "_envelope",
        "_failed_envelope",
    }
    present = {
        node.name
        for node in executor.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not (present & forbidden), f"suspension/result twins remain: {sorted(present & forbidden)}"
    assert len(executor_path.read_text(encoding="utf-8").splitlines()) <= 750


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
        assert "adopter contract ac" in text.lower(), f"{name} lost the adopter AC template"
        assert "one provider door" in text.lower(), f"{name} lost the one-door rule"


def test_reserved_state_keys_match_the_workflow_state_schema():
    """A5: the reserved-key set and the executor's state schema may never drift apart."""

    from ai_workflow_engine._runtime_state import RESERVED_STATE_KEYS
    from ai_workflow_engine.executor import WorkflowState

    assert set(WorkflowState.__annotations__) == set(RESERVED_STATE_KEYS)


def test_lifecycle_algebra_inventory_is_complete():
    """B1: every engine state channel is classified in the lifecycle algebra inventory —
    adding a state key without declaring its write rule fails BY NAME here (and the A5
    schema guard above keeps the key set itself honest). Rules are a closed vocabulary so
    an ambiguous classification cannot hide as free text."""

    import json

    from ai_workflow_engine._runtime_state import RESERVED_STATE_KEYS

    inventory = json.loads(
        (Path(__file__).parent / "fixtures" / "lifecycle_state_algebra.json").read_text(
            encoding="utf-8"
        )
    )
    channels = inventory["channels"]

    unclassified = set(RESERVED_STATE_KEYS) - set(channels)
    assert not unclassified, (
        f"state channels without a declared write rule (add them to "
        f"lifecycle_state_algebra.json): {sorted(unclassified)}"
    )
    phantom = set(channels) - set(RESERVED_STATE_KEYS)
    assert not phantom, f"inventory rows for non-existent state channels: {sorted(phantom)}"

    allowed_rules = re.compile(
        r"^(accumulate\((append|counter|key=node_id)\)"
        r"|replace(\(object\) with internal merge contract)?"
        r"|reset\((segment|consumption)\)"
        r"|derive)"
    )
    for name, row in channels.items():
        assert allowed_rules.match(row["rule"]), (
            f"channel {name!r} has a rule outside the closed vocabulary: {row['rule']!r}"
        )
        assert row.get("scope", "").strip(), f"channel {name!r} must declare its scope"
        assert row.get("write_sites"), f"channel {name!r} must name at least one write site"
        assert isinstance(row.get("snapshot_persisted"), bool), (
            f"channel {name!r} must state snapshot persistence explicitly"
        )
        for site in row["write_sites"]:
            source, separator, owner_text = site.partition("::")
            source = source.split(" ", 1)[0]
            path = ENGINE_ROOT / source
            if not path.exists():
                path = ENGINE_ROOT.parent / source
            assert path.exists(), (
                f"channel {name!r} names a write-site file that does not exist: {site!r}"
            )
            if separator:
                owner = owner_text.split(" ", 1)[0]
                assert _python_owner_exists(path, owner), (
                    f"channel {name!r} names a Python owner that does not exist: {site!r}"
                )

    # Snapshot persistence must agree with the REAL MachineSnapshot schema: an accumulate-
    # class channel claimed as persisted must be a snapshot field, and vice versa.
    from ai_workflow_engine.snapshot import MachineSnapshot

    snapshot_fields = set(MachineSnapshot.model_fields)
    alias = {"payload": "payload", "usage_summary": "usage"}
    for name, row in channels.items():
        field = alias.get(name, name)
        if row["snapshot_persisted"]:
            assert field in snapshot_fields, (
                f"channel {name!r} claims snapshot persistence but MachineSnapshot has no "
                f"field {field!r}"
            )
        else:
            assert field not in snapshot_fields or name == "status", (
                f"channel {name!r} claims NO snapshot persistence but MachineSnapshot "
                f"carries {field!r}"
            )


def _python_owner_exists(path: Path, owner: str) -> bool:
    """Resolve ``function`` or ``Class.method`` against one module's real AST."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    parts = owner.split(".")
    if len(parts) == 1:
        return any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parts[0]
            for node in tree.body
        )
    if len(parts) != 2:
        return False
    class_name, method_name = parts
    return any(
        isinstance(node, ast.ClassDef)
        and node.name == class_name
        and any(
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == method_name
            for child in node.body
        )
        for node in tree.body
    )


def test_lifecycle_owner_guard_rejects_invented_symbols(tmp_path):
    module = tmp_path / "owner.py"
    module.write_text("class RealOwner:\n    def commit(self):\n        return None\n")

    assert _python_owner_exists(module, "RealOwner.commit")
    assert not _python_owner_exists(module, "RealOwner.invented")
    assert not _python_owner_exists(module, "Invented.commit")


def test_adopter_promises_map_to_evidence():
    """B3 (GAP-1 root-cause class): every numbered promise in every consumer handoff's
    'Adopter Contract AC' section is classified in promise_evidence_registry.json. A NEW
    AC without a registry row fails by doc+number; a reworded promise fails by its anchor;
    engine-enforceable rows must reference algebra rows, tests, and oracle rows that EXIST.
    This guard proves the evidence LINKS resolve — the semantic proof itself lives in the
    named tests and their mutation kills, never in name matching."""

    import json

    registry = json.loads(
        (Path(__file__).parent / "fixtures" / "promise_evidence_registry.json").read_text(
            encoding="utf-8"
        )
    )
    algebra = json.loads(
        (Path(__file__).parent / "fixtures" / "lifecycle_state_algebra.json").read_text(
            encoding="utf-8"
        )
    )
    algebra_rows = set(algebra["channels"])

    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from invocation_oracle import BEHAVIOR_ROWS
    finally:
        sys.path.pop(0)

    test_roots = [
        Path(__file__).parent,
        REPO_ROOT / "packages" / "ai_workflow_tools" / "tests",
        REPO_ROOT / "packages" / "ai_workflow_viewer" / "tests",
    ]
    tests_text = "\n".join(
        path.read_text(encoding="utf-8")
        for root in test_roots
        for path in root.glob("test_*.py")
    )

    sources = [
        (doc_name, DOCS_ROOT / doc_name, "## Adopter Contract AC", promises)
        for doc_name, promises in registry["sources"].items()
    ]
    sources.extend(
        (
            source_name,
            REPO_ROOT / spec["path"],
            spec["section"],
            spec["promises"],
        )
        for source_name, spec in registry.get("normative_sources", {}).items()
    )

    for doc_name, path, section_heading, promises in sources:
        doc = path.read_text(encoding="utf-8")
        assert section_heading in doc, f"{doc_name}: missing contract section {section_heading!r}"
        section = doc.split(section_heading, 1)[1].split("\n## ", 1)[0]
        documented = set(re.findall(r"^(\d+)\.\s", section, re.M))
        registered = set(promises)
        assert documented == registered, (
            f"{doc_name}: adopter promises drifted from the evidence registry — "
            f"unregistered ACs {sorted(documented - registered, key=int)}, "
            f"stale registry rows {sorted(registered - documented, key=int)}"
        )
        for number, row in promises.items():
            assert row["anchor"] in section, (
                f"{doc_name} AC-{number}: anchor {row['anchor']!r} no longer appears — the "
                f"promise wording changed; re-verify its evidence mapping"
            )
            if row["class"] == "consumer-repo-obligation":
                assert row.get("waiver", "").strip(), (
                    f"{doc_name} AC-{number}: consumer obligations need an explicit waiver"
                )
                continue
            assert row["class"] == "engine-enforceable", (
                f"{doc_name} AC-{number}: unknown class {row['class']!r}"
            )
            for algebra_row in row.get("algebra_rows", []):
                assert algebra_row in algebra_rows, (
                    f"{doc_name} AC-{number}: unknown algebra row {algebra_row!r}"
                )
            assert row.get("tests"), f"{doc_name} AC-{number}: name at least one test"
            for test_name in row["tests"]:
                assert f"def {test_name}(" in tests_text, (
                    f"{doc_name} AC-{number}: referenced test does not exist: {test_name}"
                )
            for oracle_row in row.get("oracle_rows", []):
                assert oracle_row in BEHAVIOR_ROWS, (
                    f"{doc_name} AC-{number}: unknown sealed oracle row {oracle_row!r}"
                )
            assert row.get("mutation", "").strip(), (
                f"{doc_name} AC-{number}: state the mutation evidence"
            )

    # v0.11.5 hardening (codex-settled): the SlackAzz health promise regressed once by being
    # mapped to bundle evidence while the PUBLIC WaitHealth model lacked the promised fields.
    # Its evidence must always include the health-model surface directly.
    slackazz_health = registry["sources"]["slackazz-handoff.md"]["11"]
    assert slackazz_health.get("surface") == "WaitHealth", (
        "slackazz AC-11 must bind to the WaitHealth model surface explicitly"
    )
    # R1.4: CONJUNCTIVE evidence — the promise needs BOTH families; either alone regressed once.
    assert any(
        "health" in test_name or "failed" in test_name
        for test_name in slackazz_health["tests"]
    ), "slackazz AC-11 must carry health-STATUS evidence (failed-count surface)"
    assert any(
        "integrity" in test_name for test_name in slackazz_health["tests"]
    ), "slackazz AC-11 must carry integrity-conformance evidence"


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


# ---------------------------------------------- Phase F: import surface + cycles + gradient

FORBIDDEN_SIMPLE_TIER_MODULES = [
    "ai_workflow_engine.waits",
    "ai_workflow_engine.wait_runtime",
    "ai_workflow_engine.segment_lifecycle",
    "ai_workflow_engine.memory",
    "ai_workflow_engine.flow_authoring",
    "ai_workflow_engine.vision",
    "ai_workflow_engine.engine.llm_node",
    "ai_workflow_engine.observation_bundle",
    "ai_workflow_viewer",
    "ai_workflow_tools",
    "transformers",
    "numpy",
]

_SIMPLE_TIER_SCRIPT = """
import asyncio, json, sys
from ai_workflow_engine import run_single_step
out = asyncio.run(run_single_step(lambda ctx, p: {"ok": p["x"] * 2}, {"x": 21}))
assert out == {"ok": 42}, out
loaded = [m for m in %r if m in sys.modules]
print(json.dumps(loaded))
"""


def _run_simple_tier_subprocess() -> list[str]:
    import json
    import subprocess
    import sys
    from pathlib import Path

    import ai_workflow_engine

    package_parent = str(Path(ai_workflow_engine.__file__).resolve().parents[1])
    proc = subprocess.run(
        [sys.executable, "-c", _SIMPLE_TIER_SCRIPT % (FORBIDDEN_SIMPLE_TIER_MODULES,)],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": package_parent, "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, f"simple-tier subprocess failed:\n{proc.stderr[-2000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_simple_tier_run_loads_no_advanced_facility_modules():
    """F1.1/F1.4: importing AND RUNNING run_single_step must not load memory, flow
    authoring, vision/LLM-node, observation bundles, the viewer, tools, or the
    transformers/numpy surface — trivial consumers pay only for what they use."""

    loaded = _run_simple_tier_subprocess()
    assert loaded == [], f"simple tier loaded advanced modules: {loaded}"


def test_every_public_export_still_resolves_lazily():
    """F1.1 degradation guard: the lazy surface keeps EVERY public name resolvable."""

    import ai_workflow_engine
    import ai_workflow_engine.engine as engine_pkg

    for name in ai_workflow_engine.__all__:
        assert getattr(ai_workflow_engine, name) is not None, name
    for name in engine_pkg.__all__:
        assert getattr(engine_pkg, name) is not None, name


def test_engine_package_has_no_module_level_import_cycles():
    """F1.2: zero SCCs among engine modules at MODULE level (runtime-local imports inside
    function bodies are the sanctioned escape hatch and are excluded). The guard names the
    cycle members on failure."""

    import ast
    from pathlib import Path

    import ai_workflow_engine

    package_root = Path(ai_workflow_engine.__file__).resolve().parent
    prefix = "ai_workflow_engine"
    graph: dict[str, set[str]] = {}

    def module_parts(path: Path) -> tuple[list[str], bool]:
        rel = path.relative_to(package_root.parent).with_suffix("")
        parts = list(rel.parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        return parts, is_package

    def _is_type_checking_if(node: ast.If) -> bool:
        test = node.test
        return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )

    def top_level_engine_imports(tree: ast.Module, parts: list[str], is_package: bool) -> set[str]:
        # F-C4: module-level means EVERY statement outside def/class — including try/except
        # and runtime `if` bodies; only `if TYPE_CHECKING:` is a non-runtime edge. Relative
        # imports resolve against the module's package so they are edges too.
        package = parts if is_package else parts[:-1]
        found: set[str] = set()

        def visit(statements) -> None:
            for node in statements:
                if isinstance(node, ast.ImportFrom):
                    if node.level == 0:
                        module = node.module or ""
                    else:
                        base = package[: len(package) - (node.level - 1)]
                        module = ".".join(base + (node.module.split(".") if node.module else []))
                    if module.startswith(prefix):
                        found.add(module)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith(prefix):
                            found.add(alias.name)
                elif isinstance(node, ast.If):
                    if _is_type_checking_if(node):
                        visit(node.orelse)  # the else-branch of TYPE_CHECKING IS runtime
                    else:
                        visit(node.body)
                        visit(node.orelse)
                elif isinstance(node, ast.Try):
                    visit(node.body)
                    for handler in node.handlers:
                        visit(handler.body)
                    visit(node.orelse)
                    visit(node.finalbody)

        visit(tree.body)
        return found

    for path in sorted(package_root.rglob("*.py")):
        parts, is_package = module_parts(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        graph[".".join(parts)] = top_level_engine_imports(tree, parts, is_package)

    # Tarjan SCC
    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    cycles: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack[v] = True
        for w in graph.get(v, ()):  # edges to known modules only
            if w not in graph:
                continue
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif on_stack.get(w):
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                component.append(w)
                if w == v:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

    for node_name in list(graph):
        if node_name not in index:
            strongconnect(node_name)

    assert cycles == [], f"engine module-level import cycles: {cycles}"

    # Layering half of F1.2 (the historical cycle was closed by a runtime-local edge a pure
    # SCC check cannot see): the transport/protocol layers must never import UPWARD.
    LAYER_FORBIDDEN = {
        "ai_workflow_engine.transport_models": ("ai_workflow_engine.vision", "ai_workflow_engine.llm_protocol", "ai_workflow_engine.engine"),
        "ai_workflow_engine.llm_protocol": ("ai_workflow_engine.vision", "ai_workflow_engine.engine"),
        # W2A.3: the lifecycle service must NEVER import machine execution — the resume
        # path is an injected port, not an import.
        "ai_workflow_engine.wait_runtime": ("ai_workflow_engine.executor",),
        "ai_workflow_engine.waits": ("ai_workflow_engine.executor",),
        # R1: the segment-lifecycle owner is observation mechanics — it must never import
        # machine execution or wait lifecycle (the facade composes them).
        "ai_workflow_engine.segment_lifecycle": (
            "ai_workflow_engine.executor",
            "ai_workflow_engine.wait_runtime",
            "ai_workflow_engine.waits",
        ),
    }
    layering = [
        f"{module} -> {edge}"
        for module, banned in LAYER_FORBIDDEN.items()
        for edge in graph.get(module, ())
        if any(edge == b or edge.startswith(b + ".") for b in banned)
    ]
    assert layering == [], f"upward imports break the transport/protocol layering: {layering}"


async def test_complexity_gradient_matrix_contract():
    """F1.4/F-C3: one guard for the gradient — the medium tier registers a pack with no
    engine edit; the advanced tier opts into fanout + PER-NODE MEMORY + observation
    independently (the memory policy is real and its delivery is asserted, not implied);
    every tier runs the same executor contract. (Simple tier = the subprocess test above.)"""

    from ai_workflow_engine import (
        ObservationConfig,
        WorkflowBuilder,
        WorkflowEngineBuilder,
    )

    # medium: a pack registers capabilities/workflows without touching engine source
    class MediumPack:
        def register(self, builder):
            builder.register_capability("double", lambda ctx, p: {"v": p["v"] * 2}, kind="deterministic")
            builder.register_workflow(WorkflowBuilder("medium_flow").step("double").build())

    medium = WorkflowEngineBuilder().register_pack(MediumPack()).build()
    result = await medium.run("medium_flow", {"v": 5})
    assert result.status == "completed" and result.output == {"v": 10}

    # advanced: fanout + per-node memory + observation, opted into independently
    import tempfile

    # F-C3 (as agreed): the advanced tier runs a REAL fake-provider agent worker whose
    # node-level memory policy actually RENDERS the projection the provider consumes, and
    # the memory:projection observation is asserted — not merely a config dict in metadata.
    from ai_workflow_engine import (
        LLMResponse,
        StructuredStateMemory,
        ToolCallRequest,
        build_llm_agent_capability,
    )
    from pydantic import BaseModel

    class TotalSummary(BaseModel):
        summary: str

    class ScriptedAgentLLM:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return LLMResponse(
                    tool_calls=[
                        ToolCallRequest(
                            call_id="c1", name="record_total", arguments={"total": 12}
                        )
                    ]
                )
            return LLMResponse(text='{"summary": "totals recorded"}')

    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory() as tmp:
        builder = WorkflowEngineBuilder().with_observation(
            ObservationConfig(enabled=True, bundle_dir=tmp)
        )
        builder.register_capability("spread", lambda ctx, p: {"items": [1, 2, 3]}, kind="deterministic")
        builder.register_capability("double", lambda ctx, item: item * 2, kind="deterministic")
        builder.register_capability(
            "record_total", lambda ctx, payload: {"noted": payload["total"]}, kind="tool"
        )
        builder.register_capability(
            "prepare_request",
            lambda ctx, items: {
                "prompt": f"Summarize doubled items {sorted(items)}.",
                "allowed_tools": ["record_total"],
                "max_steps": 3,
            },
            kind="deterministic",
        )
        builder.register_workflow(
            WorkflowBuilder("advanced_flow")
            .step("spread")
            .fanout("fan", capability="double", items_key="spread.items", max_parallel=2)
            .step("prepare_request")
            .step("summarize_agent", memory={"mode": "structured_state"})
            .build()
        )
        advanced = builder.build()
        client = ScriptedAgentLLM()
        agent = build_llm_agent_capability(
            client,
            advanced.registry,
            allowed_tools=["record_total"],
            name="summarize_agent",
            system_prompt="Summarize via typed tools.",
            output_model=TotalSummary,
            node_name="summarize_agent",
        )
        advanced.registry.register(agent.spec, agent)

        adv = await advanced.run("advanced_flow", {})

        assert adv.status == "completed"
        assert adv.observation_bundle_path, "advanced tier opted into observation"
        # W5.3: a DURABLE-wait engine is the SAME executor/runtime class as every other
        # tier — waits are composition (with_wait_coordinator), never a second engine.
        from datetime import datetime, timezone

        from ai_workflow_engine import InMemoryWaitCoordinator

        clock = lambda: datetime(2036, 1, 1, tzinfo=timezone.utc)  # noqa: E731
        durable_engine = (
            WorkflowEngineBuilder()
            .with_wait_coordinator(InMemoryWaitCoordinator(clock=clock), clock=clock)
            .build()
        )
        assert type(durable_engine.executor) is type(advanced.executor), (
            "durable waits must run on the SAME executor class — no parallel runtime"
        )
        assert getattr(advanced.executor, "wait_runtime", None) is None, (
            "tiers that never opted into waits carry NO wait machinery"
        )
        # 1) the node-level policy really RENDERED: the second provider call consumes the
        #    StructuredStateMemory state block (projection -> provider, not just metadata)
        assert len(client.requests) == 2
        second_call_text = "\n".join(
            (m.content or "") for m in client.requests[1].messages
        )
        assert StructuredStateMemory.DEFAULT_HEADING in second_call_text, (
            "the provider must consume the structured-state projection on the next step"
        )
        # 2) the memory:projection observation is recorded with the REAL policy identity
        projections = [
            e for e in advanced.trace_sink.events if e.decision == "memory:projection"
        ]
        assert projections, "the advanced tier must record memory:projection observations"
        assert any(
            e.metadata.get("memory_mode") == "StructuredStateMemory" for e in projections
        ), "the projection must come from the DECLARED node-level structured_state policy"


def test_packaging_ships_types_pins_and_a_standalone_viewer():
    """F1.3/F-4: py.typed ships in ALL three packages; the tools->engine dependency is
    BOUNDED (never open-ended); the viewer is an installable package whose dependency
    direction is viewer->engine only."""

    from pathlib import Path

    import ai_workflow_engine

    packages_root = Path(ai_workflow_engine.__file__).resolve().parents[2]

    for pkg in ("ai_workflow_engine/ai_workflow_engine", "ai_workflow_tools/ai_workflow_tools",
                "ai_workflow_viewer/ai_workflow_viewer"):
        assert (packages_root / pkg / "py.typed").exists(), f"{pkg}/py.typed missing"

    tools_toml = (packages_root / "ai_workflow_tools" / "pyproject.toml").read_text()
    assert '"ai-workflow-engine>=' in tools_toml and ",<" in tools_toml, (
        "tools must pin a BOUNDED engine range"
    )

    viewer_toml_path = packages_root / "ai_workflow_viewer" / "pyproject.toml"
    assert viewer_toml_path.exists(), "viewer must be an installable package (F1.3)"
    viewer_toml = viewer_toml_path.read_text()
    assert 'name = "ai-workflow-viewer"' in viewer_toml
    assert '"ai-workflow-engine>=' in viewer_toml

    # dependency direction: engine source never IMPORTS the viewer (docstring pointers ok)
    import ast as _ast

    engine_src = packages_root / "ai_workflow_engine" / "ai_workflow_engine"
    offenders = []
    for path in engine_src.rglob("*.py"):
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom) and node.module and node.module.startswith("ai_workflow_viewer"):
                offenders.append(f"{path}:{node.lineno}")
            elif isinstance(node, _ast.Import) and any(a.name.startswith("ai_workflow_viewer") for a in node.names):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"engine imports the viewer: {offenders}"


def test_static_typing_surface_matches_lazy_exports_exactly():
    """F-C1: py.typed is only honest if type checkers SEE the public names. Both lazy
    __init__ modules must carry an `if TYPE_CHECKING:` import block whose bound names equal
    the _EXPORTS map exactly — no missing name (checker sees Any), no extra name (advertises
    something runtime cannot deliver). Sources must match the runtime map too."""

    import ast
    from pathlib import Path

    import ai_workflow_engine
    import ai_workflow_engine.engine as engine_pkg

    for module in (ai_workflow_engine, engine_pkg):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        static: dict[str, tuple[str, str]] = {}
        for node in tree.body:
            if not (isinstance(node, ast.If) and getattr(node.test, "id", "") == "TYPE_CHECKING"):
                continue
            for stmt in node.body:
                assert isinstance(stmt, ast.ImportFrom), (
                    f"{module.__name__}: TYPE_CHECKING block must contain only from-imports"
                )
                for alias in stmt.names:
                    bound = alias.asname or alias.name
                    static[bound] = (stmt.module, alias.name)
        assert static, f"{module.__name__} has no TYPE_CHECKING static export block"
        runtime = module._EXPORTS
        missing = sorted(set(runtime) - set(static))
        extra = sorted(set(static) - set(runtime))
        assert not missing, f"{module.__name__}: exports invisible to type checkers: {missing}"
        assert not extra, f"{module.__name__}: static-only names runtime cannot deliver: {extra}"
        drifted = sorted(
            name for name, source in static.items() if tuple(runtime[name]) != tuple(source)
        )
        assert not drifted, f"{module.__name__}: static source drifted from _EXPORTS: {drifted}"


def test_top_level_image_input_import_stays_on_the_transport_leaf():
    """F-C2: `from ai_workflow_engine import ImageInput` is the canonical lightweight DTO
    import — it must resolve via transport_models WITHOUT dragging vision, the LangChain
    node, or memory into the process."""

    import json
    import subprocess
    import sys
    from pathlib import Path

    import ai_workflow_engine

    package_parent = str(Path(ai_workflow_engine.__file__).resolve().parents[1])
    script = (
        "import json, sys\n"
        "from ai_workflow_engine import ImageInput\n"
        "img = ImageInput(source='path', data='/tmp/x.png', media_type='image/png')\n"
        "heavy = [m for m in ('ai_workflow_engine.vision', 'ai_workflow_engine.engine.llm_node',"
        " 'ai_workflow_engine.memory', 'langchain_core') if m in sys.modules]\n"
        "print(json.dumps(heavy))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=package_parent,
        timeout=120,
    )
    assert proc.returncode == 0, f"probe failed: {proc.stderr}"
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert loaded == [], (
        f"top-level ImageInput dragged heavy modules into the process: {loaded} — the export "
        "must point at the transport leaf, not the vision façade"
    )


def test_no_class_defines_the_same_method_twice():
    """W3R.2 structural guard: Python silently keeps only the LAST definition when a class
    body defines a method name twice — lifecycle policy would then have a dead source copy
    that future fixes can land in while tests exercise the live one (this exactly happened
    to InMemoryWaitCoordinator's claim/complete/fail/_terminalize block in W3)."""

    import ast
    from pathlib import Path

    package_root = Path(__file__).resolve().parents[1] / "ai_workflow_engine"
    offenders: list[str] = []
    for module in sorted(package_root.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            seen: dict[str, int] = {}
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # property setter/deleter and typing overloads legitimately reuse names
                    decorators = {
                        f"{d.value.id}.{d.attr}"
                        if isinstance(d, ast.Attribute) and isinstance(d.value, ast.Name)
                        else (d.id if isinstance(d, ast.Name) else None)
                        for d in item.decorator_list
                    }
                    if decorators & {f"{item.name}.setter", f"{item.name}.deleter", "overload"}:
                        continue
                    if item.name in seen:
                        offenders.append(
                            f"{module.relative_to(package_root.parent)}:{item.lineno} "
                            f"class {node.name} redefines {item.name!r} "
                            f"(first at line {seen[item.name]})"
                        )
                    else:
                        seen[item.name] = item.lineno
    assert not offenders, "duplicate method definitions shadow silently:\n" + "\n".join(offenders)



_SCAN_EXCLUDED_PARTS = {
    ".git", "__pycache__", "build", "node_modules", ".venv", "venv",
    "test-results", "graphify-out", ".playwright-mcp", "htmlcov",
}


def _find_scan_root(start):
    from pathlib import Path as _P

    node = _P(start).resolve()
    for ancestor in [node] + list(node.parents):
        if (ancestor / ".git").exists() or (ancestor / "environment.yml").exists():
            return ancestor
    # standalone package checkout: scan the package tree we live in
    return _P(start).resolve().parents[1]


def scan_for_vision_image_input_imports(root):
    """Every .py under ``root`` (minus generated/cache dirs) that imports ImageInput from the
    removed vision home. Factored (recheck RR2) so the arbitrary-root test below can prove an
    unlisted location is caught."""

    import ast as _ast

    offenders = []
    for path in sorted(root.rglob("*.py")):
        if _SCAN_EXCLUDED_PARTS.intersection(path.parts):
            continue
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in _ast.walk(tree):
            if (
                isinstance(node, _ast.ImportFrom)
                and node.module == "ai_workflow_engine.vision"
                and any(a.name == "ImageInput" for a in node.names)
            ):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    return offenders


def test_image_input_import_home_is_transport_models():
    """v0.11 clean contract (manifest row M4): the vision-module compat re-export marker is gone;
    ImageInput's import homes are the package root and transport_models. vision.py may USE the
    class (real dependency), but nothing may IMPORT it from vision."""

    import ast as _ast
    from pathlib import Path as _Path

    # TRUE repo-wide sweep (recheck RR2): walk the WHOLE repository root recursively — no
    # hand-enumerated directory list to fall out of date. Root discovery: nearest ancestor
    # carrying a repo marker (.git or environment.yml); standalone package checkouts fall back
    # to the package root. The scanner is factored so a test can point it at an ARBITRARY root
    # and prove previously-unlisted locations are caught.
    repo = _find_scan_root(_Path(__file__))
    offenders = scan_for_vision_image_input_imports(repo)
    assert not offenders, f"ImageInput imported from vision (home is transport_models): {offenders}"

    # Runtime half, DYNAMIC so this guard file never contains the offending import shape
    # itself (the repo-wide AST sweep above must not need to exempt its own probe).
    import importlib

    vision = importlib.import_module("ai_workflow_engine.vision")
    assert not hasattr(vision, "ImageInput"), (
        "vision still binds the public ImageInput name — the old import would keep working"
    )
    with pytest.raises(ImportError):
        exec("from ai_workflow_engine.vision import ImageInput")



def test_vision_import_scanner_catches_arbitrary_unlisted_roots(tmp_path):
    """Recheck RR2 lock: the scanner takes a root and walks EVERYTHING under it — a forbidden
    import planted in a never-enumerated directory is found (the old hand-listed roots could
    silently miss new locations)."""

    nested = tmp_path / "totally" / "new_location" / "handlers_modular"
    nested.mkdir(parents=True)
    (nested / "offender.py").write_text(
        "from ai_workflow_engine.vision import ImageInput\n", encoding="utf-8"
    )
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")

    offenders = scan_for_vision_image_input_imports(tmp_path)
    assert offenders == ["totally/new_location/handlers_modular/offender.py:1"], offenders
