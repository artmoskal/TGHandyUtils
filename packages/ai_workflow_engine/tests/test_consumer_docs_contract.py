"""Consumer-doc truth guards (codex Batch-B review findings 1-6).

The SlackAzz handoff was published with a protocol table, imports, and examples that
disagreed with the public API. These guards lock every corrected snippet to the REAL
surface so consumer docs can never drift silently again.
"""

import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SLACKAZZ_DOC = (PACKAGE_ROOT / "docs" / "slackazz-handoff.md").read_text(encoding="utf-8")
README = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")


def test_documented_conformance_kit_import_is_real():
    """The handoff's exact import path must work — a Redis adapter author copies it."""

    from ai_workflow_engine.testing import run_wait_registration_conformance

    assert callable(run_wait_registration_conformance)
    assert "from ai_workflow_engine.testing import run_wait_registration_conformance" in SLACKAZZ_DOC


def test_documented_protocol_table_matches_the_real_protocol():
    """Every documented coordinator member exists; no invented member survives."""

    from ai_workflow_engine.waits import WaitCoordinator

    real = {
        name
        for name, value in vars(WaitCoordinator).items()
        if inspect.isfunction(value) and not name.startswith("_")
    }
    assert len(real) == 11, f"expected the 11-member coordinator protocol, got {sorted(real)}"
    for member in real:
        assert f"`{member}(" in SLACKAZZ_DOC or f"`{member}`" in SLACKAZZ_DOC, (
            f"real coordinator member {member!r} missing from the handoff protocol table"
        )
    for invented in ("extend_lease", "terminalize("):
        assert invented not in SLACKAZZ_DOC, (
            f"handoff documents nonexistent coordinator member {invented!r} — an adapter "
            "written from the doc would fail with_wait_coordinator()"
        )


def test_documented_goal_example_constructs_a_valid_goal():
    """The copy-paste example must validate; the old invalid shape must be gone."""

    from ai_workflow_engine import WorkflowGoal

    goal = WorkflowGoal(
        workflow_type="client_message_triage",
        objective="triage inbound client message",
        correlation_id="case-42",
    )
    assert goal.correlation_id == "case-42"
    assert "WorkflowGoal(workflow_id" not in SLACKAZZ_DOC, (
        "handoff still shows the invalid WorkflowGoal(workflow_id=...) example"
    )
    assert "workflow_type=" in SLACKAZZ_DOC and "objective=" in SLACKAZZ_DOC


def test_delivery_outcome_is_public_and_annotated():
    """Adapters branch on the closed outcome vocabulary — it must be a stable export."""

    import ai_workflow_engine
    from ai_workflow_engine import WaitDeliveryOutcome
    from ai_workflow_engine.builder import WorkflowEngine

    assert "WaitDeliveryOutcome" in dir(ai_workflow_engine)
    annotation = inspect.signature(WorkflowEngine.deliver_wait_event).return_annotation
    assert "WaitDeliveryOutcome" in str(annotation), (
        f"deliver_wait_event must advertise its typed outcome, got {annotation!r}"
    )
    kinds = WaitDeliveryOutcome.model_fields["kind"].annotation
    assert "executed" in str(kinds) and "duplicate" in str(kinds)


def test_handoff_never_claims_coordinator_outbox_atomicity():
    """complete() carries no intent and is engine-invoked — co-commit is unexpressable."""

    assert "never claim coordinator/outbox atomicity" in SLACKAZZ_DOC
    assert "What is NOT atomic" in SLACKAZZ_DOC


def test_release_docs_name_the_current_tag_consistently():
    """No live install/pin instruction may name a superseded tag as current."""

    import ai_workflow_engine

    current = f"engine-v{ai_workflow_engine.__version__}"
    assert f"`{current}` is the current release" in README
    assert f"@{current}#subdirectory" in README, "README install line must pin the current tag"
    assert "`engine-v0.8.1` is the current release" not in README
    for name in ("gopro-handoff.md", "mageqa-handoff.md"):
        doc = (PACKAGE_ROOT / "docs" / name).read_text(encoding="utf-8")
        assert f"pin tag `{current}`" in doc, f"{name} PIN header must name {current}"
        assert "is the current pin):**" not in doc.replace(
            f"moving your pin from `engine-v0.8.1`):**", ""
        ), f"{name} still carries a stale current-pin instruction"
