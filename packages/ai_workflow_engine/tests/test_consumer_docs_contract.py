"""Consumer-doc truth guards (codex Batch-B review findings 1-6).

The SlackAzz handoff was published with a protocol table, imports, and examples that
disagreed with the public API. These guards lock every corrected snippet to the REAL
surface so consumer docs can never drift silently again.
"""

import inspect
import asyncio
import re
import runpy
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
    # A8: the advertised type must be RUNTIME-RESOLVABLE (not just annotation text) for any
    # process that has engaged the durable-wait subsystem — the realistic adapter path.
    # Engaging waits binds WaitDeliveryOutcome into builder globals (it stays OUT of the
    # simple tier by design), so get_type_hints then resolves the public door.
    import typing

    from ai_workflow_engine import InMemoryWaitCoordinator, WorkflowEngineBuilder

    def _clock():
        from datetime import datetime, timezone

        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    WorkflowEngineBuilder().with_wait_coordinator(
        InMemoryWaitCoordinator(clock=_clock), clock=_clock
    )
    hints = typing.get_type_hints(WorkflowEngine.deliver_wait_event)
    assert hints["return"] is WaitDeliveryOutcome, (
        "deliver_wait_event's return type must resolve via get_type_hints once waits are engaged"
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
    assert (
        f"checkout --detach {current}" in README
        or f"@{current}#subdirectory" in README
    ), "README install instructions must pin the current immutable tag"
    assert "`engine-v0.8.1` is the current release" not in README
    for name in ("gopro-handoff.md", "mageqa-handoff.md"):
        doc = (PACKAGE_ROOT / "docs" / name).read_text(encoding="utf-8")
        normalized = " ".join(doc.split())
        assert f"pin tag `{current}`" in normalized, f"{name} PIN header must name {current}"
        assert "is the current pin):**" not in doc.replace(
            f"moving your pin from `engine-v0.8.1`):**", ""
        ), f"{name} still carries a stale current-pin instruction"


def test_documentation_index_links_every_consumer_path_and_canonical_guide():
    index = (PACKAGE_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    for name in (
        "getting-started.md",
        "concepts.md",
        "operations.md",
        "misuse-risks.md",
        "extension-lifecycle.md",
        "observability-levels-feedback.md",
        "gopro-handoff.md",
        "mageqa-handoff.md",
        "slackazz-handoff.md",
        "voice-brain-handoff.md",
    ):
        assert f"({name})" in index, f"documentation index lost {name}"


def test_all_relative_markdown_links_resolve():
    """AI/humans must be able to traverse the documentation without repository archaeology."""

    docs = [PACKAGE_ROOT / "README.md", *sorted((PACKAGE_ROOT / "docs").rglob("*.md"))]
    pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    missing = []
    for document in docs:
        for target in pattern.findall(document.read_text(encoding="utf-8")):
            path_text = target.split("#", 1)[0]
            if not path_text or "://" in path_text or path_text.startswith("mailto:"):
                continue
            target_path = (document.parent / path_text).resolve()
            if not target_path.exists():
                missing.append(f"{document.relative_to(PACKAGE_ROOT)} -> {target}")
    assert not missing, "broken documentation links:\n" + "\n".join(missing)


def test_product_handoffs_are_current_focused_and_share_the_common_contract():
    required = {
        "gopro-handoff.md": ("GoPro-Specific Misuse Risks", "Adopter Contract AC"),
        "mageqa-handoff.md": ("MageQA-Specific Misuse Risks", "Adopter Contract AC"),
        "slackazz-handoff.md": ("SlackAzz-Specific Misuse Risks", "Adopter Contract AC"),
        "voice-brain-handoff.md": ("Voice-Specific Misuse Risks", "Adopter Contract AC"),
    }
    for name, headings in required.items():
        text = (PACKAGE_ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "engine-v0.9.0" in text
        assert "extension-lifecycle.md" in text
        assert "One Provider Door" in text
        for heading in headings:
            assert heading in text, f"{name} lost {heading}"
        assert len(text.splitlines()) <= 350, f"{name} regressed into a release-history dump"
        assert "v0.7.0 delta" not in text and "v0.8.1 delta" not in text


def test_framework_request_lifecycle_is_actionable_and_closed():
    text = (PACKAGE_ROOT / "docs" / "extension-lifecycle.md").read_text(encoding="utf-8")
    for status in (
        "`proposed`",
        "`accepted-adapter`",
        "`accepted-engine`",
        "`implemented-unreleased`",
        "`released`",
        "`adopted`",
        "`closed`",
        "`rejected`",
    ):
        assert status in text
    for section in (
        "Where Requests Live",
        "Request Template",
        "Engine Triage Questions",
        "Delivery Requirements",
        "Feedback After Adoption",
        "Documentation Defects",
        "Closing A Request",
    ):
        assert section in text


def test_documented_examples_execute_through_public_engine_doors():
    examples = PACKAGE_ROOT / "docs" / "examples"
    for name in ("minimal_step.py", "observed_workflow.py", "durable_wait.py"):
        namespace = runpy.run_path(str(examples / name), run_name=f"docs_example_{name}")
        asyncio.run(namespace["main"]())
