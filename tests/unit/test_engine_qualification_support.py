"""Q0.2: qualification runtime-identity manifest — deterministic, sensitive-value-free."""

import json

import pytest

from tests.support.engine_qualification import (
    QualificationRunIdentity,
    capture_run_identity,
)

pytestmark = pytest.mark.unit


def _identity(**overrides):
    readers = dict(
        cli_version_reader=lambda: "2.1.201 (Claude Code)",
        git_commit_reader=lambda: "f8c9e7b",
        git_dirty_reader=lambda: False,
        platform_reader=lambda: "Linux-aarch64",
        clock=lambda: "2026-07-11T02:00:00+00:00",
    )
    readers.update(overrides)
    return capture_run_identity("sonnet", **readers)


def test_manifest_captures_exactly_the_six_identity_facts_deterministically():
    identity = _identity()
    payload = identity.model_dump()
    assert payload == {
        "requested_model": "sonnet",
        "cli_version": "2.1.201 (Claude Code)",
        "git_commit": "f8c9e7b",
        "git_dirty": False,
        "platform": "Linux-aarch64",
        "started_at": "2026-07-11T02:00:00+00:00",
        "finished_at": None,
    }
    # schema is CLOSED: nothing else can ride along into the manifest
    with pytest.raises(Exception):
        QualificationRunIdentity(**{**payload, "environment": "dump"})


def test_manifest_rejects_sensitive_values_loudly():
    for leaky in ("sk-ant-abc123", "Bearer OAUTH thing", "/Users/artemm/.claude"):
        with pytest.raises(ValueError):
            _identity(cli_version_reader=lambda leaky=leaky: leaky)


def test_manifest_requires_a_model_alias():
    with pytest.raises(ValueError, match="requested_model is required"):
        capture_run_identity("", cli_version_reader=lambda: "x",
                             git_commit_reader=lambda: "y",
                             git_dirty_reader=lambda: False,
                             platform_reader=lambda: "z",
                             clock=lambda: "t")


def test_manifest_records_a_dirty_tree_honestly():
    """Q-R3: the identity carries the dirty flag — release runs gate on it."""

    identity = _identity(git_dirty_reader=lambda: True)
    assert identity.git_dirty is True


def test_manifest_serializes_without_leaking_beyond_declared_fields():
    identity = _identity()
    serialized = json.dumps(identity.model_dump())
    assert set(json.loads(serialized)) == {
        "requested_model", "cli_version", "git_commit", "git_dirty", "platform",
        "started_at", "finished_at",
    }


# ====================================================================== Q1: ledger stops


def _ledger_identity():
    return capture_run_identity(
        "sonnet",
        cli_version_reader=lambda: "fake-cli",
        git_commit_reader=lambda: "abc1234",
        git_dirty_reader=lambda: False,
        platform_reader=lambda: "test",
        clock=lambda: "2026-07-11T00:00:00+00:00",
    )


def _passing_runner(name, *, notional=0.02, calls=1):
    from tests.support.engine_qualification import ScenarioOutcome

    async def run(_config):
        return ScenarioOutcome(
            name=name, status="passed", notional_usd=notional, worker_calls=calls
        )

    return run


@pytest.mark.unit
def test_config_rejects_unknown_keys_bad_caps_and_unsafe_paths(tmp_path):
    import pydantic

    from tests.support.engine_qualification import QualificationConfig

    good = dict(output_dir=str(tmp_path))
    QualificationConfig(**good)

    for bad in (
        {"surprise_knob": 1},
        {"per_invocation_budget_usd": 0},
        {"suite_budget_usd": float("inf")},
        {"suite_timeout_s": float("nan")},
        {"max_worker_calls": 0},
        {"output_dir": "../escape"},
        {"output_dir": ""},
        {"scenarios": ("anki_basic_card", "anki_basic_card")},
        {"scenarios": ("unknown_scenario",)},
        {"scenarios": ()},
    ):
        with pytest.raises((pydantic.ValidationError, ValueError)):
            QualificationConfig(**{**good, **bad})


