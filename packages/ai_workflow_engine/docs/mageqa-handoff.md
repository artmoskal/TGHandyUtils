# MageQA Engine Adoption Guide

Status: **ready for MageQA consumer validation against the immutable `engine-v0.10.0` tag.** The five E0 blockers that
failed MageQA against `engine-v0.9.2` — planned `partial` rewritten to `done` with the error
cleared, `RuntimeLimits.timeout_s` declared but not enforced, retrace provenance not exposed to the
retraced capability, the CLI request's hidden 600s default, and the reused `0.3.0` tools wheel
identity — are all **addressed in `engine-v0.10.0`** (tools `0.4.0`, viewer `0.2.2`). MageQA must
pin tag `engine-v0.10.0` from a clean checkout, rebuild wheels, and pass its E0 canaries before
migrating; it stays on its existing pin until those canaries pass.
Do not infer the actual MageQA pin from this document; the consumer repository's pin file is
authoritative for deployed state.

Read first: [getting started](getting-started.md), [framework concepts](concepts.md),
[operations](operations.md), and [misuse risks](misuse-risks.md).

Moving to `engine-v0.10.0`? Read [migration-v0.10](migration-v0.10.md) — the `partial` timeout/plan
contract, enforced `RuntimeLimits.timeout_s`, interruptibility declaration, and the dropped CLI
600s default are all listed there. Coming from `engine-v0.7.0`/`v0.8.1`, also read
[migration-v0.9](migration-v0.9.md) first.

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
| `ai-workflow-engine==0.10.0` | Required orchestration/runtime |
| `ai-workflow-tools==0.4.0` | CLI agents, `claude -p`/`codex exec`, tool catalog, media helpers |
| `ai-workflow-viewer==0.2.2` | Low-level engine run investigation and bundle rendering |

The MageQA Next.js dashboard remains product-owned. It may link/embed/project engine bundle data, but
it must not create a duplicate trace or workflow runtime.

## Ownership Boundary

### Engine owns

- workflow execution, bounded planning, branch/evaluate/retrace/fallback, fan-out, waits;
- model/tool policy, side-effect gates, budgets, cost classes, replay mechanics;
- observation bundle lifecycle, prompts/responses/tool details, evidence manifests;
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
   separately.
9. **Safety:** prohibited external effects are denied before handler spawn.
10. **Observation/dashboard:** a real audit exposes plans, prompts, tools, screenshots, costs,
    blockers, findings, and report without a duplicate runtime.
11. **Report order:** pitch/media inputs derive only from the completed verified report.
12. **Replay:** a known-flow fixture runs without LLM calls and produces a meaningful diff.
13. **Runtime truth:** a slow browser/CLI subprocess is stopped and reaped inside the engine window,
    retains partial evidence, and renders work/cleanup/settlement; a sync handler under the same
    finite profile is refused before invocation rather than pretending to be bounded.

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
