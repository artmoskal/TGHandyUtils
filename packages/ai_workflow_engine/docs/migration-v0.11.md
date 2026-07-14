# Migration guide — `engine-v0.10.1` → v0.11 (RELEASE CANDIDATE — no tag yet)

**Status: unreleased candidate. There is no `engine-v0.11.0` tag — do not re-pin until it is cut.**
`engine-v0.10.1` remains the current release and the correct pin.

## Consumer action required: none

v0.11 is an internal refactor of `CapabilityRuntime.invoke` (the engine's one capability door).
The public surface is UNCHANGED and locked by sealed fixtures: exports, `CapabilityRuntime`
constructor/`invoke` signatures, attributes (`trace_sink`, `observation`, `registry`,
`detail_sink`), sink classes and their import paths, and the full JSON schemas (defaults and
constraints included) of `CapabilitySpec` / `CapabilityResult` / `CapabilityContext` /
`WorkflowArtifact`. No status literal, error text, trace/detail shape, usage event, budget rule,
process-settlement rule, or wait behavior changed.

What moved (internal modules only — never a consumer concept):

- pure input/output contract → `engine/capability_contract.py`;
- execution-window resolution, ambient nested-window scope, bounded awaiting, and cancellation
  containment → `engine/invocation_supervision.py`;
- start/terminal trace + detail projection → `engine/capability_observation.py`
  (still resolved from the runtime at call time, so replacing `runtime.trace_sink` or
  `runtime.observation` after construction keeps working — the shipped GoPro tee pattern).

`CapabilityRuntime.invoke` remains the only execution door; AST guards fail the suite if any
engine code calls a looked-up handler directly.

## How the "no behavior change" claim is verified

- A sealed 27-scenario preservation oracle (the plan's full behavior-freeze inventory: success,
  rejection, budget, timeout, caller/bounded cancellation, swallowed/unacknowledged cancellation,
  process timeout, artifacts, capture modes, sink failures, nested/concurrent isolation, usage
  events, authored fanout, durable suspend/resume) runs through the public door and must equal the
  `engine-v0.10.1` baseline path-by-path.
- The baseline is REAL, not hand-authored: the sha256-pinned v0.10.1 wheel is a committed fixture
  with a provenance record (tag, peeled commit, wheel/oracle/fixture hashes); the live
  baseline-vs-candidate differential runs hermetically inside the standard test wrapper.
- Consumer validation, if you want your own evidence when the tag ships: run
  `./test.sh unit -- --cov-fail-under=0 -q packages/ai_workflow_engine/tests/test_invocation_oracle.py packages/ai_workflow_engine/tests/test_invocation_differential_gate.py`
  from the repo, or re-run your product's canary suite against a wheel built from the tag —
  no adapter changes are expected.

This file will be finalized with the exact tag name, package versions, and release evidence when
the release gate (final review, qualification, packaging, tagging) completes. Until then it
documents candidate truth only.