@pytest.mark.unit
def test_ledger_stops_at_suite_budget_ceiling_before_next_paid_call(tmp_path):
    from tests.support.engine_qualification import QualificationConfig, run_qualification_suite

    config = QualificationConfig(
        output_dir=str(tmp_path / "out"), suite_budget_usd=0.05, live=True
    )
    started = {"gopro": False}

    async def expensive(name):
        from tests.support.engine_qualification import ScenarioOutcome

        return ScenarioOutcome(name=name, status="passed", notional_usd=0.03, worker_calls=1)

    async def must_not_start(_config):
        started["gopro"] = True
        raise AssertionError("scenario started past the budget ceiling")

    runners = {
        "anki_basic_card": lambda c: expensive("anki_basic_card"),
        "mageqa_local_audit": lambda c: expensive("mageqa_local_audit"),
        "gopro_frame_inspection": must_not_start,
        "slackazz_triage": must_not_start,
    }

    report = run_qualification_suite(config, runners, identity=_ledger_identity())

    assert not started["gopro"], "the ledger must stop BEFORE the next scenario starts"
    assert "budget ceiling" in (report.stopped_reason or "")
    assert not report.completed
    assert len(report.outcomes) == 2


@pytest.mark.unit
def test_ledger_fails_on_unknown_notional_cost_in_live_mode(tmp_path):
    from tests.support.engine_qualification import (
        QualificationConfig,
        ScenarioOutcome,
        run_qualification_suite,
    )

    config = QualificationConfig(output_dir=str(tmp_path / "out"), live=True)

    async def unknown_cost(_config):
        return ScenarioOutcome(
            name="anki_basic_card", status="passed", notional_usd=None, worker_calls=1
        )

    async def must_not_start(_config):
        raise AssertionError("suite continued past an unknown-cost scenario")

    runners = {
        "anki_basic_card": unknown_cost,
        "mageqa_local_audit": must_not_start,
        "gopro_frame_inspection": must_not_start,
        "slackazz_triage": must_not_start,
    }

    report = run_qualification_suite(config, runners, identity=_ledger_identity())

    assert "UNKNOWN notional cost" in (report.stopped_reason or "")
    assert report.outcomes[0].failure_class == "unknown_cost"
    assert not report.completed


@pytest.mark.unit
def test_ledger_hermetic_mode_tolerates_unpriced_scenarios(tmp_path):
    """Degradation pair: hermetic (free) runs do not demand a notional price."""

    from tests.support.engine_qualification import QualificationConfig, run_qualification_suite

    config = QualificationConfig(output_dir=str(tmp_path / "out"), live=False)
    runners = {
        name: _passing_runner(name, notional=None)
        for name in (
            "anki_basic_card",
            "mageqa_local_audit",
            "gopro_frame_inspection",
            "slackazz_triage",
        )
    }
    report = run_qualification_suite(config, runners, identity=_ledger_identity())

    assert report.completed
    assert report.notional_usd_total is None


@pytest.mark.unit
def test_ledger_stops_at_worker_call_ceiling(tmp_path):
    from tests.support.engine_qualification import QualificationConfig, run_qualification_suite

    config = QualificationConfig(output_dir=str(tmp_path / "out"), max_worker_calls=2)

    async def must_not_start(_config):
        raise AssertionError("scenario started past the worker-call ceiling")

    runners = {
        "anki_basic_card": _passing_runner("anki_basic_card", notional=0.01, calls=2),
        "mageqa_local_audit": must_not_start,
        "gopro_frame_inspection": must_not_start,
        "slackazz_triage": must_not_start,
    }

    report = run_qualification_suite(config, runners, identity=_ledger_identity())

    assert "worker-call ceiling" in (report.stopped_reason or "")
    assert len(report.outcomes) == 1


