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
        raise ConsoleCliError(
            "console CLI exited 1 (error_max_budget_usd); consumed notional ~$0.1122: ",
            cli_subtype="error_max_budget_usd",
            notional_usd=0.1122,
            returncode=1,
        )  # NO monkeypatched attributes — the typed contract carries the attempt count

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

    config = QualificationConfig(
        output_dir=str(tmp_path / "out"), emergency_max_worker_calls=9
    )
    calls, notional = declared_worst_case(
        ("anki_basic_card", "mageqa_local_audit", "gopro_frame_inspection", "slackazz_triage"),
        config,
    )
    assert calls == 10 and notional == pytest.approx(1.20), (
        "the DOCUMENTED default worst case is 10 calls / $1.20 — update the structural "
        "declaration and the plan doc together if a scenario legitimately grows"
    )
    started = {"any": False}

    async def must_not_start(_config):
        started["any"] = True
        raise AssertionError("no scenario may start past the emergency gate")

    runners = {name: must_not_start for name in config.scenarios}
    with pytest.raises(QualificationError, match="emergency maximum"):
        run_qualification_suite(config, runners, identity=_ledger_identity())
    assert not started["any"]


# ============================== Q-R re-verification reproducers (codex, now permanent)


@pytest.mark.unit
def test_declared_emergency_cost_tracks_configured_invocation_caps(tmp_path):
    """Q-R1 (codex reproducer): raising the invocation caps RAISES the declared worst case —
    no second price table can certify a stale bound; the emergency gate must trip."""

    from tests.support.engine_qualification import (
        QualificationConfig,
        QualificationError,
        declared_worst_case,
        run_qualification_suite,
    )

    config = QualificationConfig(
        output_dir=str(tmp_path / "out"),
        per_invocation_budget_usd=0.50,
        vision_invocation_budget_usd=0.80,
    )
    calls, notional = declared_worst_case(config.scenarios, config)
    assert calls == 10
    assert notional == pytest.approx(8 * 0.50 + 2 * 0.80), (
        "declared cost must be computed FROM the configured caps"
    )

    async def must_not_start(_config):
        raise AssertionError("no scenario may start past a stale emergency bound")

    runners = {name: must_not_start for name in config.scenarios}
    with pytest.raises(QualificationError, match="emergency maximum"):
        run_qualification_suite(config, runners, identity=_ledger_identity())


@pytest.mark.unit
def test_real_typed_cli_error_counts_the_attempt_without_test_monkeypatch(tmp_path):
    """Q-R2 (codex reproducer): a production-constructed ConsoleCliError carries its attempt
    count intrinsically — the ledger persists 1 attempted call, not zero."""

    import json
    from pathlib import Path

    from ai_workflow_tools.cli_agents import ConsoleCliError

    from tests.support.engine_qualification import QualificationConfig, run_qualification_suite

    config = QualificationConfig(output_dir=str(tmp_path / "out"), live=True)

    async def aborted(_config):
        raise ConsoleCliError("console CLI exited 1: transport down", returncode=1)

    runners = {name: aborted for name in config.scenarios}
    with pytest.raises(ConsoleCliError):
        run_qualification_suite(config, runners, identity=_ledger_identity())
    persisted = json.loads(
        (Path(config.output_dir) / "qualification-summary.json").read_text(encoding="utf-8")
    )
    assert persisted["outcomes"][0]["worker_calls"] == 1, (
        "a real spawned-and-failed call is ATTEMPTED spend without any test monkeypatch"
    )
    assert persisted["worker_calls_total"] == 1


@pytest.mark.unit
def test_timeout_transport_failure_is_not_mislabeled_provider(tmp_path):
    """Q-R4 (codex reproducer): a timeout-shaped typed error with a return code classifies
    as timeout — explicit timeout evidence beats generic return-code heuristics."""

    from ai_workflow_tools.cli_agents import ConsoleCliError

    from tests.support.engine_qualification import classify_scenario_exception

    typed = ConsoleCliError(
        "external process timed out", returncode=-15, failure_kind="timeout"
    )
    assert classify_scenario_exception(typed) == "timeout"

    # even an UNTYPED timeout message with a returncode attribute must not become provider
    class LegacyTimeout(RuntimeError):
        returncode = -15

    assert classify_scenario_exception(LegacyTimeout("external process timed out")) == "timeout"


