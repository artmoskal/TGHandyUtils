"""Consumer-doc truth guards (codex Batch-B review findings 1-6).

The SlackAzz handoff was published with a protocol table, imports, and examples that
disagreed with the public API. These guards lock every corrected snippet to the REAL
surface so consumer docs can never drift silently again.
"""

import inspect
import asyncio
import re
import runpy
import tomllib
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
    assert len(real) == 13, f"expected the 13-member coordinator protocol, got {sorted(real)}"
    for member in real:
        assert f"`{member}(" in SLACKAZZ_DOC or f"`{member}`" in SLACKAZZ_DOC, (
            f"real coordinator member {member!r} missing from the handoff protocol table"
        )
    for invented in ("extend_lease", "terminalize("):
        assert invented not in SLACKAZZ_DOC, (
            f"handoff documents nonexistent coordinator member {invented!r} — an adapter "
            "written from the doc would fail with_wait_coordinator()"
        )


def test_documented_wait_delivery_and_compensation_match_current_contract():
    """v0.11.6: adapter authors must copy the handle-bound door and complete abort CAS."""

    from ai_workflow_engine.builder import WorkflowEngine
    from ai_workflow_engine.waits import InMemoryWaitCoordinator, WaitCoordinator

    delivery = inspect.signature(WorkflowEngine.deliver_wait_event)
    assert list(delivery.parameters) == ["self", "handle", "event"]
    assert "deliver_wait_event(handle, event)" in SLACKAZZ_DOC
    assert "deliver_wait_event(wait_id" not in SLACKAZZ_DOC

    expected_abort = [
        "self",
        "wait_id",
        "expected_registration_id",
        "expected_registration_attempt_id",
        "expected_definition_digest",
        "reason",
    ]
    assert list(inspect.signature(WaitCoordinator.abort_registration).parameters) == expected_abort
    assert (
        list(inspect.signature(InMemoryWaitCoordinator.abort_registration).parameters)
        == expected_abort
    )
    for parameter in expected_abort[2:]:
        assert parameter in SLACKAZZ_DOC
    for kind in (
        "absent",
        "cancelled",
        "already_terminal",
        "not_creator",
        "refused_reused",
        "refused_mismatch",
        "refused_active_claim",
    ):
        assert f"`{kind}`" in SLACKAZZ_DOC


