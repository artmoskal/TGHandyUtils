"""Cross-consumer engine qualification support — Q0.2 runtime identity manifest.

The manifest makes a qualification run diagnosable (alias/CLI drift) WITHOUT leaking
anything sensitive: it records exactly the six identity facts and nothing else —
no auth token, no environment dump, no prompt text, no home paths.
"""

from __future__ import annotations

import platform as _platform_module
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, field_validator

_SENSITIVE_MARKERS = ("sk-ant-", "oauth", "token=", "authorization:")


class QualificationRunIdentity(BaseModel):
    """The ONLY identity facts a qualification manifest may carry (extra fields forbidden)."""

    model_config = ConfigDict(extra="forbid")

    requested_model: str
    cli_version: str
    git_commit: str
    # Q-R3: evidence must map to EXACTLY one source revision — a dirty tree is recorded,
    # and a RELEASE qualification run refuses to proceed on one.
    git_dirty: bool = False
    platform: str
    started_at: str
    finished_at: Optional[str] = None

    @field_validator("*")
    @classmethod
    def _no_sensitive_values(cls, value):
        if isinstance(value, str):
            lowered = value.lower()
            for marker in _SENSITIVE_MARKERS:
                if marker in lowered:
                    raise ValueError(f"sensitive marker {marker!r} must never enter the manifest")
            if lowered.startswith(("/users/", "/home/")):
                raise ValueError("home paths must never enter the manifest")
        return value


def capture_run_identity(
    requested_model: str,
    *,
    cli_version_reader: Optional[Callable[[], str]] = None,
    git_commit_reader: Optional[Callable[[], str]] = None,
    git_dirty_reader: Optional[Callable[[], bool]] = None,
    platform_reader: Optional[Callable[[], str]] = None,
    clock: Optional[Callable[[], str]] = None,
) -> QualificationRunIdentity:
    """Capture the run identity. Readers are injectable so unit tests stay deterministic
    and spawn nothing; the defaults shell out to `claude --version` / `git rev-parse`."""

    if not requested_model:
        raise ValueError("requested_model is required — a run without a model alias is not reproducible")
    read_version = cli_version_reader or _read_claude_cli_version
    read_commit = git_commit_reader or _read_git_commit
    read_platform = platform_reader or (lambda: f"{_platform_module.system()}-{_platform_module.machine()}")
    read_clock = clock or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    read_dirty = git_dirty_reader or _read_git_dirty
    return QualificationRunIdentity(
        requested_model=requested_model,
        cli_version=read_version(),
        git_commit=read_commit(),
        git_dirty=read_dirty(),
        platform=read_platform(),
        started_at=read_clock(),
    )


def _read_claude_cli_version() -> str:
    proc = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude --version failed: {proc.stderr.strip()[-300:]}")
    return proc.stdout.strip()


def _read_git_dirty(cwd: "str | None" = None) -> bool:
    # QRF.3: STRICT full porcelain. An untracked, non-ignored source file is exactly the
    # override attack "evidence maps to one source revision" must catch. GITIGNORED runtime
    # files (the staged .env) never appear in porcelain, so the detached-worktree release
    # procedure passes clean by construction while any smuggled source fails it.
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=cwd,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git status failed: {proc.stderr.strip()[-300:]}")
    return bool(proc.stdout.strip())


def _read_git_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {proc.stderr.strip()[-300:]}")
    return proc.stdout.strip()


# ====================================================================== Q1: config + ledger


class QualificationError(RuntimeError):
    """Loud harness failure — the suite never skips or falls back silently."""


class ScenarioSemanticError(QualificationError):
    """A scenario's semantic invariant failed (engine-meaningful, not infrastructure).

    Q-R2/Q-R4: it may CARRY the burn the scenario had already caused (notional + attempted
    calls) so a semantic failure never erases money data from the ledger."""

    def __init__(self, message: str, *, notional_usd: Optional[float] = None, worker_calls: int = 0):
        super().__init__(message)
        self.notional_usd = notional_usd
        self.worker_calls = worker_calls