@pytest.mark.unit
def test_strict_dirty_gate_catches_untracked_source_override_but_not_ignored_env(tmp_path):
    """QRF.3 (codex reproducer): STRICT porcelain — an untracked, non-ignored source file is
    the override attack the gate exists for; the gitignored staged .env stays invisible; a
    tracked edit fails too."""

    import subprocess

    from tests.support.engine_qualification import _read_git_dirty

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=repo, check=True, capture_output=True,
        )

    git("init", "-q")
    (repo / "tracked.txt").write_text("v1", encoding="utf-8")
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    git("add", "tracked.txt", ".gitignore")
    git("commit", "-q", "-m", "init")

    (repo / ".env").write_text("SECRET=1", encoding="utf-8")  # gitignored runtime file
    assert _read_git_dirty(cwd=str(repo)) is False, (
        "ignored runtime files never appear in porcelain — the worktree procedure is runnable"
    )

    (repo / "evil_override.py").write_text("boom", encoding="utf-8")  # the attack
    assert _read_git_dirty(cwd=str(repo)) is True, (
        "an untracked source override MUST fail the release gate"
    )
    (repo / "evil_override.py").unlink()

    (repo / "tracked.txt").write_text("v2", encoding="utf-8")
    assert _read_git_dirty(cwd=str(repo)) is True, "tracked edits ARE source dirt"


@pytest.mark.unit
def test_console_error_rejects_nonpositive_attempts_and_unknown_kinds():
    """QRF.2 + QRF.4 (codex reproducers): the typed transport contract is CLOSED — zero or
    negative attempt counts and typo'd failure kinds are loud construction errors."""

    from ai_workflow_tools.cli_agents import ConsoleCliError

    for bad_calls in (0, -1, 1.5, "1", True):  # bool is an int subclass — still rejected
        with pytest.raises(ValueError, match="worker_calls"):
            ConsoleCliError("x", worker_calls=bad_calls)

    for bad_kind in ("timeuot", "PROVIDER", "unknown_cost", "harness "):
        with pytest.raises(ValueError, match="failure_kind"):
            ConsoleCliError("x", failure_kind=bad_kind)

    ok = ConsoleCliError("x", failure_kind="timeout", worker_calls=2)
    assert ok.failure_kind == "timeout" and ok.worker_calls == 2


@pytest.mark.unit
def test_worktree_export_procedure_preserves_evidence_and_allows_nonforced_removal(tmp_path):
    """QRF operational contract (codex): the release cleanup NEVER forces — evidence is
    exported and VERIFIED, staged transients are removed, and a plain (non-forced)
    `git worktree remove` succeeds. Anything unexpectedly left behind fails removal loudly."""

    import json
    import subprocess

    from tests.support.engine_qualification import (
        QualificationError,
        export_evidence_and_clean_worktree,
    )

    main = tmp_path / "main"
    main.mkdir()

    def git(*args, cwd=main):
        return subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=cwd, check=True, capture_output=True, text=True,
        )

    git("init", "-q")
    (main / "src.txt").write_text("v1", encoding="utf-8")
    (main / ".gitignore").write_text(".env\n", encoding="utf-8")
    git("add", "src.txt", ".gitignore")
    git("commit", "-q", "-m", "init")

    worktree = tmp_path / "release-wt"
    git("worktree", "add", "--detach", str(worktree), "HEAD")

    # simulate the release run: staged secrets + paid evidence inside the worktree
    (worktree / ".env").write_text("SECRET=1", encoding="utf-8")
    run_dir = worktree / "infra" / "test-results" / "engine-subscription-qualification" / "runX"
    run_dir.mkdir(parents=True)
    (run_dir / "qualification-summary.json").write_text(
        json.dumps({"completed": True}), encoding="utf-8"
    )
    (run_dir / "index.html").write_text("<html>evidence</html>", encoding="utf-8")

    exported_root = tmp_path / "exported"
    exported_root.mkdir()
    exported = export_evidence_and_clean_worktree(str(worktree), str(exported_root))

    # evidence survived, verified
    summary = (
        exported_root / "engine-subscription-qualification" / "runX" / "qualification-summary.json"
    )
    assert summary.exists() and json.loads(summary.read_text(encoding="utf-8"))["completed"]
    assert exported == str(exported_root / "engine-subscription-qualification")

    # NON-forced removal succeeds — the contract's whole point
    git("worktree", "remove", str(worktree))
    assert not worktree.exists()

    # and the guard is real: without evidence the helper refuses to clean anything
    worktree2 = tmp_path / "release-wt2"
    git("worktree", "add", "--detach", str(worktree2), "HEAD")
    with pytest.raises(QualificationError, match="no qualification evidence"):
        export_evidence_and_clean_worktree(str(worktree2), str(exported_root))
    git("worktree", "remove", str(worktree2))