@pytest.mark.unit
def test_ledger_stops_on_suite_timeout_between_scenarios(tmp_path):
    from tests.support.engine_qualification import QualificationConfig, run_qualification_suite

    config = QualificationConfig(output_dir=str(tmp_path / "out"), suite_timeout_s=100.0)
    ticks = iter([0.0, 0.0, 500.0])  # started, scenario-1 check, scenario-2 check

    async def must_not_start(_config):
        raise AssertionError("scenario started past the suite timeout")

    runners = {
        "anki_basic_card": _passing_runner("anki_basic_card"),
        "mageqa_local_audit": must_not_start,
        "gopro_frame_inspection": must_not_start,
        "slackazz_triage": must_not_start,
    }

    report = run_qualification_suite(
        config, runners, identity=_ledger_identity(), clock=lambda: next(ticks)
    )

    assert "suite timeout" in (report.stopped_reason or "")
    assert len(report.outcomes) == 1


@pytest.mark.unit
def test_ledger_never_wires_a_missing_scenario_silently(tmp_path):
    from tests.support.engine_qualification import (
        QualificationConfig,
        QualificationError,
        run_qualification_suite,
    )

    config = QualificationConfig(output_dir=str(tmp_path / "out"))
    with pytest.raises(QualificationError, match="no runner wired"):
        run_qualification_suite(config, {}, identity=_ledger_identity())


# ================================================== Q-R adversarial regressions (codex probes)


@pytest.mark.unit
def test_last_scenario_overshoot_never_reports_completed(tmp_path):
    """Q-R1 (codex adversarial probe, now in-repo): 7 calls / $0.60 against 6 / $0.50 —
    the ceilings are enforced AFTER the last scenario too; completed=true would be a lie."""

    from tests.support.engine_qualification import QualificationConfig, ScenarioOutcome, run_qualification_suite

    config = QualificationConfig(output_dir=str(tmp_path / "out"), live=True)

    async def modest(name):
        return ScenarioOutcome(name=name, status="passed", notional_usd=0.05, worker_calls=1)

    async def greedy(_config):
        return ScenarioOutcome(
            name="slackazz_triage", status="passed", notional_usd=0.45, worker_calls=4
        )

    runners = {
        "anki_basic_card": lambda c: modest("anki_basic_card"),
        "mageqa_local_audit": lambda c: modest("mageqa_local_audit"),
        "gopro_frame_inspection": lambda c: modest("gopro_frame_inspection"),
        "slackazz_triage": greedy,  # the LAST scenario blows both ceilings
    }

    report = run_qualification_suite(
        config, runners, identity=_ledger_identity(), finish_clock=lambda: "T-END"
    )

    assert not report.completed, "an overshooting suite must NEVER claim completed"
    assert "EXCEEDED after scenario 'slackazz_triage'" in (report.stopped_reason or "")
    assert report.worker_calls_total == 7 and report.notional_usd_total == pytest.approx(0.60)


@pytest.mark.unit
def test_semantic_failures_are_classified_semantic_not_provider(tmp_path):
    """Q-R4 (codex adversarial probe): a ScenarioSemanticError is the SCENARIO's verdict —
    persisting it as 'provider' blames the wrong party; its burn data is preserved."""

    import json

    from pathlib import Path

    from tests.support.engine_qualification import (
        QualificationConfig,
        ScenarioSemanticError,
        run_qualification_suite,
    )

    config = QualificationConfig(output_dir=str(tmp_path / "out"))

    async def semantic_failure(_config):
        raise ScenarioSemanticError("card was empty", notional_usd=0.04, worker_calls=1)

    runners = {name: semantic_failure for name in config.scenarios}

    with pytest.raises(ScenarioSemanticError):
        run_qualification_suite(
            config, runners, identity=_ledger_identity(), finish_clock=lambda: "T-END"
        )

    persisted = json.loads(
        (Path(config.output_dir) / "qualification-summary.json").read_text(encoding="utf-8")
    )
    outcome = persisted["outcomes"][0]
    assert outcome["failure_class"] == "semantic"
    assert outcome["notional_usd"] == pytest.approx(0.04), "semantic failure kept its burn"
    assert persisted["identity"]["finished_at"] == "T-END"


