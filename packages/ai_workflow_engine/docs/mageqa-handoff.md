# MageQA Engine Adoption Guide

Status: **`engine-v0.11.6` is the current immutable release.**
The pending v0.11.7 candidate closes MageQA's CLI economics request. Do not repin until its
immutable tag and complete `0.11.7 / 0.5.2 / 0.3.2` release directory exist and the E0 canary below
passes.
MageQA's two durable-registration race canaries fail on `engine-v0.11.5`. MageQA must stay on its
existing pin and keep its fork until the v0.11.6 E0
canaries pass against the immutable tag. The five historical E0 blockers (planned
`partial` rewritten to `done`, unenforced `RuntimeLimits.timeout_s`, hidden retrace provenance,
the CLI 600s default, and the reused tools wheel identity) are addressed and carried forward;
v0.11 additionally hardens every persisted contract (strict versioned snapshot, bundle meta v2,
wait records) with NO migration layer. To adopt, MageQA must pin tag `engine-v0.11.6`,
verify the complete published release directory, install the required wheels, and pass its E0 canaries before
adopting; it stays on its existing pin until those canaries pass.
Do not infer the actual MageQA pin from this document; the consumer repository's pin file is
authoritative for deployed state.

Read first: [getting started](getting-started.md), [framework concepts](concepts.md),
[operations](operations.md), and [misuse risks](misuse-risks.md).

The line is **latest-only** — no migration guides exist. Adopt the current contract fresh; data
written under older tags is rejected loudly and stays inspectable with its matching historical tag.

## Product Outcome

MageQA is an AI-first scenario tester. A user supplies a target and broad or narrow natural-language
scenarios. MageQA gathers cheap surface facts, plans bounded instruments, executes browser/vision/
probe work, verifies facts, produces findings, then derives reports and optional pitch/design media
from grounded evidence.

Canonical machine:

```text
target + scenarios
  -> surface/safety facts
  -> AI strategy plan
  -> bounded fan-out over browser, vision, probes, platform packs, replay
  -> fact claims
  -> verify: confirmed | refuted | inconclusive
  -> finding candidates -> dedupe/adjudicate -> findings
  -> full report
  -> optional pitch/deck/video/redesign under separate budgets and human gates
```

The full report precedes sales artifacts. Inconclusive evidence becomes a coverage note/blocker, not
a defect.

## Package Choice

| Package | MageQA use |
|---|---|
| `ai-workflow-engine==0.11.7` | Required orchestration/runtime |
| `ai-workflow-tools==0.5.2` | CLI agents, `claude -p`/`codex exec`, tool catalog, media helpers |
| `ai-workflow-viewer==0.3.2` | Low-level engine run investigation and bundle rendering |

> **Candidate matrix:** `engine-v0.11.7` + `ai-workflow-tools==0.5.2` +
> `ai-workflow-viewer==0.3.2` (tools/viewer require
> `ai-workflow-engine>=0.11.7,<0.12`). Do not install this matrix until `engine-v0.11.7` is cut.
> The previous line remains available at its historical tag for historical data; the lines never
> mix in one environment.


The MageQA Next.js dashboard remains product-owned. It may link/embed/project engine bundle data, but
it must not create a duplicate trace or workflow runtime.

## Ownership Boundary

### Engine owns

- workflow execution, bounded planning, branch/evaluate/retrace/fallback, fan-out, waits;
- model/tool policy, side-effect gates, budgets, cost classes, replay mechanics;
- observation bundle lifecycle, prompts/responses/tool details, evidence manifests;
- caller-cancellation finalization and retention of already-completed capability artifacts;
- validated authored flows over already registered capabilities.

### MageQA owns

- browser/MCP sessions, safe public probing, rubrics, platform packs, and scenario semantics;
- `Observation -> FactClaim -> FindingCandidate -> Finding` schemas and verification policy;
- target/finding/learned-flow storage and product dashboard;
- report, pitch, video, and redesign domain workflows;
- outreach approval and final external delivery.

## One Provider Door

Create API/CLI/browser clients through one MageQA composition/factory surface and inject them into
capabilities or `ai_workflow_tools` adapters. A direct client in a workflow module bypasses profile
routing, usage, capture, timeout, and test substitution.

Text-only `claude -p` completions are tool-free. Browser/vision episodes receive explicit/scoped
tools and matching side-effect declarations. Never silently fall back from a failed browser episode
to parser-only subjective UX findings.

## Product Models

MageQA should define strict product models resembling:

```text
ObservationBundleRef
FactClaim(status=confirmed|refuted|inconclusive, evidence_refs=[...])
FindingCandidate(claim_refs=[...], severity, rationale)
Finding(verified_claim_refs=[...], evidence_refs=[...])
CoverageNote(reason, attempted_instruments, evidence_refs=[...])
```