FAILURE_CLASSES = ("semantic", "provider", "cap", "timeout", "unknown_cost", "harness")


def classify_scenario_exception(exc: BaseException) -> str:
    """Q-R4: map an escaped scenario exception to the CONTRACT's failure class — never
    blame the provider for semantic or harness defects."""

    if isinstance(exc, ScenarioSemanticError):
        return "semantic"
    # Q-R4: a transport that classified ITSELF wins outright; heuristics only for untyped
    # exceptions — and explicit timeout evidence beats generic return codes.
    typed_kind = str(getattr(exc, "failure_kind", "") or "")
    if typed_kind in FAILURE_CLASSES:
        return typed_kind
    subtype = str(getattr(exc, "cli_subtype", "") or "")
    if subtype == "error_max_budget_usd":
        return "cap"
    message = str(exc).lower()
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if getattr(exc, "returncode", None) is not None or subtype:
        return "provider"
    if "console cli" in message or "external process" in message or "transport" in message:
        return "provider"
    return "harness"


KNOWN_SCENARIOS = (
    "anki_basic_card",
    "mageqa_local_audit",
    "gopro_frame_inspection",
    "slackazz_triage",
)

# Q-R1 (amended contract + codex re-verification): the suite ceilings are the EXPECTED
# TARGET; the DECLARED worst case is STRUCTURAL (how many calls of which ROLE each scenario
# can spawn, assuming every structured worker burns its one repair) and is PRICED from the
# VALIDATED CONFIG's per-invocation caps — never from a second hard-coded price table, so a
# raised cap raises the declared bound and can trip the emergency gate. With defaults:
# 10 calls / $1.20. The runner refuses to START a suite whose declared worst case exceeds
# the configured emergency maximum — bounded by construction, never by luck.
SCENARIO_WORST_CASE_CALLS = {
    "anki_basic_card": {"completion": 2},
    "mageqa_local_audit": {"completion": 4},
    "gopro_frame_inspection": {"vision": 2},
    "slackazz_triage": {"completion": 2},
}


def declared_worst_case(scenarios, config: "QualificationConfig") -> tuple:
    price = {
        "completion": config.per_invocation_budget_usd,
        "vision": config.vision_invocation_budget_usd,
    }
    calls = 0
    notional = 0.0
    for name in scenarios:
        for role, count in SCENARIO_WORST_CASE_CALLS[name].items():
            calls += count
            notional += count * price[role]
    return calls, round(notional, 6)


class QualificationConfig(BaseModel):
    """One validated source for model, caps, output dir, and scenario selection (Q1.1)."""

    model_config = ConfigDict(extra="forbid")

    model: str = "sonnet"
    live: bool = False
    per_invocation_budget_usd: float = 0.10
    # Staged-vision calls are TOOL SESSIONS (claude reads the image file: measured 4 turns,
    # total_cost_usd 0.112 on sonnet, 2026-07-11 envelope evidence) — they get their own
    # pre-call cap; the suite ceiling still bounds the total.
    vision_invocation_budget_usd: float = 0.20
    suite_budget_usd: float = 0.50
    # Q-R1 (amended): finite EMERGENCY maximums — the documented worst case the suite can
    # reach even when everything retries; the expected target above is what a healthy run
    # stays under. Pre-start gate: declared worst case must fit these.
    emergency_max_worker_calls: int = 10
    emergency_max_notional_usd: float = 1.20
    max_worker_calls: int = 6
    per_invocation_timeout_s: float = 180.0
    suite_timeout_s: float = 900.0
    output_dir: str
    scenarios: tuple = KNOWN_SCENARIOS

    @field_validator(
        "per_invocation_budget_usd",
        "vision_invocation_budget_usd",
        "suite_budget_usd",
        "emergency_max_notional_usd",
        "per_invocation_timeout_s",
        "suite_timeout_s",
    )
    @classmethod
    def _finite_positive(cls, value: float):
        import math

        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"caps must be finite positive numbers, got {value}")
        return value

    @field_validator("max_worker_calls")
    @classmethod
    def _positive_calls(cls, value: int):
        if value < 1:
            raise ValueError(f"max_worker_calls must be >= 1, got {value}")
        return value

    @field_validator("output_dir")
    @classmethod
    def _safe_output_dir(cls, value: str):
        from pathlib import PurePosixPath

        if not value:
            raise ValueError("output_dir is required")
        if ".." in PurePosixPath(value).parts:
            raise ValueError(f"output_dir must not traverse upward: {value!r}")
        return value

    @field_validator("scenarios")
    @classmethod
    def _known_unique_scenarios(cls, value):
        names = tuple(value)
        if not names:
            raise ValueError("at least one scenario is required")
        unknown = [n for n in names if n not in KNOWN_SCENARIOS]
        if unknown:
            raise ValueError(f"unknown scenarios: {unknown}; known: {list(KNOWN_SCENARIOS)}")
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate scenarios: {names}")
        return names


