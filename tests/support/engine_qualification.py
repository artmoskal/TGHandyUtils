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
    return QualificationRunIdentity(
        requested_model=requested_model,
        cli_version=read_version(),
        git_commit=read_commit(),
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
    """A scenario's semantic invariant failed (engine-meaningful, not infrastructure)."""


KNOWN_SCENARIOS = (
    "anki_basic_card",
    "mageqa_local_audit",
    "gopro_frame_inspection",
    "slackazz_triage",
)


class QualificationConfig(BaseModel):
    """One validated source for model, caps, output dir, and scenario selection (Q1.1)."""

    model_config = ConfigDict(extra="forbid")

    model: str = "sonnet"
    live: bool = False
    per_invocation_budget_usd: float = 0.10
    suite_budget_usd: float = 0.50
    max_worker_calls: int = 6
    per_invocation_timeout_s: float = 180.0
    suite_timeout_s: float = 900.0
    output_dir: str
    scenarios: tuple = KNOWN_SCENARIOS

    @field_validator(
        "per_invocation_budget_usd", "suite_budget_usd", "per_invocation_timeout_s", "suite_timeout_s"
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
    worker_calls: int = 0
    duration_s: Optional[float] = None
    bundle_path: Optional[str] = None
    viewer_html_path: Optional[str] = None
    detail: str = ""


class SuiteReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: QualificationRunIdentity
    config: QualificationConfig
    outcomes: list = []
    notional_usd_total: Optional[float] = None
    worker_calls_total: int = 0
    stopped_reason: Optional[str] = None
    completed: bool = False


def run_qualification_suite(
    config: QualificationConfig,
    scenario_runners: "dict[str, Any]",
    *,
    identity: QualificationRunIdentity,
    clock: Optional[Any] = None,
) -> SuiteReport:
    """Sequential fail-fast orchestration + notional-cost ledger (Q1.2).

    The ledger writes the partial manifest to disk BEFORE deciding whether another
    (potentially paid) scenario may start, and stops on: semantic failure, unknown
    notional cost in live mode, suite budget ceiling, suite timeout, or worker-call
    ceiling. It never skips a configured scenario silently."""

    import asyncio
    import time as _time
    from pathlib import Path

    now = clock or _time.monotonic
    started = now()
    report = SuiteReport(identity=identity, config=config)
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _persist() -> None:
        known = [o.notional_usd for o in report.outcomes if o.notional_usd is not None]
        report.notional_usd_total = round(sum(known), 6) if known else None
        report.worker_calls_total = sum(o.worker_calls for o in report.outcomes)
        (out_dir / "qualification-summary.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )

    def _spent() -> float:
        return sum(o.notional_usd or 0.0 for o in report.outcomes)

    for name in config.scenarios:
        if name not in scenario_runners:
            report.stopped_reason = f"no runner wired for scenario {name!r}"
            _persist()
            raise QualificationError(report.stopped_reason)
        elapsed = now() - started
        if elapsed >= config.suite_timeout_s:
            report.stopped_reason = f"suite timeout ({elapsed:.0f}s >= {config.suite_timeout_s:.0f}s)"
            _persist()
            return report
        if _spent() >= config.suite_budget_usd:
            report.stopped_reason = (
                f"suite budget ceiling reached (${_spent():.4f} >= ${config.suite_budget_usd:.2f})"
            )
            _persist()
            return report
        if report.worker_calls_total >= config.max_worker_calls:
            report.stopped_reason = (
                f"worker-call ceiling reached ({report.worker_calls_total} >= {config.max_worker_calls})"
            )
            _persist()
            return report

        try:
            outcome = asyncio.run(scenario_runners[name](config))
        except Exception as exc:
            report.outcomes.append(
                ScenarioOutcome(
                    name=name, status="failed", failure_class="provider", detail=str(exc)[:500]
                )
            )
            report.stopped_reason = f"scenario {name!r} raised: {exc}"
            _persist()
            raise
        report.outcomes.append(outcome)
        _persist()  # result on disk BEFORE the next paid call may start

        if outcome.status != "passed":
            report.stopped_reason = f"scenario {name!r} failed ({outcome.failure_class}): {outcome.detail}"
            _persist()
            return report
        if config.live and outcome.worker_calls > 0 and outcome.notional_usd is None:
            outcome.failure_class = "unknown_cost"
            outcome.status = "failed"
            report.stopped_reason = (
                f"scenario {name!r} reported UNKNOWN notional cost in live mode — "
                "the ledger cannot bound spend, stopping"
            )
            _persist()
            return report

    report.completed = report.stopped_reason is None
    _persist()
    return report


# ====================================================================== Q3.1: visual report


def render_suite_report_html(report: SuiteReport) -> str:
    """Write per-scenario viewer HTML + one index.html; returns the index path (Q3.1)."""

    from pathlib import Path

    from ai_workflow_viewer import (
        FileEventSource,
        build_observation_graph,
        save_observation_html,
    )

    out_dir = Path(report.config.output_dir)
    rows = []
    for outcome in report.outcomes:
        viewer_rel = None
        if outcome.bundle_path:
            run_data = FileEventSource(outcome.bundle_path).read()
            graph = build_observation_graph(
                run_data.definition,
                run_data.trace_events,
                run_data.usage_events,
                run_data.details,
                run_id=run_data.run_id,
            )
            viewer_rel = f"{outcome.name}.html"
            save_observation_html(
                run_data.definition, graph, out_dir / viewer_rel, title=outcome.name
            )
            outcome.viewer_html_path = str(out_dir / viewer_rel)
        cost = (
            f"~${outcome.notional_usd:.4f} (subscription plan value)"
            if outcome.notional_usd is not None
            else ("UNKNOWN" if outcome.worker_calls else "none")
        )
        status_label = outcome.status.upper()
        if outcome.failure_class:
            status_label += f" ({outcome.failure_class})"
        link = f'<a href="{viewer_rel}">bundle view</a>' if viewer_rel else "—"
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
<p>worker calls: {report.worker_calls_total}/{report.config.max_worker_calls}
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
    return str(index)
