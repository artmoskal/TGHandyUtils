"""Config-first observation: the engine owns bundle mechanics from a typed config section."""

import asyncio
import hashlib
import json

import pytest

from ai_workflow_engine import (
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
    with pytest.raises(ValueError, match="plain directory name"):
        asyncio.run(engine.run("escape_flow", {"x": 1}, goal=goal))
    assert not (tmp_path / "escape").exists(), "bundle escaped the configured root"

    for bad in ("/tmp/abs-escape", "..", "", "a/b"):
        with pytest.raises(ValueError, match="plain directory name"):
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
