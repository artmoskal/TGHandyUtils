"""Config-first observation: the engine owns bundle mechanics from a typed config section."""

import asyncio
import hashlib
import json

import pytest

from ai_workflow_engine import (
    LocalWaitPolicy,
    CapabilityResult,
    EvidenceRef,
    ObservationConfig,
    WorkflowArtifact,
    WorkflowBuilder,
    WorkflowEngine,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.config_loader import load_workflow_config
from ai_workflow_engine.observation_bundle import open_observation_run_bundle

pytestmark = pytest.mark.unit


def _noop(context, payload):
    return {"ok": True}


def _evidence_capability(source_path, *, status="accepted"):
    """Capability producing the CLI-agent shape: an EvidenceRef + WorkflowArtifact pair."""

    def _cap(context, payload):
        return CapabilityResult(
            status=status,
            error="boom" if status == "failed" else None,
            output={
                "evidence": EvidenceRef(
                    role="screenshot", uri=str(source_path), media_type="image/png"
                ).model_dump()
            },
            artifacts=[
                WorkflowArtifact(
                    path=str(source_path),
                    kind="media",
                    source="fake_browser",
                    owner_node="solo",
                    metadata={"role": "screenshot", "media_type": "image/png"},
                )
            ],
        )

    return _cap


def _run_with_observation(tmp_path, capability, *, observation=None, flow_id="evidence_flow"):
    from pathlib import Path

    engine = WorkflowEngine(
        observation=observation
        or ObservationConfig(enabled=True, bundle_dir=str(tmp_path / "bundles")),
    )
    engine.register_capability("solo", capability, kind="deterministic")
    engine.register_workflow(WorkflowBuilder(flow_id).step("solo").build())
    result = asyncio.run(engine.run(flow_id, {"x": 1}))
    assert result.observation_bundle_path, "engine did not report the bundle location"
    bundle_path = Path(result.observation_bundle_path)
    manifest = json.loads((bundle_path / "artifacts.json").read_text())
    return result, bundle_path, manifest


def test_loader_parses_the_observation_section(tmp_path):
    config = tmp_path / "app.yaml"
    config.write_text(
        """
workflow:
  workflow_type: demo
observation:
  enabled: true
  bundle_dir: data/observations
  retention_limit: 25
  capture: full
  artifacts: copy
  artifact_max_bytes: 1000000
""",
        encoding="utf-8",
    )
    bundle = load_workflow_config([config])
    assert bundle.observation is not None
    assert bundle.observation.enabled is True
    assert bundle.observation.retention_limit == 25
    assert bundle.observation.capture == "full"
    assert bundle.observation.artifacts == "copy"
    assert bundle.observation.artifact_max_bytes == 1000000
    # observation is a first-class key — no unknown-top-level warning for it
    assert not any("observation" in warning for warning in bundle.warnings)


def _observation_yaml(tmp_path, bundle_dir):
    config = tmp_path / "app.yaml"
    config.write_text(
        f"""
workflow:
  workflow_type: demo
observation:
  enabled: true
  bundle_dir: {bundle_dir}
""",
        encoding="utf-8",
    )
    return config


def _register_and_run(engine, flow_id):
    engine.register_capability("solo", _noop, kind="deterministic")
    engine.register_workflow(WorkflowBuilder(flow_id).step("solo").build())
    return asyncio.run(engine.run(flow_id, {"x": 1}))


def test_builder_and_from_config_observation_parity(tmp_path):
    """Task 1.1: the fluent builder honors the YAML `observation:` block exactly like from_config."""

    from pathlib import Path

    config = _observation_yaml(tmp_path, tmp_path / "bundles")

    via_config = _register_and_run(WorkflowEngine.from_config(config), "cfg_flow")
    via_builder = _register_and_run(
        WorkflowEngineBuilder().with_config(config).build(), "builder_flow"
    )

    assert via_config.observation_bundle_path, "from_config lost the observation block"
    assert via_builder.observation_bundle_path, (
        "fluent builder dropped the YAML observation: block (pre-1.1 defect)"
    )
    meta = json.loads(
        (Path(via_builder.observation_bundle_path) / "meta.json").read_text()
    )
    assert meta["status"] == "completed"


def test_builder_with_observation_none_disables_config_observation(tmp_path):
    """Explicit with_observation(None) opts out even when the config enables observation."""

    bundles = tmp_path / "bundles"
    config = _observation_yaml(tmp_path, bundles)

    result = _register_and_run(
        WorkflowEngineBuilder().with_config(config).with_observation(None).build(),
        "optout_flow",
    )

    assert result.observation_bundle_path is None
    assert not bundles.exists(), "bundle dir written despite explicit with_observation(None)"


def test_builder_with_observation_override_wins_regardless_of_order(tmp_path):
    """The explicit override beats the config's observation whether set before or after with_config."""

    from pathlib import Path

    config = _observation_yaml(tmp_path, tmp_path / "config-bundles")
    override_dir = tmp_path / "override-bundles"
    override = ObservationConfig(enabled=True, bundle_dir=str(override_dir))

    before = _register_and_run(
        WorkflowEngineBuilder().with_observation(override).with_config(config).build(),
        "before_flow",
    )
    after = _register_and_run(
        WorkflowEngineBuilder().with_config(config).with_observation(override).build(),
        "after_flow",
    )

    for result in (before, after):
        assert result.observation_bundle_path, "override observation did not open a bundle"
        assert Path(result.observation_bundle_path).parent == override_dir
    assert not (tmp_path / "config-bundles").exists(), (
        "config observation dir used despite explicit override"
    )


def test_enabled_observation_auto_opens_routes_and_finalizes(tmp_path):
    engine = WorkflowEngine(
        observation=ObservationConfig(enabled=True, bundle_dir=str(tmp_path), retention_limit=5),
    )
    engine.register_capability("solo", _noop, kind="deterministic")
    engine.register_workflow(WorkflowBuilder("auto_flow").step("solo").build())

    result = asyncio.run(engine.run("auto_flow", {"x": 1}))

    assert result.observation_bundle_path, "engine did not report the bundle location"
    bundle_dir = tmp_path / result.observation_bundle_path.split("/")[-1]
    meta = json.loads((bundle_dir / "meta.json").read_text())
    assert meta["status"] == "completed"
    trace_lines = (bundle_dir / "trace.jsonl").read_text().strip().splitlines()
    assert trace_lines, "trace was not routed into the auto-opened bundle"
    assert (bundle_dir / "definition.json").exists()


def test_disabled_observation_opens_nothing(tmp_path):
    engine = WorkflowEngine(
        observation=ObservationConfig(enabled=False, bundle_dir=str(tmp_path)),
    )
    engine.register_capability("solo", _noop, kind="deterministic")
    engine.register_workflow(WorkflowBuilder("quiet_flow").step("solo").build())

    result = asyncio.run(engine.run("quiet_flow", {"x": 1}))

    assert result.observation_bundle_path is None
    assert not any(tmp_path.iterdir()), "bundle dir written despite observation disabled"


def test_malicious_run_id_cannot_escape_the_bundle_root(tmp_path):
    """A caller-supplied run id is a directory NAME — traversal/absolute paths fail loudly."""

    from ai_workflow_engine import WorkflowGoal

    engine = WorkflowEngine(
        observation=ObservationConfig(enabled=True, bundle_dir=str(tmp_path / "root")),
    )
    engine.register_capability("solo", _noop, kind="deterministic")
    engine.register_workflow(WorkflowBuilder("escape_flow").step("solo").build())

    goal = WorkflowGoal(
        workflow_type="escape_flow",
        objective="escape attempt",
        metadata={"run_id": "../escape"},
    )
    with pytest.raises(ValueError, match="plain name"):
        asyncio.run(engine.run("escape_flow", {"x": 1}, goal=goal))
    assert not (tmp_path / "escape").exists(), "bundle escaped the configured root"

    for bad in ("/tmp/abs-escape", "..", "", "a/b"):
        with pytest.raises(ValueError, match="plain name"):
            open_observation_run_bundle(tmp_path / "root", bad)


def test_explicit_bundle_escape_hatch_beats_the_config(tmp_path):
    engine = WorkflowEngine(
        observation=ObservationConfig(enabled=True, bundle_dir=str(tmp_path / "auto")),
    )
    engine.register_capability("solo", _noop, kind="deterministic")
    engine.register_workflow(WorkflowBuilder("hatch_flow").step("solo").build())
    explicit = open_observation_run_bundle(tmp_path / "explicit", "run-x")

    result = asyncio.run(engine.run("hatch_flow", {"x": 1}, observation_bundle=explicit))

    assert result.observation_bundle_path == str(explicit.path)
    assert not (tmp_path / "auto").exists(), "auto bundle opened despite explicit escape hatch"
    meta = json.loads((tmp_path / "explicit" / "run-x" / "meta.json").read_text())
    assert meta["status"] == "completed"


def test_explicit_bundle_wrong_types_fail_at_the_door_with_zero_calls(tmp_path):
    """C2-4 (F-2G-1, codex Iteration-2 review): the escape hatch is public API — a string or
    a partial duck object is a TypeError AT ``engine.run``, before compilation or any
    capability call, and the message names both supported routes."""

    calls: list[int] = []

    def probe(payload, context):
        calls.append(1)
        return {"ok": True}

    engine = WorkflowEngine()
    engine.register_capability("probe", probe, kind="deterministic")
    engine.register_workflow(WorkflowBuilder("door_flow").step("probe").build())

    with pytest.raises(TypeError, match="ObservationRunBundle"):
        asyncio.run(
            engine.run("door_flow", {"x": 1}, observation_bundle=str(tmp_path / "str-bundle"))
        )

    class Impostor:
        trace_sink = None

        def finalize(self, *args, **kwargs):
            return None

    with pytest.raises(TypeError, match="open_observation_run_bundle"):
        asyncio.run(engine.run("door_flow", {"x": 1}, observation_bundle=Impostor()))

    assert calls == [], "wrong bundle types must be rejected before ANY capability executes"
    assert not (tmp_path / "str-bundle").exists(), "the string must never become a directory"


# --- G1: evidence resolution — artifacts are archived INTO the bundle -----------------


def test_run_artifacts_are_archived_and_resolvable(tmp_path):
    """The resolution contract: EvidenceRef.uri → manifest source_path → bundle_path."""

    source = tmp_path / "shot.png"
    source.write_bytes(b"png-bytes-evidence")

    result, bundle_path, manifest = _run_with_observation(
        tmp_path, _evidence_capability(source)
    )

    assert result.status == "completed"
    assert len(manifest) == 1
    entry = manifest[0]
    assert entry["copied"] is True and entry["skip_reason"] is None
    assert entry["source_path"] == str(source)
    assert entry["media_type"] == "image/png" and entry["role"] == "screenshot"
    archived = bundle_path / entry["bundle_path"]
    assert archived.read_bytes() == b"png-bytes-evidence"
    assert entry["sha256"] == hashlib.sha256(b"png-bytes-evidence").hexdigest()
    # the EvidenceRef in the run output joins the manifest on source_path
    evidence_uri = result.output["evidence"]["uri"]
    assert evidence_uri == entry["source_path"]
    meta = json.loads((bundle_path / "meta.json").read_text())
    assert meta["artifact_root"] == "artifacts"
    assert meta["artifact_manifest_path"] == "artifacts.json"
    assert meta["artifact_count"] == 1 and meta["artifacts_copied"] == 1


def test_failed_run_still_archives_failure_evidence(tmp_path):
    """Failure evidence is the evidence that matters most — archived before any cleanup."""

    source = tmp_path / "failure-shot.png"
    source.write_bytes(b"broken-page")

    result, bundle_path, manifest = _run_with_observation(
        tmp_path, _evidence_capability(source, status="failed")
    )

    assert result.status == "failed"
    meta = json.loads((bundle_path / "meta.json").read_text())
    assert meta["status"] == "failed"
    assert manifest[0]["copied"] is True
    assert (bundle_path / manifest[0]["bundle_path"]).read_bytes() == b"broken-page"


def test_oversized_artifact_is_skipped_honestly(tmp_path):
    source = tmp_path / "huge.bin"
    source.write_bytes(b"x" * 100)

    _, bundle_path, manifest = _run_with_observation(
        tmp_path,
        _evidence_capability(source),
        observation=ObservationConfig(
            enabled=True, bundle_dir=str(tmp_path / "bundles"), artifact_max_bytes=10
        ),
    )

    entry = manifest[0]
    assert entry["copied"] is False
    assert entry["skip_reason"] == "exceeds_artifact_max_bytes"
    assert entry["size_bytes"] == 100 and entry["bundle_path"] is None
    assert not (bundle_path / "artifacts").exists(), "oversized artifact was copied anyway"
    meta = json.loads((bundle_path / "meta.json").read_text())
    assert meta["artifact_count"] == 1 and meta["artifacts_copied"] == 0


def test_missing_artifact_source_is_diagnosable(tmp_path):
    _, _, manifest = _run_with_observation(
        tmp_path, _evidence_capability(tmp_path / "never-written.png")
    )

    assert manifest[0]["copied"] is False
    assert manifest[0]["skip_reason"] == "source_missing"


def test_artifact_policy_off_keeps_manifest_without_bytes(tmp_path):
    source = tmp_path / "shot.png"
    source.write_bytes(b"png-bytes")

    _, bundle_path, manifest = _run_with_observation(
        tmp_path,
        _evidence_capability(source),
        observation=ObservationConfig(
            enabled=True, bundle_dir=str(tmp_path / "bundles"), artifacts="off"
        ),
    )

    entry = manifest[0]
    assert entry["copied"] is False and entry["skip_reason"] == "artifact_policy_off"
    assert entry["source_path"] == str(source), "policy off must still record where evidence was"
    assert not (bundle_path / "artifacts").exists()


def test_duplicate_artifact_paths_copied_once(tmp_path):
    source = tmp_path / "shared.png"
    source.write_bytes(b"shared-bytes")

    def _two_artifacts(context, payload):
        artifact = WorkflowArtifact(path=str(source), kind="media")
        return CapabilityResult(
            output={"ok": True},
            artifacts=[artifact, WorkflowArtifact(path=str(source), kind="media")],
        )

    _, bundle_path, manifest = _run_with_observation(tmp_path, _two_artifacts)

    assert len(manifest) == 2
    assert all(entry["copied"] for entry in manifest)
    assert manifest[0]["bundle_path"] == manifest[1]["bundle_path"]
    assert len(list((bundle_path / "artifacts").iterdir())) == 1


def test_retention_prunes_evidence_with_the_bundle(tmp_path):
    """The user's cleanup policy: ONE knob — pruned bundles take their artifacts with them."""

    source = tmp_path / "shot.png"
    source.write_bytes(b"png-bytes")
    engine = WorkflowEngine(
        observation=ObservationConfig(
            enabled=True, bundle_dir=str(tmp_path / "bundles"), retention_limit=1
        ),
    )
    engine.register_capability("solo", _evidence_capability(source), kind="deterministic")
    engine.register_workflow(WorkflowBuilder("prune_flow").step("solo").build())

    first = asyncio.run(engine.run("prune_flow", {"x": 1}))
    second = asyncio.run(engine.run("prune_flow", {"x": 2}))

    from pathlib import Path

    assert not Path(first.observation_bundle_path).exists(), (
        "pruned bundle left evidence behind"
    )
    survivor = Path(second.observation_bundle_path)
    manifest = json.loads((survivor / "artifacts.json").read_text())
    assert (survivor / manifest[0]["bundle_path"]).exists()


async def test_resumed_run_opens_its_own_bundle_and_finalizes_completed(tmp_path):
    """Q-R5: config-first observation covers the RESUMED half like any run — its own
    bundle, machine:resumed in its trace, finalized completed. Suspend->resume is
    inspectable end to end, not an in-memory claim."""

    import json

    from pathlib import Path

    from pydantic import BaseModel

    from ai_workflow_engine import ObservationConfig, WorkflowBuilder, WorkflowEngineBuilder

    class Gate(BaseModel):
        status: str
        value: str = ""

    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(enabled=True, bundle_dir=str(tmp_path))
    )

    def ask(context, _payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending")
        return Gate(status="answered", value=str(event))

    builder.register_capability("ask", ask, kind="deterministic")
    builder.register_capability("finish", lambda ctx, p: {"done": True}, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("resume_bundle_flow").human("ask", wait_policy=LocalWaitPolicy()).step("finish").build()
    )
    engine = builder.build()

    first = await engine.run("resume_bundle_flow", {"q": "?"})
    assert first.status == "requires_user_input"
    assert first.observation_bundle_path, "suspension half must have a bundle"

    resumed = await engine.resume(first.snapshot, "yes")
    assert resumed.status == "completed"
    assert resumed.observation_bundle_path, "resumed half must open its OWN bundle"
    assert resumed.observation_bundle_path != first.observation_bundle_path

    resumed_dir = Path(resumed.observation_bundle_path)
    meta = json.loads((resumed_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("status") == "completed"
    assert "machine:resumed" in (resumed_dir / "trace.jsonl").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# W4 — segment identity, logical run ids, group retention
# ---------------------------------------------------------------------------


def _meta(path):
    import json as _json
    from pathlib import Path as _P

    return _json.loads((_P(path) / "meta.json").read_text(encoding="utf-8"))


def _suspend_resume_engine(bundle_dir, *, retention_limit=None):
    """Local-wait engine with config-first observation for segment tests."""

    from ai_workflow_engine import ObservationConfig, WorkflowEngineBuilder

    from pydantic import BaseModel as _BM

    builder = WorkflowEngineBuilder().with_observation(
        ObservationConfig(
            enabled=True, bundle_dir=str(bundle_dir), retention_limit=retention_limit
        )
    )

    class Gate(_BM):
        status: str
        value: str = ""

    def ask(context, _payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending")
        return Gate(status="answered", value=str(event))

    builder.register_capability("ask", ask, kind="deterministic")
    builder.register_capability("finish", lambda ctx, p: {"done": True}, kind="deterministic")
    builder.register_workflow(
        WorkflowBuilder("seg_flow").human("ask", wait_policy=LocalWaitPolicy()).step("finish").build()
    )
    builder.register_workflow(WorkflowBuilder("plain_flow").step("finish").build())
    return builder.build()


async def test_resumed_bundle_is_a_segment_of_the_logical_run(tmp_path):
    """W4.1: the resumed half's meta carries the LOGICAL run id (viewer queries by run id
    find it — the Q-R5 empty-viewer defect), a persisted segment index/kind, and the exact
    parent segment id; the initial bundle keeps its pre-W4 directory key."""

    from pathlib import Path

    from ai_workflow_engine.models import WorkflowGoal

    engine = _suspend_resume_engine(tmp_path)
    goal = WorkflowGoal(workflow_type="seg_flow", objective="seg", metadata={"run_id": "logical-1"})
    first = await engine.run("seg_flow", {"q": "?"}, goal=goal)
    assert first.status == "requires_user_input"
    initial_dir = Path(first.observation_bundle_path)
    assert initial_dir.name == "logical-1", "initial segment keeps the pre-W4 dir key"
    initial_meta = _meta(initial_dir)
    assert initial_meta["run_id"] == "logical-1"
    assert initial_meta["segment_index"] == 0
    assert initial_meta["segment_kind"] == "initial"
    assert initial_meta["usage_totals_scope"] == "run_cumulative_at_finalize"
    assert first.snapshot.segment_index == 0, (
        "the snapshot persists the LOGICAL position the continuation derives from — and "
        "deliberately NO physical directory key (R1: physical identity varies per attempt)"
    )
    assert not hasattr(first.snapshot, "segment_id"), (
        "machine snapshots must not carry a physical storage key"
    )

    resumed = await engine.resume(first.snapshot, "yes")
    assert resumed.status == "completed"
    resumed_dir = Path(resumed.observation_bundle_path)
    assert resumed_dir.name.startswith("logical-1--s001-"), resumed_dir.name
    resumed_meta = _meta(resumed_dir)
    assert resumed_meta["run_id"] == "logical-1", (
        "resumed segment meta must carry the LOGICAL id, not the physical key"
    )
    assert resumed_meta["segment_index"] == 1
    assert resumed_meta["segment_kind"] == "resume"
    assert resumed_meta["attempt"] is None, "local resumes carry no claim ordinal"
    assert resumed_meta["definition_digest"] == initial_meta["definition_digest"]


async def test_group_retention_prunes_logical_runs_as_units(tmp_path):
    """W4.5 (Q-R5 reproducer): retention must never delete a suspension half while keeping
    its continuation — the logical run is the unit. With limit 1, the OLD run's two
    segments disappear together and the newest run's segments all survive."""

    from ai_workflow_engine.models import WorkflowGoal

    bundles = tmp_path / "bundles"
    engine = _suspend_resume_engine(bundles, retention_limit=1)

    old_goal = WorkflowGoal(workflow_type="seg_flow", objective="old", metadata={"run_id": "old-run"})
    old_first = await engine.run("seg_flow", {}, goal=old_goal)
    await engine.resume(old_first.snapshot, "done")  # old-run group terminal (2 segments)

    new_goal = WorkflowGoal(workflow_type="seg_flow", objective="new", metadata={"run_id": "new-run"})
    new_first = await engine.run("seg_flow", {}, goal=new_goal)
    resumed = await engine.resume(new_first.snapshot, "done")
    assert resumed.status == "completed"

    names = {path.name for path in bundles.iterdir() if (path / "meta.json").exists()}
    assert not any(name.startswith("old-run") for name in names), (
        f"the old logical run must be pruned as a UNIT: {names}"
    )
    assert "new-run" in names and any(name.startswith("new-run--s001-") for name in names), (
        f"retention limit 1 must preserve EVERY segment of the newest run: {names}"
    )


async def test_in_flight_suspended_group_is_never_pruned(tmp_path):
    """W4.5: a run awaiting resume (newest segment requires_user_input) is in-flight —
    newer completed runs must not push its history out of retention."""

    from ai_workflow_engine.models import WorkflowGoal

    bundles = tmp_path / "bundles"
    engine = _suspend_resume_engine(bundles, retention_limit=1)

    waiting_goal = WorkflowGoal(
        workflow_type="seg_flow", objective="waiting", metadata={"run_id": "waiting-run"}
    )
    waiting = await engine.run("seg_flow", {}, goal=waiting_goal)
    assert waiting.status == "requires_user_input"

    for index in range(2):  # two newer terminal runs, limit 1 → old finalized would rotate
        goal = WorkflowGoal(
            workflow_type="plain_flow", objective="n", metadata={"run_id": f"done-{index}"}
        )
        result = await engine.run("plain_flow", {}, goal=goal)
        assert result.status == "completed"

    names = {path.name for path in bundles.iterdir() if (path / "meta.json").exists()}
    assert "waiting-run" in names, f"in-flight suspended group was pruned: {names}"
    assert "done-1" in names and "done-0" not in names, (
        f"terminal groups still rotate normally around the protected one: {names}"
    )

    # ...and the protection ends with the lifecycle: once resumed to terminal, it rotates.
    resumed = await engine.resume(waiting.snapshot, "go")
    assert resumed.status == "completed"
    goal = WorkflowGoal(workflow_type="plain_flow", objective="n", metadata={"run_id": "done-2"})
    await engine.run("plain_flow", {}, goal=goal)
    names = {path.name for path in bundles.iterdir() if (path / "meta.json").exists()}
    assert not any(name.startswith("waiting-run") for name in names), (
        f"terminalized group must prune as a unit again: {names}"
    )


def test_malformed_segment_identity_is_loud(tmp_path):
    """W4.1: segment identity rules fail loud — no silent half-lineage on disk."""

    import pytest as _pytest

    from ai_workflow_engine import ObservationSegment, open_observation_run_bundle

    with _pytest.raises(ValueError, match="segment_index=0"):
        ObservationSegment(segment_id="x", segment_index=1, kind="initial")
    with _pytest.raises(ValueError, match="no attempt"):
        ObservationSegment(segment_id="x", segment_index=0, kind="initial", attempt=1)
    with _pytest.raises(ValueError, match="segment_index >= 1"):
        ObservationSegment(segment_id="x", segment_index=0, kind="resume")
    with _pytest.raises(ValueError, match="initial|resume"):
        ObservationSegment(segment_id="x", segment_index=0, kind="weird")
    with _pytest.raises(ValueError, match="plain name"):
        ObservationSegment(segment_id="  ", segment_index=0, kind="initial")
    with _pytest.raises(ValueError, match="plain name"):
        open_observation_run_bundle(
            tmp_path,
            "logical",
            segment=ObservationSegment(segment_id="../escape", segment_index=0, kind="initial"),
        )


async def test_suspended_eviction_cap_is_opt_in_and_age_based(tmp_path):
    """R4-B (user-settled policy): by default a suspended group is NEVER evicted; with
    `evict_suspended_after_s` set, groups suspended longer than the cap rotate out at the
    normal finalize-time sweep — viewer history only. This LOCAL wait resumes afterwards
    because the CALLER kept its snapshot (durable waits would resume via the coordinator's
    stored snapshot); the engine never stored a local snapshot anywhere."""

    import json
    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.models import WorkflowGoal

    bundles = tmp_path / "bundles"
    engine = _suspend_resume_engine(bundles, retention_limit=1)
    # opt in: anything suspended for more than an hour is evictable
    engine.observation = engine.observation.model_copy(update={"evict_suspended_after_s": 3600.0})

    goal = WorkflowGoal(workflow_type="seg_flow", objective="old", metadata={"run_id": "aged-run"})
    aged = await engine.run("seg_flow", {}, goal=goal)
    assert aged.status == "requires_user_input"
    # age the suspension two hours into the past (meta timestamp is the sweep's clock)
    meta_path = bundles / "aged-run" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["timestamp"] = (
        (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    fresh_goal = WorkflowGoal(workflow_type="seg_flow", objective="f", metadata={"run_id": "fresh-run"})
    fresh = await engine.run("seg_flow", {}, goal=fresh_goal)  # suspended NOW (inside cap)
    for index in (0, 1):
        goal_n = WorkflowGoal(
            workflow_type="plain_flow", objective="n", metadata={"run_id": f"done-{index}"}
        )
        await engine.run("plain_flow", {}, goal=goal_n)  # terminal groups -> sweep fires

    names = {p.name for p in bundles.iterdir() if (p / "meta.json").exists()}
    assert "aged-run" not in names, f"over-cap suspended group must rotate out: {names}"
    assert "fresh-run" in names, "a suspended group INSIDE the cap stays protected"
    # resumability is untouched: the aged run's snapshot still resumes fine
    resumed = await engine.resume(aged.snapshot, "late answer")
    assert resumed.status == "completed", (
        "eviction sacrifices viewer history only — the caller-retained snapshot resumes"
    )


def test_eviction_cap_rejects_non_positive_and_non_finite_values(tmp_path):
    """W5-C2: an invalid retention policy refuses loudly at construction — NaN would
    otherwise make `age <= cap` silently false and evict nothing (or worse, mislead the
    operator into thinking a bound exists). Same discipline as durable timeout_s."""

    import math

    import pytest as _pytest

    from ai_workflow_engine import ObservationConfig

    for bad in (-1.0, 0.0, float("inf"), float("nan")):
        with _pytest.raises(Exception) as err:
            ObservationConfig(enabled=True, bundle_dir=str(tmp_path), evict_suspended_after_s=bad)
        assert "evict_suspended_after_s" in str(err.value) or "greater than 0" in str(err.value), (
            f"cap {bad!r} must be rejected loudly, got: {err.value}"
        )
    ok = ObservationConfig(enabled=True, bundle_dir=str(tmp_path), evict_suspended_after_s=3600.0)
    assert math.isfinite(ok.evict_suspended_after_s)
    assert ObservationConfig(enabled=True, bundle_dir=str(tmp_path)).evict_suspended_after_s is None


async def test_correlation_id_spans_runs_events_bundles_and_writes(tmp_path):
    """W5-C4 (user-approved contract): an optional caller-supplied `correlation_id`
    is projected AUTOMATICALLY into run context, every engine-written trace/usage event's
    metadata, and every segment's bundle meta — across suspend/resume and across SEPARATE
    runs sharing one case — while run_id stays the storage identity. Products stamp their
    ExternalWriteRequest.metadata from context (shown here); absent means absent."""

    import json
    from pathlib import Path

    from ai_workflow_engine.models import ExternalWriteRequest, WorkflowGoal

    engine = _suspend_resume_engine(tmp_path)
    captured_writes = []

    def deliver_result(context, payload):
        # the documented product-side pattern for external-write correlation
        captured_writes.append(
            ExternalWriteRequest(
                target="crm",
                idempotency_key=f"case-{context.run_context.correlation_id}",
                metadata={"correlation_id": context.run_context.correlation_id},
            )
        )
        return {"done": True}

    engine.register_capability("deliver_result", deliver_result, kind="deterministic")
    engine.register_workflow(
        WorkflowBuilder("case_flow").step("deliver_result").build()
    )

    goal = WorkflowGoal(
        workflow_type="seg_flow", objective="case", metadata={"run_id": "case-run-1"},
        correlation_id="case-7",
    )
    first = await engine.run("seg_flow", {}, goal=goal)
    assert first.status == "requires_user_input"
    assert first.snapshot.goal.correlation_id == "case-7", "correlation survives snapshots"
    resumed = await engine.resume(first.snapshot, "yes")
    assert resumed.status == "completed"

    # a SECOND logical run of the same case: distinct run_id, same correlation
    goal2 = WorkflowGoal(
        workflow_type="case_flow", objective="case", metadata={"run_id": "case-run-2"},
        correlation_id="case-7",
    )
    second = await engine.run("case_flow", {}, goal=goal2)
    assert second.status == "completed"
    assert captured_writes and captured_writes[0].metadata["correlation_id"] == "case-7"

    # every finalized segment of BOTH runs carries the correlation in meta AND in events
    dirs = [p for p in tmp_path.iterdir() if (p / "meta.json").exists()]
    assert len(dirs) >= 3
    for d in dirs:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        assert meta["correlation_id"] == "case-7", d.name
        for line in (d / "trace.jsonl").read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            assert event["metadata"].get("correlation_id") == "case-7", (d.name, event["node"])

    # engine-side truth: the run result's trace buffer is stamped too
    assert all(
        e.metadata.get("correlation_id") == "case-7" for e in resumed.trace
    ), "every engine-written trace event carries the correlation automatically"

    # optionality: no correlation -> no phantom key
    goal3 = WorkflowGoal(
        workflow_type="case_flow", objective="n", metadata={"run_id": "plain-run"}
    )
    await engine.run("case_flow", {}, goal=goal3)
    plain_meta = json.loads((tmp_path / "plain-run" / "meta.json").read_text(encoding="utf-8"))
    assert "correlation_id" not in plain_meta


async def test_usage_correlation_is_identical_across_all_five_surfaces(tmp_path):
    """W5R.2: usage enrichment happens ONCE, before aggregation — the SAME event (same
    event_id, same correlation) reaches the returned result ledger, the snapshot-restored
    summary, the observation JSONL, and the configured sink; a conflicting caller-stamped
    correlation refuses loudly before persistence; uncorrelated events carry no key."""

    import json

    from ai_workflow_engine.models import WorkflowGoal, WorkflowUsageEvent
    from ai_workflow_engine.usage_events import record_usage_event

    captured_sink: list = []

    class SinkSpy:
        def record(self, event):
            captured_sink.append(event)

    engine = _suspend_resume_engine(tmp_path)
    engine.executor.runner.usage_sink.inner = SinkSpy()

    def spend(context, payload):
        record_usage_event(
            WorkflowUsageEvent(node="spend", operation="chat", total_tokens=5, estimated_usd=0.01)
        )
        return payload

    engine.register_capability("spend", spend, kind="deterministic")
    engine.register_workflow(
        WorkflowBuilder("spend_flow")
        .step("spend")
        .human("ask", wait_policy=LocalWaitPolicy())
        .step("finish")
        .build()
    )
    goal = WorkflowGoal(
        workflow_type="spend_flow", objective="s", metadata={"run_id": "five-run"},
        correlation_id="case-5",
    )
    first = await engine.run("spend_flow", {}, goal=goal)
    assert first.status == "requires_user_input"

    # surface 1: returned result ledger
    [event] = first.usage.events
    assert event.metadata["correlation_id"] == "case-5"
    # surface 2: snapshot-persisted summary (what a resume restores)
    snap_events = first.snapshot.usage["events"]
    assert snap_events[0]["metadata"]["correlation_id"] == "case-5"
    # surface 3: observation JSONL (identical event id + correlation)
    usage_line = json.loads(
        (tmp_path / "five-run" / "usage.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert usage_line["event_id"] == event.event_id
    assert usage_line["metadata"]["correlation_id"] == "case-5"
    # surface 4: configured sink got the SAME enriched object
    assert captured_sink[0].event_id == event.event_id
    assert captured_sink[0].metadata["correlation_id"] == "case-5"
    # surface 5: resumed half restores the correlated summary and keeps stamping
    resumed = await engine.resume(first.snapshot, "yes")
    assert all(
        e.metadata.get("correlation_id") == "case-5" for e in resumed.usage.events
    ), "the restored ledger and any post-resume events stay identically correlated"

    # conflict refuses loudly BEFORE persistence
    from ai_workflow_engine.budget import WorkflowBudget, WorkflowUsageContext, workflow_usage_scope
    from ai_workflow_engine.models import WorkflowRunContext, WorkflowUsageSummary

    conflicted = WorkflowUsageContext(
        run_context=WorkflowRunContext(
            workflow_id="r-x", workflow_type="t", correlation_id="case-5"
        ),
        summary=WorkflowUsageSummary(),
        budget=WorkflowBudget(),
    )
    with workflow_usage_scope(conflicted):
        import pytest as _pytest

        with _pytest.raises(ValueError, match="conflicting related-run identity"):
            record_usage_event(
                WorkflowUsageEvent(
                    node="n", operation="chat", total_tokens=1,
                    metadata={"correlation_id": "case-OTHER"},
                )
            )
    assert conflicted.summary.events == [], "nothing persists past a refused conflict"

    # absent stays absent
    goal2 = WorkflowGoal(
        workflow_type="spend_flow", objective="p", metadata={"run_id": "plain-five"}
    )
    plain = await engine.run("spend_flow", {}, goal=goal2)
    assert "correlation_id" not in plain.usage.events[0].metadata


def test_blank_correlation_id_is_rejected():
    """W5R.1: absent means absent — a blank/whitespace related-run id would silently
    correlate unrelated runs under an empty label."""

    import pytest as _pytest

    from ai_workflow_engine.models import WorkflowGoal

    for blank in ("", "   "):
        with _pytest.raises(Exception, match="non-blank"):
            WorkflowGoal(workflow_type="t", objective="o", correlation_id=blank)
    assert WorkflowGoal(workflow_type="t", objective="o").correlation_id is None


async def test_conflicting_related_run_identity_is_loud_on_every_event_surface(tmp_path):
    """W5RR.1/.2: ONE enrichment rule — trace, detail, and usage all REFUSE a conflicting
    related-run id before any persistence (buffer, bundle, sink, summary), with no string
    coercion (int 42 never matches "42"); persisted wait schemas refuse blank ids."""

    import pytest as _pytest

    from ai_workflow_engine.models import (
        ObservationDetail,
        WorkflowRunContext,
        WorkflowTraceEvent,
        WorkflowUsageEvent,
        WorkflowUsageSummary,
    )
    from ai_workflow_engine.run_session import (
        SessionScopedDetailSink,
        SessionScopedTraceSink,
        WorkflowRunSession,
    )
    from ai_workflow_engine._runtime_state import run_session_scope
    from ai_workflow_engine.models import WorkflowGoal

    from types import SimpleNamespace

    goal = WorkflowGoal(workflow_type="t", objective="o", correlation_id="case-right")
    context = SimpleNamespace(
        goal=goal,
        run_context=WorkflowRunContext(
            workflow_id="r-1", workflow_type="t", correlation_id="case-right"
        ),
    )
    session = WorkflowRunSession(workflow_id="t", context=context)

    class Collect:
        def __init__(self):
            self.events = []

        def record(self, e):
            self.events.append(e)

    inner = Collect()
    trace_sink = SessionScopedTraceSink(inner)
    with run_session_scope(session):
        # conflicting trace event: refused BEFORE buffer/bundle/inner persistence
        with _pytest.raises(ValueError, match="conflicting related-run identity"):
            trace_sink.record(
                WorkflowTraceEvent(node="n", metadata={"correlation_id": "case-wrong"})
            )
        assert session.trace_events == [] and inner.events == [], "no partial persistence"
        # matching + absent both fine, and stamped identically
        trace_sink.record(WorkflowTraceEvent(node="n"))
        assert session.trace_events[0].metadata["correlation_id"] == "case-right"

        detail_sink = SessionScopedDetailSink(Collect())
        with _pytest.raises(ValueError, match="conflicting related-run identity"):
            detail_sink.record(
                ObservationDetail(
                    detail_id="d1", event_id="e1", kind="tool_result", content_type="text/plain",
                    digest="x", metadata={"correlation_id": "case-wrong"},
                )
            )

    # usage: type-strict — int 42 never matches authoritative "42"
    from ai_workflow_engine.budget import WorkflowBudget, WorkflowUsageContext, workflow_usage_scope
    from ai_workflow_engine.usage_events import record_usage_event

    typed = WorkflowUsageContext(
        run_context=WorkflowRunContext(workflow_id="r-2", workflow_type="t", correlation_id="42"),
        summary=WorkflowUsageSummary(),
        budget=WorkflowBudget(),
    )
    with workflow_usage_scope(typed):
        with _pytest.raises(ValueError, match="conflicting related-run identity"):
            record_usage_event(
                WorkflowUsageEvent(
                    node="n", operation="chat", total_tokens=1, metadata={"correlation_id": 42}
                )
            )
    assert typed.summary.events == []

    # an UNCORRELATED run must not accept events that INVENT an identity (truth table)
    invented = WorkflowUsageContext(
        run_context=WorkflowRunContext(workflow_id="r-3", workflow_type="t"),  # no correlation
        summary=WorkflowUsageSummary(),
        budget=WorkflowBudget(),
    )
    with workflow_usage_scope(invented):
        with _pytest.raises(ValueError, match="UNCORRELATED"):
            record_usage_event(
                WorkflowUsageEvent(
                    node="n", operation="chat", total_tokens=1,
                    metadata={"correlation_id": "case-invented"},
                )
            )
    assert invented.summary.events == []
    plain_context = SimpleNamespace(
        goal=WorkflowGoal(workflow_type="t", objective="o"),
        run_context=WorkflowRunContext(workflow_id="r-4", workflow_type="t"),
    )
    plain_session = WorkflowRunSession(workflow_id="t", context=plain_context)
    with run_session_scope(plain_session):
        with _pytest.raises(ValueError, match="UNCORRELATED"):
            SessionScopedTraceSink(Collect()).record(
                WorkflowTraceEvent(node="n", metadata={"correlation_id": "case-invented"})
            )
    assert plain_session.trace_events == []

    # the EXPORTED bundle boundary enforces the same blank rule
    from ai_workflow_engine import open_observation_run_bundle

    with _pytest.raises(Exception, match="non-blank"):
        open_observation_run_bundle(tmp_path, "b-run", correlation_id="   ")

    # persisted wait schemas share the non-blank rule
    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine.waits import DurableWaitPolicy, WaitRecord
    from ai_workflow_engine.wait_runtime import WaitRegistrationRequest

    now = datetime(2036, 1, 1, tzinfo=timezone.utc)
    for blank in ("", "   "):
        with _pytest.raises(Exception, match="non-blank"):
            WaitRecord(
                record_schema_version="wait-v2",
                wait_id="w", run_id="r", workflow_id="wf", suspended_node="g",
                policy=DurableWaitPolicy(timeout_s=1), definition_digest="d",
                registration_attempt_id="attempt-w",
                created_at=now, deadline_at=now + timedelta(seconds=1),
                correlation_id=blank,
            )
        with _pytest.raises(Exception, match="non-blank"):
            WaitRegistrationRequest(
                run_id="r", workflow_id="wf", definition_digest="d", suspended_node="g",
                occurrence=0, policy=DurableWaitPolicy(timeout_s=1), snapshot_json="{}",
                definition_json="{}", correlation_id=blank,
            )


def test_observation_config_rejects_unknown_keys_loudly():
    """v0.11 clean contract (manifest row M8): engine-owned config schemas are CLOSED — a
    misspelled retention knob (or an obsolete key from any earlier line) fails instead of being
    silently ignored and quietly changing retention behavior."""

    import pytest as _pytest
    from pydantic import ValidationError as _VE

    from ai_workflow_engine import ObservationConfig

    with _pytest.raises(_VE):
        ObservationConfig(enabled=True, bundle_dir="x", retention_limt=5)  # typo'd key
    with _pytest.raises(_VE):
        ObservationConfig.model_validate({"enabled": True, "keep_bundles": 3})  # obsolete shape