@pytest.mark.unit
def test_stale_destination_summary_cannot_bless_an_incomplete_current_run(tmp_path):
    """QRF.3c.1 (codex reproducer): destination already holds an OLD valid summary; the
    CURRENT run has none — the helper must refuse BEFORE copying or deleting anything, and
    the incomplete run's only local evidence must survive."""

    import json

    from tests.support.engine_qualification import (
        QualificationError,
        export_evidence_and_clean_worktree,
    )

    worktree = tmp_path / "wt"
    current = worktree / "infra" / "test-results" / "engine-subscription-qualification" / "runNEW"
    current.mkdir(parents=True)
    (current / "index.html").write_text("<html>only artifact — incomplete</html>", encoding="utf-8")
    (worktree / ".env").write_text("SECRET=1", encoding="utf-8")

    dest = tmp_path / "dest"
    old_run = dest / "engine-subscription-qualification" / "runOLD"
    old_run.mkdir(parents=True)
    (old_run / "qualification-summary.json").write_text(
        json.dumps({"completed": True}), encoding="utf-8"
    )

    with pytest.raises(QualificationError, match="without their own qualification-summary"):
        export_evidence_and_clean_worktree(str(worktree), str(dest))

    assert (current / "index.html").exists(), "the incomplete run's ONLY evidence must survive"
    assert (worktree / ".env").exists(), "no cleanup may run on a failed verification"
    assert not (dest / "engine-subscription-qualification" / "runNEW").exists(), (
        "nothing may be copied for an incomplete run"
    )


@pytest.mark.unit
def test_export_rejects_destination_inside_worktree_and_run_id_collisions(tmp_path):
    """QRF.3c.2: a destination under the disposable worktree is a self-deletion trap; an
    existing destination run id is a loud collision — run directories are never merged."""

    import json

    from tests.support.engine_qualification import (
        QualificationError,
        export_evidence_and_clean_worktree,
    )

    worktree = tmp_path / "wt"
    run = worktree / "infra" / "test-results" / "engine-subscription-qualification" / "runX"
    run.mkdir(parents=True)
    (run / "qualification-summary.json").write_text(json.dumps({"completed": True}), encoding="utf-8")

    with pytest.raises(QualificationError, match="inside the disposable worktree"):
        export_evidence_and_clean_worktree(str(worktree), str(worktree / "exports"))

    dest = tmp_path / "dest"
    collision = dest / "engine-subscription-qualification" / "runX"
    collision.mkdir(parents=True)
    (collision / "qualification-summary.json").write_text(
        json.dumps({"completed": False, "older": True}), encoding="utf-8"
    )

    with pytest.raises(QualificationError, match="never merged or overwritten"):
        export_evidence_and_clean_worktree(str(worktree), str(dest))

    assert json.loads(
        (collision / "qualification-summary.json").read_text(encoding="utf-8")
    )["older"] is True, "prior evidence must remain byte-identical after a collision refusal"
    assert (run / "qualification-summary.json").exists(), "source evidence untouched"


@pytest.mark.unit
def test_export_verifies_copied_summary_bytes_before_cleaning(tmp_path, monkeypatch):
    """QRF.3c.3: a copy that lands corrupted fails byte verification — cleanup never runs
    and the source evidence stays untouched."""

    import json
    import pathlib
    import shutil

    from tests.support import engine_qualification as eq

    worktree = tmp_path / "wt"
    run = worktree / "infra" / "test-results" / "engine-subscription-qualification" / "runX"
    run.mkdir(parents=True)
    (run / "qualification-summary.json").write_text(json.dumps({"completed": True}), encoding="utf-8")
    (worktree / ".env").write_text("SECRET=1", encoding="utf-8")

    original_copytree = shutil.copytree

    def corrupting_copytree(src, dst, **kwargs):
        result = original_copytree(src, dst, **kwargs)
        summary = pathlib.Path(dst) / "qualification-summary.json"
        if summary.exists():
            summary.write_text("{corrupted}", encoding="utf-8")
        return result

    # the helper does `import shutil` inside the function — patching the stdlib module
    # attribute is exactly the boundary it will read
    monkeypatch.setattr(shutil, "copytree", corrupting_copytree)

    with pytest.raises(eq.QualificationError, match="failed byte verification"):
        eq.export_evidence_and_clean_worktree(str(worktree), str(tmp_path / "dest"))

    assert (run / "qualification-summary.json").exists(), "source evidence untouched"
    assert (worktree / ".env").exists(), "cleanup must not run after failed verification"