class ScenarioOutcome(BaseModel):
    """What one scenario reports back to the ledger — honest, per the money rules."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: str  # "passed" | "failed"
    failure_class: Optional[str] = None  # semantic|provider|cap|timeout|unknown_cost
    notional_usd: Optional[float] = None  # None = provider did not report -> UNKNOWN
    worker_calls: int = 0  # ATTEMPTED paid calls (success + failure) — the spend bound
    failed_worker_calls: int = 0
    duration_s: Optional[float] = None
    bundle_path: Optional[str] = None
    viewer_html_path: Optional[str] = None
    # Q-R5: multi-run scenarios link EVERY half of their lifecycle (author engine run,
    # suspension half, ...) — label -> bundle path; the reporter renders a page per bundle.
    linked_bundles: dict = {}
    linked_viewer_paths: dict = {}
    detail: str = ""


class SuiteReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: QualificationRunIdentity
    config: QualificationConfig
    outcomes: list = []
    notional_usd_total: Optional[float] = None
    worker_calls_total: int = 0
    failed_worker_calls_total: int = 0
    stopped_reason: Optional[str] = None
    completed: bool = False


def persist_suite_report(report: "SuiteReport") -> None:
    """Recompute totals and write the manifest — the ONE serialization used by the runner
    after every scenario and by the reporter after viewer generation (Q-R6)."""

    from pathlib import Path

    known = [o.notional_usd for o in report.outcomes if o.notional_usd is not None]
    report.notional_usd_total = round(sum(known), 6) if known else None
    report.worker_calls_total = sum(o.worker_calls for o in report.outcomes)
    report.failed_worker_calls_total = sum(o.failed_worker_calls for o in report.outcomes)
    out_dir = Path(report.config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "qualification-summary.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )


def run_qualification_suite(
    config: QualificationConfig,
    scenario_runners: "dict[str, Any]",
    *,
    identity: QualificationRunIdentity,
    clock: Optional[Any] = None,
    finish_clock: Optional[Any] = None,
) -> SuiteReport:
    """Sequential fail-fast orchestration + notional-cost ledger (Q1.2).

    The ledger writes the partial manifest to disk BEFORE deciding whether another
    (potentially paid) scenario may start, and stops on: semantic failure, unknown
    notional cost in live mode, suite budget ceiling, suite timeout, or worker-call
    ceiling. It never skips a configured scenario silently."""

    import asyncio
    import time as _time
    from datetime import datetime, timezone
    from pathlib import Path

    now = clock or _time.monotonic
    read_finish = finish_clock or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    started = now()
    # Q-R1 (amended): refuse to START work whose DECLARED worst case exceeds the finite
    # emergency bound — spend that has not happened yet is the only spend a gate can stop.
    worst_calls, worst_notional = declared_worst_case(config.scenarios, config)
    if worst_calls > config.emergency_max_worker_calls or (
        worst_notional > config.emergency_max_notional_usd
    ):
        raise QualificationError(
            f"declared worst case ({worst_calls} calls / ${worst_notional:.2f}) exceeds the "
            f"emergency maximum ({config.emergency_max_worker_calls} calls / "
            f"${config.emergency_max_notional_usd:.2f}) — shrink the scenario set or raise "
            "the documented bound deliberately"
        )
    report = SuiteReport(identity=identity, config=config)
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _persist() -> None:
        persist_suite_report(report)

    def _finish(reason: Optional[str]) -> SuiteReport:
        # Q-R6: EVERY terminal path stamps finished_at and persists the final report.
        report.stopped_reason = reason
        report.completed = reason is None
        report.identity.finished_at = read_finish()
        _persist()
        return report

    def _spent() -> float:
        return sum(o.notional_usd or 0.0 for o in report.outcomes)

    def _ceiling_violation(when: str) -> Optional[str]:
        # Q-R1: ceilings are validated AFTER every scenario too (including the last) —
        # "completed" is a claim about the WHOLE run, not about the pre-checks.
        if _spent() > config.suite_budget_usd:
            return (
                f"suite budget ceiling EXCEEDED {when} (${_spent():.4f} > "
                f"${config.suite_budget_usd:.2f})"
            )
        if report.worker_calls_total > config.max_worker_calls:
            return (
                f"worker-call ceiling EXCEEDED {when} ({report.worker_calls_total} > "
                f"{config.max_worker_calls})"
            )
        elapsed = now() - started
        if elapsed > config.suite_timeout_s:
            return f"suite timeout EXCEEDED {when} ({elapsed:.0f}s > {config.suite_timeout_s:.0f}s)"
        return None

    for name in config.scenarios:
        if name not in scenario_runners:
            reason = f"no runner wired for scenario {name!r}"
            _finish(reason)
            raise QualificationError(reason)
        elapsed = now() - started
        if elapsed >= config.suite_timeout_s:
            return _finish(f"suite timeout ({elapsed:.0f}s >= {config.suite_timeout_s:.0f}s)")
        if _spent() >= config.suite_budget_usd:
            return _finish(
                f"suite budget ceiling reached (${_spent():.4f} >= ${config.suite_budget_usd:.2f})"
            )
        if report.worker_calls_total >= config.max_worker_calls:
            return _finish(
                f"worker-call ceiling reached ({report.worker_calls_total} >= {config.max_worker_calls})"
            )

        try:
            outcome = asyncio.run(scenario_runners[name](config))
        except Exception as exc:
            # Q-R2/Q-R4: typed classification + burn data carried by the exception — a
            # failed paid call is money data, not prose.
            report.outcomes.append(
                ScenarioOutcome(
                    name=name,
                    status="failed",
                    failure_class=classify_scenario_exception(exc),
                    notional_usd=getattr(exc, "notional_usd", None),
                    worker_calls=int(getattr(exc, "worker_calls", 0) or 0),
                    failed_worker_calls=int(getattr(exc, "worker_calls", 0) or 0),
                    detail=str(exc)[:500],
                )
            )
            _finish(f"scenario {name!r} raised: {exc}")
            raise
        report.outcomes.append(outcome)
        _persist()  # result on disk BEFORE the next paid call may start

        violation = _ceiling_violation(f"after scenario {name!r}")
        if violation is not None:
            return _finish(violation)
        if outcome.status != "passed":
            return _finish(
                f"scenario {name!r} failed ({outcome.failure_class}): {outcome.detail}"
            )
        if config.live and outcome.worker_calls > 0 and outcome.notional_usd is None:
            outcome.failure_class = "unknown_cost"
            outcome.status = "failed"
            return _finish(
                f"scenario {name!r} reported UNKNOWN notional cost in live mode — "
                "the ledger cannot bound spend, stopping"
            )

    return _finish(None)


# ====================================================================== Q3.1: visual report


def render_suite_report_html(report: SuiteReport) -> str:
    """Write per-scenario viewer HTML + one index.html; returns the index path (Q3.1)."""

    from pathlib import Path

    from ai_workflow_viewer import (
        FileEventSource,
        build_observation_graph,
        observation_group_to_html,
        save_observation_html,
    )

    out_dir = Path(report.config.output_dir)

    def _render_bundle(bundle_path: str, page_name: str, title: str) -> str:
        source = FileEventSource(bundle_path)
        run_data = source.read()
        # R3/F5: render the whole LOGICAL run when the bundle is one segment of a
        # suspended->resumed lifecycle — the report must show the merged truth, not the
        # half this bundle happens to be. Single-segment groups render identically rich.
        try:
            group = source.read_group(str(run_data.run_id))
        except FileNotFoundError:
            group = None
        if group is not None:
            (out_dir / page_name).write_text(
                observation_group_to_html(group, title=title), encoding="utf-8"
            )
            return str(out_dir / page_name)
        graph = build_observation_graph(
            run_data.definition,
            run_data.trace_events,
            run_data.usage_events,
            run_data.details,
            run_id=run_data.run_id,
        )
        save_observation_html(run_data.definition, graph, out_dir / page_name, title=title)
        return str(out_dir / page_name)

    rows = []
    for outcome in report.outcomes:
        viewer_rel = None
        if outcome.bundle_path:
            viewer_rel = f"{outcome.name}.html"
            outcome.viewer_html_path = _render_bundle(
                outcome.bundle_path, viewer_rel, outcome.name
            )
        for label, bundle in (outcome.linked_bundles or {}).items():
            page = f"{outcome.name}--{label}.html"
            outcome.linked_viewer_paths = {
                **(outcome.linked_viewer_paths or {}),
                label: _render_bundle(bundle, page, f"{outcome.name} — {label}"),
            }
        cost = (
            f"~${outcome.notional_usd:.4f} (subscription plan value)"
            if outcome.notional_usd is not None
            else ("UNKNOWN" if outcome.worker_calls else "none")
        )
        status_label = outcome.status.upper()
        if outcome.failure_class:
            status_label += f" ({outcome.failure_class})"
        links = [f'<a href="{viewer_rel}">bundle view</a>'] if viewer_rel else []
        links += [
            f'<a href="{outcome.name}--{label}.html">{label}</a>'
            for label in (outcome.linked_bundles or {})
        ]
        link = " · ".join(links) if links else "—"
        rows.append(
            f"<tr><td>{outcome.name}</td><td>{status_label}</td><td>{outcome.worker_calls}</td>"
            f"<td>{cost}</td><td>{outcome.duration_s or 0:.1f}s</td><td>{link}</td></tr>"
        )
    not_run = [n for n in report.config.scenarios if n not in {o.name for o in report.outcomes}]
    for name in not_run:
        rows.append(f"<tr><td>{name}</td><td>NOT RUN</td><td>0</td><td>none</td><td>—</td><td>—</td></tr>")
    honesty = "COMPLETED" if report.completed else f"STOPPED: {report.stopped_reason or 'unknown'}"
    total = (
        f"~${report.notional_usd_total:.4f}" if report.notional_usd_total is not None else "none recorded"
    )
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Engine subscription qualification</title></head>
<body>
<h1>Cross-consumer engine qualification</h1>
<p><b>{honesty}</b></p>
<p>model requested: <code>{report.identity.requested_model}</code> · CLI: <code>{report.identity.cli_version}</code>
 · commit: <code>{report.identity.git_commit}</code> · platform: {report.identity.platform}
 · started: {report.identity.started_at}</p>
<p>worker calls: {report.worker_calls_total} attempted ({report.failed_worker_calls_total} failed)
 · expected target: ≤{report.config.max_worker_calls} calls / ≤${report.config.suite_budget_usd:.2f}
 · documented emergency maximum: {report.config.emergency_max_worker_calls} calls /
 ${report.config.emergency_max_notional_usd:.2f}
 · subscription notional total: {total} (plan value — NEVER summed with metered API spend)</p>
<table border="1" cellpadding="6">
<tr><th>scenario</th><th>status</th><th>calls</th><th>notional cost</th><th>duration</th><th>observation</th></tr>
{chr(10).join(rows)}
</table>
<p>Raw data: <code>qualification-summary.json</code> + per-scenario observation bundles.</p>
</body></html>
"""
    index = out_dir / "index.html"
    index.write_text(html, encoding="utf-8")
    # Q-R6: the persisted manifest must carry the viewer paths the report just created —
    # re-persist AFTER generation so disk and memory agree.
    persist_suite_report(report)
    return str(index)