@pytest.mark.unit
def test_unknown_harness_exceptions_are_not_blamed_on_the_provider(tmp_path):
    from tests.support.engine_qualification import QualificationConfig, run_qualification_suite

    import json
    from pathlib import Path

    config = QualificationConfig(output_dir=str(tmp_path / "out"))

    async def harness_bug(_config):
        raise KeyError("oops, harness defect")

    runners = {name: harness_bug for name in config.scenarios}
    with pytest.raises(KeyError):
        run_qualification_suite(
            config, runners, identity=_ledger_identity(), finish_clock=lambda: "T-END"
        )
    persisted = json.loads(
        (Path(config.output_dir) / "qualification-summary.json").read_text(encoding="utf-8")
    )
    assert persisted["outcomes"][0]["failure_class"] == "harness"


@pytest.mark.unit
def test_cli_cap_abort_maps_to_cap_class_with_burn(tmp_path):
    """Q-R2+Q-R4: a typed ConsoleCliError (budget abort) lands as failure_class=cap with
    the consumed notional in the ledger — machine data, not prose."""

    import json
    from pathlib import Path

    from ai_workflow_tools.cli_agents import ConsoleCliError

    from tests.support.engine_qualification import QualificationConfig, run_qualification_suite

    config = QualificationConfig(output_dir=str(tmp_path / "out"), live=True)

    async def aborted(_config):
        error = ConsoleCliError(
            "console CLI exited 1 (error_max_budget_usd); consumed notional ~$0.1122: ",
            cli_subtype="error_max_budget_usd",
            notional_usd=0.1122,
            returncode=1,
        )
        error.worker_calls = 1
        raise error

    runners = {name: aborted for name in config.scenarios}
    with pytest.raises(ConsoleCliError):
        run_qualification_suite(
            config, runners, identity=_ledger_identity(), finish_clock=lambda: "T-END"
        )
    persisted = json.loads(
        (Path(config.output_dir) / "qualification-summary.json").read_text(encoding="utf-8")
    )
    outcome = persisted["outcomes"][0]
    assert outcome["failure_class"] == "cap"
    assert outcome["notional_usd"] == pytest.approx(0.1122)
    assert persisted["notional_usd_total"] == pytest.approx(0.1122), (
        "the aborted call's burn must count toward the suite total"
    )


@pytest.mark.unit
def test_suite_refuses_to_start_when_declared_worst_case_exceeds_emergency_bound(tmp_path):
    """Q-R1 (amended): the finite emergency maximum is enforced BEFORE any work starts —
    the only spend a gate can stop is spend that has not happened yet."""

    from tests.support.engine_qualification import (
        QualificationConfig,
        QualificationError,
        declared_worst_case,
        run_qualification_suite,
    )

    calls, notional = declared_worst_case(
        ("anki_basic_card", "mageqa_local_audit", "gopro_frame_inspection", "slackazz_triage")
    )
    assert calls == 10 and notional == pytest.approx(1.20), (
        "the DOCUMENTED suite worst case is 10 calls / $1.20 — update SCENARIO_WORST_CASE "
        "and the plan doc together if a scenario legitimately grows"
    )

    config = QualificationConfig(
        output_dir=str(tmp_path / "out"), emergency_max_worker_calls=9
    )
    started = {"any": False}

    async def must_not_start(_config):
        started["any"] = True
        raise AssertionError("no scenario may start past the emergency gate")

    runners = {name: must_not_start for name in config.scenarios}
    with pytest.raises(QualificationError, match="emergency maximum"):
        run_qualification_suite(config, runners, identity=_ledger_identity())
    assert not started["any"]
