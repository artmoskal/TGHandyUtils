"""Q4: LIVE cross-consumer engine qualification over the claude -p subscription.

Run (double opt-in, both REQUIRED — either missing means this module SKIPS):

    ALLOW_PAID_TESTS=1 RUN_ENGINE_SUBSCRIPTION_QUALIFICATION=1 \
      ./test.sh integration -- --cov-fail-under=0 -q \
      tests/integration/test_engine_subscription_qualification.py

Caps (settled in docs/_discussion/2026-07-11-engine-cross-consumer-live-qualification-plan.md):
model alias `sonnet`, $0.10/invocation via `--max-budget-usd`, $0.50 suite ceiling, 6 worker
calls, 180 s/invocation, 15 min suite. Outputs (manifest, four bundles, viewer pages,
index.html) land under infra/test-results/engine-subscription-qualification/<run-id>/.

After the opt-in, a configured-but-unavailable provider FAILS the run — never skips.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_OPTED_IN = (
    os.getenv("ALLOW_PAID_TESTS") == "1"
    and os.getenv("RUN_ENGINE_SUBSCRIPTION_QUALIFICATION") == "1"
)


@pytest.mark.skipif(
    not _OPTED_IN,
    reason=(
        "live subscription qualification needs BOTH ALLOW_PAID_TESTS=1 and "
        "RUN_ENGINE_SUBSCRIPTION_QUALIFICATION=1"
    ),
)
def test_live_cross_consumer_subscription_qualification():
    from tests.support.engine_qualification import (
        QualificationConfig,
        capture_run_identity,
        render_suite_report_html,
        run_qualification_suite,
    )
    from tests.support.engine_qualification_scenarios import SCENARIO_FUNCTIONS

    # Opted in: the provider must actually be there — fail loudly, never skip.
    if shutil.which("claude") is None:
        raise AssertionError("live opt-in but no `claude` binary on PATH — refusing to skip")
    if not (os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")):
        raise AssertionError("live opt-in but no claude auth in the environment")

    identity = capture_run_identity("sonnet")
    run_id = f"{identity.started_at.replace(':', '').replace('+0000', 'Z')}-{identity.git_commit}"
    output_dir = Path("infra/test-results/engine-subscription-qualification") / run_id
    workdir = output_dir / "work"
    workdir.mkdir(parents=True, exist_ok=True)

    config = QualificationConfig(live=True, output_dir=str(output_dir))

    def provider_factory(scenario: str, role: str):
        from ai_workflow_tools.cli_agents import ConsoleLLMClient, claude_p

        return ConsoleLLMClient(
            claude_p,
            timeout_s=config.per_invocation_timeout_s,
            subscription_mode=True,
            cli_max_budget_usd=config.per_invocation_budget_usd,
        )

    runners = {
        name: (lambda cfg, fn=fn: fn(cfg, provider_factory, workdir))
        for name, fn in SCENARIO_FUNCTIONS.items()
    }

    report = run_qualification_suite(config, runners, identity=identity)
    index = render_suite_report_html(report)

    print(f"\nqualification report: {index}")
    print(f"summary json: {output_dir / 'qualification-summary.json'}")
    for outcome in report.outcomes:
        print(
            f"  {outcome.name}: {outcome.status} calls={outcome.worker_calls} "
            f"notional=${outcome.notional_usd if outcome.notional_usd is not None else '?'} "
            f"bundle={outcome.bundle_path}"
        )

    assert report.completed, f"live qualification stopped: {report.stopped_reason}"
    assert [o.status for o in report.outcomes] == ["passed"] * 4
    assert report.worker_calls_total <= config.max_worker_calls
    assert report.notional_usd_total is not None, "live run must record real notional cost"
    assert report.notional_usd_total < config.suite_budget_usd
