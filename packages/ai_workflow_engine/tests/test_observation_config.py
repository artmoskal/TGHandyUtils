"""Config-first observation: the engine owns bundle mechanics from a typed config section."""

import asyncio
import json

import pytest

from ai_workflow_engine import (
    ObservationConfig,
    WorkflowBuilder,
    WorkflowEngine,
    WorkflowEngineBuilder,
)
from ai_workflow_engine.config_loader import load_workflow_config
from ai_workflow_engine.observation_bundle import open_observation_run_bundle

pytestmark = pytest.mark.unit


def _noop(context, payload):
    return {"ok": True}


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
""",
        encoding="utf-8",
    )
    bundle = load_workflow_config([config])
    assert bundle.observation is not None
    assert bundle.observation.enabled is True
    assert bundle.observation.retention_limit == 25
    assert bundle.observation.capture == "full"
    # observation is a first-class key — no unknown-top-level warning for it
    assert not any("observation" in warning for warning in bundle.warnings)


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