def test_all_current_wait_guides_require_the_complete_handle():
    """The binding spec and operational guides may not resurrect bare-id delivery."""

    repo_root = PACKAGE_ROOT.parents[1]
    paths = (
        PACKAGE_ROOT / "README.md",
        PACKAGE_ROOT / "docs" / "getting-started.md",
        PACKAGE_ROOT / "docs" / "operations.md",
        PACKAGE_ROOT / "docs" / "misuse-risks.md",
        PACKAGE_ROOT / "docs" / "mageqa-handoff.md",
        PACKAGE_ROOT / "docs" / "slackazz-handoff.md",
        repo_root / "docs" / "executable-workflow-engine-spec.md",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "deliver_wait_event(wait_id" not in text, (
            f"{path}: stale bare-wait-id continuation instruction survived"
        )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "complete `WaitHandle`" in combined
    assert "abrupt process death" in combined
    assert "observed cancellation" in combined


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
    # The advertised type must be runtime-resolvable before composition. Documentation
    # generators and validation frameworks inspect the public class before an application
    # builds an engine; the DTO lives in the lightweight wait-contract leaf for this reason.
    import typing
    hints = typing.get_type_hints(WorkflowEngine.deliver_wait_event)
    assert hints["return"] is WaitDeliveryOutcome, (
        "deliver_wait_event's return type must resolve before runtime composition"
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
    # Two honest states (5R finding 7): after tagging the README names the current release;
    # BEFORE the tag exists it must say release candidate WITH the do-not-pin-yet caveat —
    # permanent docs may never claim a release that does not exist.
    released = f"`{current}` is the current release" in README
    candidate = (
        f"`{current}` is the release candidate" in README
        and "do not re-pin until it is cut" in README
    )
    assert released or candidate, (
        f"README must either declare {current} released or mark it a pending release candidate"
    )
    assert "verify-bundle" in README, (
        "README install instructions must verify the published release directory"
    )
    permanent_release_docs = {
        "README.md": README,
        **{
            f"docs/{path.name}": path.read_text(encoding="utf-8")
            for path in (PACKAGE_ROOT / "docs").glob("*.md")
        },
    }
    for name, doc in permanent_release_docs.items():
        normalized_doc = " ".join(doc.lower().split())
        assert "pip wheel" not in normalized_doc, (
            f"{name} must not present source rebuilding as consumer artifact verification"
        )
        for false_tag_claim in (
            "tag carries wheel hashes",
            "tag contains wheel hashes",
            "tag includes wheel hashes",
            "tag records wheel hashes",
        ):
            assert false_tag_claim not in normalized_doc, (
                f"{name} falsely assigns published wheel identity to the source tag"
            )
    assert "`engine-v0.8.1` is the current release" not in README
    pin_tag = current
    if candidate:
        prior_release = re.search(
            r"`(engine-v[0-9]+\.[0-9]+\.[0-9]+)` remains the current release",
            README,
        )
        assert prior_release, "candidate README must name the immutable release consumers still pin"
        pin_tag = prior_release.group(1)
    for name in ("gopro-handoff.md", "mageqa-handoff.md"):
        doc = (PACKAGE_ROOT / "docs" / name).read_text(encoding="utf-8")
        normalized = " ".join(doc.split())
        assert f"pin tag `{pin_tag}`" in normalized, f"{name} PIN header must name {pin_tag}"
        if candidate:
            assert f"`{current}`" in normalized and "do not" in normalized.lower(), (
                f"{name} must name {current} as unavailable before its tag exists"
            )
        assert "is the current pin):**" not in doc.replace(
            f"moving your pin from `engine-v0.8.1`):**", ""
        ), f"{name} still carries a stale current-pin instruction"


def test_documentation_index_links_every_consumer_path_and_canonical_guide():
    index = (PACKAGE_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    docs_dir = PACKAGE_ROOT / "docs"
    assert not list(docs_dir.glob("migration-*.md")), (
        "latest-only line (M14): the shipped package must carry no migration guides — "
        "historical lines are documented by their historical tags"
    )
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
    import ai_workflow_engine

    current_tag = f"engine-v{ai_workflow_engine.__version__}"
    for name, headings in required.items():
        text = (PACKAGE_ROOT / "docs" / name).read_text(encoding="utf-8")
        # derive from __version__ so a release bump doesn't require hand-editing each tag literal
        assert current_tag in text, f"{name} must name the current tag {current_tag}"
        assert "extension-lifecycle.md" in text
        assert "One Provider Door" in text
        for heading in headings:
            assert heading in text, f"{name} lost {heading}"
        assert len(text.splitlines()) <= 350, f"{name} regressed into a release-history dump"
        assert "v0.7.0 delta" not in text and "v0.8.1 delta" not in text


def test_viewer_version_and_consumer_package_tables_match():
    """Viewer fixes need a distinguishable wheel version, not reused 0.2.0 metadata."""

    import ai_workflow_viewer

    viewer_root = PACKAGE_ROOT.parent / "ai_workflow_viewer"
    pyproject = tomllib.loads((viewer_root / "pyproject.toml").read_text(encoding="utf-8"))
    expected = pyproject["project"]["version"]
    assert ai_workflow_viewer.__version__ == expected
    package_pin = f"ai-workflow-viewer=={expected}"
    for name in (
        "gopro-handoff.md",
        "mageqa-handoff.md",
        "slackazz-handoff.md",
        "voice-brain-handoff.md",
    ):
        text = (PACKAGE_ROOT / "docs" / name).read_text(encoding="utf-8")
        assert package_pin in text, f"{name} must pin the released viewer wheel {expected}"


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


def test_documented_package_matrix_is_coherent_and_derived_from_pyproject():
    """C2-5: the documented pins form ONE resolvable matrix — versions and the engine
    requirement come from the three pyproject.toml files, never from hand-kept strings.
    A handoff pairing a released engine with candidate tools/viewer could never install."""

    packages_root = PACKAGE_ROOT.parent
    versions: dict[str, str] = {}
    engine_reqs: dict[str, str] = {}
    for pkg in ("ai_workflow_engine", "ai_workflow_tools", "ai_workflow_viewer"):
        data = tomllib.loads((packages_root / pkg / "pyproject.toml").read_text(encoding="utf-8"))
        versions[pkg] = data["project"]["version"]
        for dep in data["project"].get("dependencies", ()):
            flat = dep.replace(" ", "")
            if flat.startswith("ai-workflow-engine"):
                engine_reqs[pkg] = flat
    assert set(engine_reqs) == {"ai_workflow_tools", "ai_workflow_viewer"}

    def _tuple(text: str) -> tuple[int, ...]:
        parts = tuple(int(piece) for piece in text.split("."))
        return parts + (0,) * (3 - len(parts))

    engine = _tuple(versions["ai_workflow_engine"])
    for pkg, req in engine_reqs.items():
        bounds = re.fullmatch(r"ai-workflow-engine>=([0-9.]+),<([0-9.]+)", req)
        assert bounds, f"{pkg} engine requirement {req!r} must be a simple >=,< range"
        assert _tuple(bounds.group(1)) <= engine < _tuple(bounds.group(2)), (
            f"{pkg} requires engine {req!r} but the repository engine is "
            f"{versions['ai_workflow_engine']} — the documented matrix could never resolve"
        )

    # The visible table is one coherent matrix derived from source. During a candidate
    # gate it is explicitly non-installable; after release it becomes the current matrix.
    current_pins = [
        f"ai-workflow-engine=={versions['ai_workflow_engine']}",
        f"ai-workflow-tools=={versions['ai_workflow_tools']}",
        f"ai-workflow-viewer=={versions['ai_workflow_viewer']}",
    ]
    candidate_tag = f"engine-v{versions['ai_workflow_engine']}"
    candidate = f"`{candidate_tag}` is the release candidate" in README
    for name in (
        "gopro-handoff.md",
        "mageqa-handoff.md",
        "slackazz-handoff.md",
        "voice-brain-handoff.md",
    ):
        text = (PACKAGE_ROOT / "docs" / name).read_text(encoding="utf-8")
        for pin in current_pins:
            assert pin in text, f"{name}: current matrix is missing {pin}"
        if candidate:
            assert "Candidate matrix" in text
            assert f"Do not install this matrix until `{candidate_tag}` is cut." in text
            assert "Current matrix" not in text
        else:
            assert "Current matrix" in text
            assert "Candidate matrix" not in text


def test_source_handoffs_never_embed_dynamic_release_evidence():
    """Source is committed before tag/artifact identities exist, so embedding those values
    creates a stale or self-referential release record. Consumers must read them from the
    immutable release directory instead."""

    import ai_workflow_engine

    current_tag = f"engine-v{ai_workflow_engine.__version__}"
    handoffs = tuple(sorted((PACKAGE_ROOT / "docs").glob("*handoff.md")))
    assert handoffs
    for path in handoffs:
        text = path.read_text(encoding="utf-8")
        dynamic_claims = {
            "source commit": r"Source commit:\s*`[0-9a-f]{40}`",
            "tag object": r"Annotated tag object:\s*`[0-9a-f]{40}`",
            "manifest hash": r"Release manifest SHA-256:\s*\n?\s*`[0-9a-f]{64}`",
            "checksum hash": r"SHA256SUMS`? SHA-256:\s*\n?\s*`[0-9a-f]{64}`",
            "wheel hash": r"wheel SHA-256:\s*\n?\s*`[0-9a-f]{64}`",
            "release test count": r"release tier passed\s+`?\d+`?\s+tests",
        }
        for claim, pattern in dynamic_claims.items():
            assert re.search(pattern, text, flags=re.IGNORECASE) is None, (
                f"{path.name} embeds a dynamic {claim}; source handoffs cannot know release "
                "identities generated after commit/tag creation"
            )

        if "### Immutable release evidence" not in text:
            continue
        evidence = text.split("### Immutable release evidence", 1)[1]
        evidence = re.split(r"\n#{2,3} ", evidence, maxsplit=1)[0]
        tags = set(re.findall(r"engine-v\d+\.\d+\.\d+", evidence))
        assert tags <= {current_tag}, (
            f"{path.name} mixes release identities in its current evidence section: {tags}"
        )
        assert "release-manifest.json" in evidence
        assert "SHA256SUMS" in evidence
        assert "does not duplicate" in evidence.lower()


def test_documented_bundle_schema_version_is_the_engine_truth():
    """C2-5: docs state the CURRENT bundle schema version from the engine constant —
    a stale hand-written number misleads every dashboard author."""

    from ai_workflow_engine.observation_bundle import BUNDLE_SCHEMA_VERSION

    text = (PACKAGE_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    found = re.search(r"`bundle_schema_version`\s*\(currently\s*\n?`(\d+)`", text)
    assert found, "operations.md must document the current bundle schema version"
    assert int(found.group(1)) == BUNDLE_SCHEMA_VERSION, (
        f"operations.md claims bundle schema {found.group(1)} but the engine writes "
        f"{BUNDLE_SCHEMA_VERSION}"
    )


def test_current_observation_docs_require_v4_reader_without_physical_parsers():
    current_docs = {
        "concepts.md": (PACKAGE_ROOT / "docs" / "concepts.md").read_text(encoding="utf-8"),
        "getting-started.md": (PACKAGE_ROOT / "docs" / "getting-started.md").read_text(
            encoding="utf-8"
        ),
        "operations.md": (PACKAGE_ROOT / "docs" / "operations.md").read_text(encoding="utf-8"),
        "observability-levels-feedback.md": (
            PACKAGE_ROOT / "docs" / "observability-levels-feedback.md"
        ).read_text(encoding="utf-8"),
        **{
            path.name: path.read_text(encoding="utf-8")
            for path in (PACKAGE_ROOT / "docs").glob("*handoff.md")
        },
    }
    combined = "\n".join(current_docs.values())
    assert "bundle v4" in combined
    assert "ObservationReader" in combined
    for name, text in current_docs.items():
        normalized = " ".join(text.lower().split())
        assert "current viewer reads bundle v3" not in normalized, name
        assert "advances observation metadata to strict bundle v3" not in normalized, name
        assert "parse `details.jsonl`" not in normalized, name
        assert "consumers parse physical jsonl" not in normalized, name
    assert "they do not parse physical jsonl/value files" in " ".join(
        current_docs["operations.md"].lower().split()
    )


def test_wait_conformance_docs_do_not_overclaim_process_atomicity():
    """The generic kit is in-process; durable adapters need a real-store process gate."""

    required = {
        PACKAGE_ROOT / "README.md": ("process-isolated", "real store"),
        PACKAGE_ROOT / "docs" / "getting-started.md": (
            "separate-process",
            "real store",
        ),
        PACKAGE_ROOT / "docs" / "operations.md": (
            "process-isolated",
            "real store",
        ),
        PACKAGE_ROOT / "docs" / "misuse-risks.md": (
            "separate OS",
            "real Redis/DB",
        ),
        PACKAGE_ROOT / "docs" / "slackazz-handoff.md": (
            "separate OS",
            "real store",
        ),
    }
    for path, phrases in required.items():
        text = " ".join(path.read_text(encoding="utf-8").split())
        for phrase in phrases:
            assert phrase in text, (
                f"{path.name} must state the process-isolated real-store adoption gate; "
                f"missing {phrase!r}"
            )