def export_evidence_and_clean_worktree(worktree: "str", destination: "str") -> str:
    """QRF.3c operational contract: export the paid evidence OUT of the disposable worktree
    and clean it for a NON-FORCED `git worktree remove` — with verification tied to the
    EXACT CURRENT SOURCE RUNS, never to whatever already sits in the destination.

    Order of guarantees (each failure leaves the source evidence untouched):
      1. every CURRENT source run must carry its own qualification-summary.json BEFORE any
         copy — a stale summary from an older exported run can never bless an incomplete one;
      2. the destination must lie OUTSIDE the worktree (no self-deletion trap) and each run
         copies to a FRESH directory — an existing run-id is a loud collision, never a merge;
      3. every copied summary is byte-compared against its source; only after ALL verify does
         the helper remove the staged .env and transient test output."""

    import shutil
    from pathlib import Path

    tree = Path(worktree).resolve()
    dest_root = Path(destination).resolve()
    if dest_root == tree or tree in dest_root.parents:
        raise QualificationError(
            f"destination {dest_root} lies inside the disposable worktree {tree} — the export "
            "would be destroyed by cleanup; choose a destination outside"
        )
    evidence = tree / "infra" / "test-results" / "engine-subscription-qualification"
    if not evidence.is_dir():
        raise QualificationError(f"no qualification evidence under {evidence}")
    source_runs = sorted(d for d in evidence.iterdir() if d.is_dir())
    if not source_runs:
        raise QualificationError(f"no qualification run directories under {evidence}")
    incomplete = [d.name for d in source_runs if not (d / "qualification-summary.json").is_file()]
    if incomplete:
        raise QualificationError(
            f"current source runs without their own qualification-summary.json: {incomplete} — "
            "refusing to copy or clean; the run is incomplete"
        )
    exported = dest_root / "engine-subscription-qualification"
    exported.mkdir(parents=True, exist_ok=True)  # the ROOT may exist; run dirs must not
    for run in source_runs:
        target = exported / run.name
        if target.exists():
            raise QualificationError(
                f"destination already contains run id {run.name!r} — evidence directories are "
                "never merged or overwritten; move the prior export aside first"
            )
        shutil.copytree(run, target)
    for run in source_runs:
        source_bytes = (run / "qualification-summary.json").read_bytes()
        copied = exported / run.name / "qualification-summary.json"
        if not copied.is_file() or copied.read_bytes() != source_bytes:
            raise QualificationError(
                f"exported summary for run {run.name!r} failed byte verification against its "
                "source — source evidence left untouched"
            )
    # only now is the current evidence demonstrably durable outside the worktree
    shutil.rmtree(tree / "infra" / "test-results", ignore_errors=True)
    (tree / ".env").unlink(missing_ok=True)
    return str(exported)