# ============================== W5.2: hermetic durable cross-consumer scenario


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hermetic_slack_durable_approval_full_lifecycle(tmp_path):
    """W5.2 (repaired per W5-C3): the Slack-shaped consumer on REAL public engine doors
    with a fake provider — durable approval, duplicate-delivery idempotence, a chained
    second gate, due()-discovered timeout through the DECLARED route, an action-intent
    sink keyed by wait_idempotency proving ONE intent under timeout REDELIVERY, and a
    GENUINE engine denial of the external Slack send (side-effect gate, not a prop)."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine import (
        DurableWaitPolicy,
        InMemoryWaitCoordinator,
        ObservationConfig,
        WorkflowBuilder,
        WorkflowEngineBuilder,
    )
    from ai_workflow_engine.models import RuntimeLimits, SafetyPolicy, WorkflowGoal, WorkflowProfile
    from ai_workflow_viewer import FileEventSource
    from pydantic import BaseModel

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    coordinator = InMemoryWaitCoordinator(clock=clock, shared_state={})
    slack_executions: list = []
    action_intents: dict = {}  # the PRODUCT outbox: idempotency key -> intent count

    builder = (
        WorkflowEngineBuilder()
        .with_observation(ObservationConfig(enabled=True, bundle_dir=str(tmp_path)))
        .with_wait_coordinator(coordinator, clock=clock)
        .with_profile(
            WorkflowProfile(
                workflow_type="slack_hermetic",
                limits=RuntimeLimits(max_text_calls=3),
                safety=SafetyPolicy(allowed_side_effects=["read_only"]),
            )
        )
    )

    class Draft(BaseModel):
        status: str
        decision: str = ""
        draft: str = "reply-draft"

    def classify(_context, payload):  # fake provider: deterministic, zero network
        return {"draft": f"triage:{payload['message']}"}

    def approval(context, payload):
        draft = payload.draft if isinstance(payload, Draft) else payload["draft"]
        event = context.metadata.get("resume_event")
        if event is None:
            return Draft(status="pending", draft=draft)
        return Draft(status="answered", decision=str(event.get("decision")), draft=draft)

    def second_gate(context, payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return Draft(status="pending", draft="second")
        return Draft(status="answered", decision=str(event.get("decision")), draft="second")

    def finish(_context, payload):
        return {"done": True, "decision": getattr(payload, "decision", "")}

    def record_escalation_intent(context, _payload):
        # PRODUCT pattern: external action INTENTS are keyed by the engine's stable
        # wait/event idempotency context — at-least-once redelivery collapses to one.
        key = context.metadata.get("wait_idempotency") or "missing"
        action_intents[key] = action_intents.get(key, 0) + 1
        return {"escalated": True, "intent_key": key}

    def send_slack(_context, payload):
        slack_executions.append(payload)  # must NEVER run — engine denies external_call
        return {"sent": True}

    builder.register_capability("classify", classify, kind="deterministic")
    builder.register_capability("approval", approval, kind="deterministic")
    builder.register_capability("second_gate", second_gate, kind="deterministic")
    builder.register_capability("finish", finish, kind="deterministic")
    builder.register_capability(
        "record_escalation_intent", record_escalation_intent, kind="deterministic"
    )
    builder.register_capability(
        "send_slack", send_slack, kind="external", side_effects=["external_call"]
    )
    builder.register_workflow(
        WorkflowBuilder("slack_durable")
        .step("classify")
        .human("approval", wait_policy=DurableWaitPolicy(timeout_s=3600), timeout_to="record_escalation_intent")
        .human("second_gate", wait_policy=DurableWaitPolicy(timeout_s=3600), timeout_to="record_escalation_intent")
        .step("finish")
        .step("record_escalation_intent")
        .build()
    )
    builder.register_workflow(
        WorkflowBuilder("slack_send_attempt").step("send_slack").build()
    )
    engine = builder.build()

    # arc 1: approval — suspend, DUPLICATE deliveries stay idempotent, then approve
    goal = WorkflowGoal(workflow_type="slack_durable", objective="approve", metadata={"run_id": "sd-1"})
    first = await engine.run("slack_durable", {"message": "deploy?"}, goal=goal)
    assert first.status == "requires_user_input" and first.wait_handle is not None
    assert first.snapshot is None, "durable suspensions expose the handle door only"
    wait_1 = first.wait_handle.wait_id

    ok = await engine.deliver_wait_event(
        wait_1, {"kind": "signal", "event_id": "evt-approve", "payload": {"decision": "approve"}}
    )
    assert ok.kind == "executed"
    dup = await engine.deliver_wait_event(
        wait_1, {"kind": "signal", "event_id": "evt-approve", "payload": {"decision": "approve"}}
    )
    assert dup.kind == "duplicate" and dup.run_result is None

    # arc 2 (repeated wait): the SECOND durable gate suspended inside the resumed run
    chained = ok.run_result
    assert chained.status == "requires_user_input" and chained.wait_handle is not None
    done = await engine.deliver_wait_event(
        chained.wait_handle.wait_id,
        {"kind": "signal", "event_id": "evt-2", "payload": {"decision": "ship"}},
    )
    assert done.kind == "executed" and done.run_result.status == "completed"
    assert done.run_result.output["done"] is True

    # arc 3 (timeout + ACTION-INTENT DEDUP): nobody answers; the product loop discovers
    # the overdue wait via due() and delivers the timeout; REDELIVERING the same timeout
    # must not duplicate the escalation intent.
    goal_t = WorkflowGoal(workflow_type="slack_durable", objective="timeout", metadata={"run_id": "sd-2"})
    second = await engine.run("slack_durable", {"message": "ping"}, goal=goal_t)
    wait_t = second.wait_handle.wait_id
    current["now"] += timedelta(seconds=3601)
    due = await coordinator.due(clock())
    assert [r.wait_id for r in due] == [wait_t]
    timed_out = await engine.deliver_wait_event(wait_t, {"kind": "timeout", "event_id": "evt-T"})
    assert timed_out.kind == "executed"
    assert timed_out.run_result.output["intent_key"] == f"{wait_t}:evt-T"
    redelivered = await engine.deliver_wait_event(wait_t, {"kind": "timeout", "event_id": "evt-T"})
    assert redelivered.kind == "duplicate" and redelivered.run_result is None
    assert action_intents == {f"{wait_t}:evt-T": 1}, (
        "duplicate timeout delivery must NEVER duplicate an action intent"
    )

    # arc 4 (GENUINE denial): the external Slack send is engine-DENIED pre-invocation
    goal_d = WorkflowGoal(
        workflow_type="slack_send_attempt", objective="send", metadata={"run_id": "sd-deny"}
    )
    denied = await engine.run("slack_send_attempt", {"text": "hi"}, goal=goal_d)
    assert denied.status == "failed" and "external_call" in (denied.error or ""), (
        f"the send must be denied EXPLICITLY by the side-effect gate: {denied.error}"
    )
    assert slack_executions == [], "the denied capability must never have executed"

    # ledger truth across the logical runs
    source = FileEventSource(tmp_path)
    g1 = source.read_group("sd-1")
    assert g1.status == "completed" and [s.segment_index for s in g1.segments] == [0, 1, 2]
    assert g1.usage_totals["scope"] == "actual_all_attempts"
    g2 = source.read_group("sd-2")
    assert g2.status == "completed" and g2.segments[-1].kind == "resume"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hermetic_slack_cooldown_ack_and_membership_lifecycle(tmp_path):
    """W5-C3: the remaining refreshed-SlackAzzCovered arcs, hermetically, public doors only:
    (a) FAST bounded classifier retry (business Retry policy) recovers a flaky provider in
    ONE run — completely separate from max_resume_attempts (delivery crash recovery);
    (b) provider OUTAGE routes to a recoverable durable COOLDOWN wait carrying a typed
    operational-alert proposal — never terminal lost work — and heals via the declared
    timeout route; (c) a manager acknowledgement CANCELS the pending escalation wait
    (product-driven cancel_wait) with terminal evidence and no escalation intent;
    (d) a no-manager state suspends durably and resumes after membership refresh."""

    from datetime import datetime, timedelta, timezone

    from ai_workflow_engine import (
        DurableWaitPolicy,
        InMemoryWaitCoordinator,
        ObservationConfig,
        Retry,
        WorkflowBuilder,
        WorkflowEngineBuilder,
    )
    from ai_workflow_engine.models import WorkflowGoal
    from ai_workflow_viewer import FileEventSource
    from pydantic import BaseModel

    current = {"now": datetime(2036, 1, 1, tzinfo=timezone.utc)}
    clock = lambda: current["now"]  # noqa: E731
    coordinator = InMemoryWaitCoordinator(clock=clock, shared_state={})
    provider = {"failures_left": 2, "healed": False}
    escalation_intents: list = []

    builder = (
        WorkflowEngineBuilder()
        .with_observation(ObservationConfig(enabled=True, bundle_dir=str(tmp_path)))
        .with_wait_coordinator(coordinator, clock=clock)
    )

    class Gate(BaseModel):
        status: str
        value: str = ""

    def flaky_classifier(_context, payload):
        # (a) transient flake: fails twice, then classifies — the NODE Retry policy
        # (business bounded retry) absorbs it inside one run
        if provider["failures_left"] > 0:
            provider["failures_left"] -= 1
            raise RuntimeError("provider hiccup")
        return {"classified": True, "message": payload["message"]}

    class OperationalAlertProposal(BaseModel):
        # W5R.5: a GENUINELY typed, source-neutral alert PROPOSAL — strict at the
        # capability output boundary; it proposes, it never sends.
        model_config = {"extra": "forbid"}

        kind: str
        provider: str
        proposed_action: str

    class ClassifierOutput(BaseModel):
        model_config = {"extra": "forbid"}

        classified: bool
        message: str = ""
        alert_proposal: OperationalAlertProposal | None = None

    def outage_classifier(_context, payload):
        # (b) hard outage until healed — the run must route to cooldown, not die
        if not provider["healed"]:
            return ClassifierOutput(
                classified=False,
                alert_proposal=OperationalAlertProposal(
                    kind="provider_outage",
                    provider="fake-slack-classifier",
                    proposed_action="notify-oncall",
                ),
            )
        return ClassifierOutput(classified=True, message=payload["message"])


    def cooldown_gate(context, payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending", value="cooling")
        return Gate(status="answered", value="woke")

    def retry_classify(_context, _payload):
        assert provider["healed"], "the cooldown timeout fires only after healing in this arc"
        return {"classified": True, "recovered": True}

    def gate(context, _payload):
        event = context.metadata.get("resume_event")
        if event is None:
            return Gate(status="pending")
        return Gate(status="answered", value=str(event))

    def escalate(_context, _payload):
        escalation_intents.append("escalated")
        return {"escalated": True}

    def finish(_context, payload):
        return {"done": True, "value": getattr(payload, "value", None) or payload}

    builder.register_capability("flaky_classifier", flaky_classifier, kind="deterministic")
    builder.register_capability("outage_classifier", outage_classifier, kind="deterministic")
    builder.register_guard(
        "outage_router", lambda payload: "cooldown" if not payload.classified else "proceed"
    )
    builder.register_capability("cooldown_gate", cooldown_gate, kind="deterministic")
    builder.register_capability("retry_classify", retry_classify, kind="deterministic")
    builder.register_capability("gate", gate, kind="deterministic")
    builder.register_capability("escalate", escalate, kind="deterministic")
    builder.register_capability("finish", finish, kind="deterministic")

    builder.register_workflow(
        WorkflowBuilder("flaky_flow")
        .step("flaky_classifier", retry=Retry(3))
        .step("finish")
        .build()
    )
    builder.register_workflow(
        WorkflowBuilder("outage_flow")
        .step("outage_classifier")
        .branch("outage_router", {"cooldown": "cooldown_gate", "proceed": "finish"})
        .human("cooldown_gate", wait_policy=DurableWaitPolicy(timeout_s=900), timeout_to="retry_classify")
        .step("retry_classify")
        .step("finish")
        .build()
    )
    builder.register_workflow(
        WorkflowBuilder("escalation_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=3600), timeout_to="escalate")
        .step("finish")
        .step("escalate")
        .build()
    )
    builder.register_workflow(
        WorkflowBuilder("membership_flow")
        .human("gate", wait_policy=DurableWaitPolicy(timeout_s=3600), timeout_to="escalate")
        .step("finish")
        .step("escalate")
        .build()
    )
    engine = builder.build()

    # (a) fast bounded retry: one run, no waits involved, business limit ≠ delivery limit
    flaky = await engine.run(
        "flaky_flow", {"message": "hello"},
        goal=WorkflowGoal(workflow_type="flaky_flow", objective="a", metadata={"run_id": "flaky-run"}),
    )
    assert flaky.status == "completed", flaky.error
    assert provider["failures_left"] == 0
    assert (await coordinator.health()).claimed == 0, (
        "business retries never touch wait machinery — max_resume_attempts is delivery-"
        "crash recovery only"
    )

    # (b) outage -> recoverable durable cooldown with a typed alert proposal, then heal
    outage = await engine.run(
        "outage_flow", {"message": "classify me"},
        goal=WorkflowGoal(workflow_type="outage_flow", objective="b", metadata={"run_id": "outage-run"}),
    )
    assert outage.status == "requires_user_input", f"outage must suspend recoverably, never die: {outage.error}"
    assert outage.wait_handle.suspended_node == "cooldown_gate"
    alert = next(
        r.output for r in outage.node_results if r.node_id == "outage_classifier"
    ).alert_proposal
    assert alert.kind == "provider_outage" and alert.proposed_action == "notify-oncall"
    # strictness pair: malformed/extra fields refuse BEFORE any downstream action mapping
    import pytest as _pytest

    with _pytest.raises(Exception):
        OperationalAlertProposal(kind="provider_outage", provider="x")  # missing action
    with _pytest.raises(Exception):
        OperationalAlertProposal(
            kind="provider_outage", provider="x", proposed_action="y", severity="high"
        )  # extra field forbidden
    provider["healed"] = True
    current["now"] += timedelta(seconds=901)
    assert [r.wait_id for r in await coordinator.due(clock())] == [outage.wait_handle.wait_id]
    recovered = await engine.deliver_wait_event(
        outage.wait_handle.wait_id, {"kind": "timeout", "event_id": "evt-cool"}
    )
    assert recovered.kind == "executed" and recovered.run_result.status == "completed"
    assert recovered.run_result.output.get("recovered") is True, (
        "the declared on_timeout route re-classified successfully after healing"
    )
    stored = await coordinator.get(outage.wait_handle.wait_id)
    assert stored.resume_attempts == 1, "one delivery attempt — cooldown is not a retry loop"

    # (c) acknowledgement cancels the LATER escalation: the product's loop receives the
    # manager's ack and cancels the pending wait instead of letting it escalate
    esc = await engine.run(
        "escalation_flow", {},
        goal=WorkflowGoal(workflow_type="escalation_flow", objective="c", metadata={"run_id": "ack-run"}),
    )
    wait_esc = esc.wait_handle.wait_id
    current["now"] += timedelta(seconds=3601)  # overdue — but the ack arrived out of band
    record, observation = await engine.cancel_wait(
        wait_esc, reason="manager acknowledged in-channel; escalation unnecessary"
    )
    assert record.status == "cancelled" and observation == "recorded"
    assert escalation_intents == [], "an acknowledged case must never escalate"
    late_timeout = await engine.deliver_wait_event(
        wait_esc, {"kind": "timeout", "event_id": "evt-late"}
    )
    assert late_timeout.kind == "terminal" and late_timeout.run_result is None, (
        "the raced timeout after cancellation is a terminal report, not an escalation"
    )
    assert escalation_intents == []
    assert FileEventSource(tmp_path).read_group("ack-run").status == "cancelled"

    # (d) no-manager state: suspend durably, resume after membership refresh
    member = await engine.run(
        "membership_flow", {},
        goal=WorkflowGoal(workflow_type="membership_flow", objective="d", metadata={"run_id": "member-run"}),
    )
    refreshed = await engine.deliver_wait_event(
        member.wait_handle.wait_id,
        {"kind": "signal", "event_id": "evt-roster", "payload": {"manager": "alice"}},
    )
    assert refreshed.kind == "executed" and refreshed.run_result.status == "completed"
    assert "alice" in str(refreshed.run_result.output["value"]), (
        "the resumed run must consume the REFRESHED membership payload"
    )