These models stay out of engine core. They are capability input/output schemas, making every
conversion visible in trace and testable independently.

## Workflow Construction

- Broad user scenarios go to a structured planner capability with explicit tools, limits, surface
  facts, memory, and budget.
- Planner output selects registered capabilities and bounded work items; it does not invent code.
- Deterministic probes supply objective facts, not subjective UX conclusions.
- Browser/vision agents produce evidence-linked observations.
- Verification and finding adjudication are explicit nodes, not report-prompt instructions.
- Report/pitch/video/redesign are subworkflows or registered capabilities after findings are stable.
- Public-facing artifacts and outreach sit behind top-level human gates.

`FlowArtifact` v1.5a can author bounded step/branch/evaluate/fan-out machines over registered
capabilities. It cannot author arbitrary tools, human/subworkflow nodes, or general process code.
Start migration with hand-declared workflows; adopt authored flow only for a proven scenario-planning
need.

## Memory And Replay

MageQA implements its durable SQLite/other `MemoryStore` backend for:

- target profile;
- verified finding history and prior false positives;
- learned successful flows;
- flaky checks and planning hints.

Memory is evidence-linked and non-authoritative. First audits may be AI-heavy; known-client runs can
use recorded/replay plans and compare evidence to baselines with zero LLM calls where possible.

## Safety Boundary

MageQA capabilities must enforce product policy before spawn:

- public surface only unless a separately approved authenticated test profile exists;
- no login bypass, brute force, account creation, payment submission, CAPTCHA solving, or hidden
  destructive actions;
- robots/rate limits and probe budgets are explicit configuration;
- external writes/outreach default denied;
- browser unavailability or MCP startup failure is a visible blocker.

The engine enforces declared side-effect classes; MageQA must declare the real effects honestly.

## MageQA-Specific Misuse Risks

- Do not rebuild an `audit_site` supervisor loop around the engine.
- Do not treat HTML/DOM heuristics as proof of visual defects.
- Do not emit a finding directly from one unverified agent statement.
- Do not generate a pitch independently from the full verified report.
- Do not store screenshots or transcripts as raw bytes in engine state.
- Do not let the product dashboard become the source of run status or cost truth.
- Do not use authored flow as permission for arbitrary generated pipelines.
- Do not hide provider/tool failures behind a parser fallback.
- Do not catch `CancelledError` to create a MageQA-owned replacement bundle; await engine cleanup
  and use the finalized `cancelled` segment.

## Adopter Contract AC

MageQA adoption is complete when its repository proves:

1. **Pin integrity:** engine/tools/viewer versions, commit, and wheel hashes are recorded.
2. **One provider door:** no workflow module constructs API/CLI/browser clients directly.
3. **No product orchestration loop:** canonical audits start at `engine.run` and engine owns fan-out,
   retries, waits, and status.
4. **Scenario planning:** broad and narrow fixtures produce bounded structured strategies.
5. **Grounding:** visual findings require real image evidence consumed by a vision worker.
6. **Fact ledger:** refuted/inconclusive claims cannot become final findings.
7. **Partial isolation:** one failed instrument remains visible without erasing successful evidence.
8. **Cost honesty:** metered, subscription-notional, unknown, retries, and remaining budget project
   separately. For CLI workers, assert Claude provider-reported notional and Codex normalized
   uncached/cache/reasoning quantities plus configured catalog/rate versions survive result,
   cancellation, bundle/group read, and viewer. Reasoning is a subset of output and is not priced
   twice.
9. **Safety:** prohibited external effects are denied before handler spawn.
10. **Observation/dashboard:** a real audit exposes plans, prompts, tools, screenshots, costs,
    blockers, findings, and report without a duplicate runtime.
11. **Report order:** pitch/media inputs derive only from the completed verified report.
12. **Replay:** a known-flow fixture runs without LLM calls and produces a meaningful diff.
13. **Runtime truth:** a slow browser/CLI subprocess is stopped and reaped inside the engine window,
    retains partial evidence, and renders work/cleanup/settlement; a sync handler under the same
    finite profile is refused before invocation rather than pretending to be bounded.
14. **Caller-cancellation truth:** cancelling fresh and resumed runs re-raises the original
    `CancelledError`, finalizes the segment as `cancelled`, preserves prior trace/detail/usage and
    completed direct/fan-out/child artifacts, and keeps archive failure loud.
15. **Durable-registration exposure truth:** cancellation after registration commit and
    registration-acknowledgement loss both expose no handle and leave no executable hidden
    continuation; abrupt process death plus an exact retry recovers the same stored receipt, while
    cancellation during an ownership-ambiguous retry fails loudly rather than revoking a handle
    that another caller may already hold or reporting false clean settlement.
16. **Subscription pricing truth:** one real or hermetic Codex JSONL completion uses the final
    cumulative `turn.completed`, preserves response text from the result file, and records one
    notional event. Missing/malformed usage or an unmatched model remains typed unknown. The
    notional is API-equivalent plan value, never billed spend or a substitute for call/token/time
    caps.

Engine-side examples: `ai_workflow_engine.examples.run_toy_site_audit_pilot` and the paid
qualification's MageQA authored-flow scenario.

## Known Engine Limits Relevant To MageQA

- Nested human suspension inside subworkflows is rejected; use top-level gates.
- Authored subworkflow/general I/O mapping remains deferred.
- Durable semantic/vector memory backend is product-owned/deferred; the seam is current.
- Token-level streaming is a client concern, not an engine-core event stream.
- Parent edges for out-of-definition fan-out/provenance activity are a viewer follow-up; current UI
  labels the activity without inventing wiring.
- Python cannot kill cancellation-resistant in-process code. MageQA browser/CLI episodes that need
  a hard stop must use `CliAgentCapability`/`ExternalProcessCapability`, not a synchronous wrapper.

If one of these blocks a concrete workflow, use [the framework request lifecycle](extension-lifecycle.md)
with a scenario and acceptance proof. Upgrade via [`operations.md`](operations.md#upgrade-lifecycle).


## v0.11 Release Contract

`engine-v0.11.6` is the current immutable release for the **latest-only** line. The line uses strict versioned persisted
contracts (snapshot `v0.11`, bundle meta v2, wait records `wait-v2`), one strict viewer loader, and
NO migration layer — the sealed current-contract corpus shows 0 behavior deltas vs v0.10.1, but old
persisted data is rejected loudly naming its historical tag. Adopt by re-pinning fresh
(pin tag `engine-v0.11.6`, verify the published `release-manifest-v2` bundle, then install engine
`0.11.6`, tools `0.5.1`, and viewer `0.3.1`) and
re-run your canaries before changing any deployed pin.
`wait-v1` records written by v0.11.5 are non-current: settle or discard them under v0.11.5 and
start a new coordinator namespace before v0.11.6 writes `wait-v2`.

The release supersedes exactly the two changes declared in MageQA's v0.11.1 fork. Engine
`0.11.4` retains the stronger planner-output merge at both loss surfaces from `0.11.3` and adds
the public `RunExecutionRequest`: callers can narrow one complete run without mutating cached
profiles, the effective limit survives local suspend/resume, retraces share the same monotonic
deadline, and graph cleanup is contained inside the declared wall time. Terminal run status and
errors now follow the latest attempt per node while preserving all attempts in history. Tools
`0.5.1` attaches
staged `ImageInput` files to `codex exec` with native `--image`; Claude keeps the separate
path-scoped `Read(./inputs/**)` transport. It also closes context-binding parity: human and fan-out
capability invocations consume all four declared bindings, while subworkflows consume plan/machine
cards and reject capability-only memory/model settings. The release additionally makes
retrace provenance target-only (a retraced parent never leaks round data into child-workflow
capabilities — a child planner cannot mistake its first local run for a follow-up round) and makes
`model_binding` trace truth invocation-local (concurrent fan-out items each attribute the model
THEY invoked, keyed by `fanout_item_index`; aggregate billing/budget behavior is unchanged). MageQA removes its patch only after the
annotated tag, source commit, published wheel hashes, engine canaries, the recorded Codex image semantic proof, and E0 gate
are recorded.

The release also closes MageQA's caller-cancellation blocker. Every normalized capability result
publishes its artifacts to the active run session before graph-state aggregation. Cancelling
`engine.run()` or `engine.resume()` then finalizes the active observation segment as `cancelled`
with completed evidence and re-raises the original `CancelledError`. This applies to direct steps,
completed fan-out siblings, declared child workflows, registered workflow capabilities, and
resumed runs. MageQA's E0 gate must execute its public
`test_operator_cancellation_finalizes_bundle_and_preserves_completed_artifacts` canary against the
installed immutable release; no downstream bundle owner or fork is accepted.

The same E0 gate must run the two durable-registration race canaries from MageQA's
`2026-07-19-engine-durable-wait-registration-atomicity-handoff.md`: cancellation after coordinator
commit and registration acknowledgement loss. Both must leave the caller without a handle and the
stored continuation non-executable. Delivery always receives the complete exposed `WaitHandle`;
MageQA must not reconstruct one from `wait_id`.
