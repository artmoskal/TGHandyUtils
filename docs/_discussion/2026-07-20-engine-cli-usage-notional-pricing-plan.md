# Engine CLI Usage And Notional Pricing Plan

Date: 2026-07-20
Branch: `polish/workflow-engine`
Baseline: local immutable tag `engine-v0.11.6` at `266d2cf`
Target: fix-forward latest-only release `engine-v0.11.7` with package matrix
`ai-workflow-engine==0.11.7`, `ai-workflow-tools==0.5.2`,
`ai-workflow-viewer==0.3.2`
Implementation owner: Codex, end-to-end for both iterations
Independent reviewer: Claude, once after each complete iteration gate, unless explicitly waived by
the release owner
Status: **RELEASED LOCALLY; IMMUTABLE TAG AND VERIFIED RELEASE BUNDLE COMPLETE; NOT PUSHED OR UPLOADED**

Primary consumer request:
`/Users/artemm/PycharmProjects/MageQA/docs/_discussion/2026-07-20-engine-cli-usage-and-notional-pricing-request.md`

Permanent authorities:

- `docs/executable-workflow-engine-spec.md`
- `docs/workflow-engine-architecture.md`
- `packages/ai_workflow_engine/docs/operations.md`
- `packages/ai_workflow_engine/docs/misuse-risks.md`

Testing rule: every repository test command uses `./test.sh` from the repository root. Direct
`pytest`, `python -m pytest`, and direct Docker Compose test commands are forbidden.

## Why We Need This

The engine owns subscription CLI execution, but tools `0.5.1` throws away Codex's structured token
usage. Both Codex command builders omit `--json`; the shared result-file parser consequently keeps
only the final response text and records a `subscription_notional` event with zero tokens and
`cost_known=false`. Claude already reports usage and provider-computed `total_cost_usd`.

This is not an infinite-spend bug: execution windows and call-count/worker-count caps still bind.
It is a cost-truth bug. MageQA cannot distinguish an inexpensive Codex call from an expensive one,
cannot explain cache/reasoning consumption, and cannot project observed notional economics without
adding a product-owned parser and price table. That would violate the one-provider-door and
engine-owned accounting architecture.

If this is not fixed:

- Codex subscription calls remain economically invisible even when the CLI reports exact counters;
- the viewer and observation bundles honestly say "unknown" but omit available facts;
- MageQA must keep its adoption gate blocked or create a forbidden duplicate accounting runtime;
- timeout/cancellation can lose already-emitted usage even though v0.11.6 otherwise preserves
  cancellation evidence.

## What We Need

Deliver one immutable latest-only release that:

1. runs every supported Codex execution door with `--json` while retaining
   `--output-last-message` as canonical response text;
2. parses the final valid `turn.completed.usage` JSONL event without summing cumulative events or
   reading stderr as protocol data;
3. normalizes provider counters into one typed engine usage contract, preserving raw counter
   semantics and rejecting impossible relationships;
4. prices subscription usage through one injected, versioned engine policy;
5. records provider-reported, configured-public-rate, configured-proxy-rate, and unknown pricing
   truth as typed data;
6. preserves the latest available usage on success, provider failure, timeout, and caller
   cancellation;
7. carries the same truth through returned results, the canonical usage event, cumulative summary,
   suspension snapshot/resume, observation bundle/grouped read, and viewer;
8. keeps metered spend and subscription notional strictly separate;
9. leaves process ownership, kill/reap, execution-window, image transport, artifacts, and provider
   result parsing behavior unchanged;
10. updates the complete current package matrix and MageQA handoff only after all gates pass.

### Consumer-contract correction discovered during implementation

The original plan accidentally omitted requirements 10-13 from the MageQA request. They are
release-blocking and are now binding:

11. Every engine-owned provider attempt receives one opaque invocation id before the provider is
    called. The same typed id must be present on its usage event, request and response/tool-result
    trace events, captured prompt and response/tool-result details, and process settlement metadata.
12. Subscription CLI attempts record elapsed time from the engine-owned process boundary. Provider
    duration may be retained as a separate provider fact, but it must not replace missing wall-clock
    settlement time.
13. A trace whose detail body is unavailable records one closed typed reason
    (`capture_mode_off`, `detail_sink_unavailable`, or `projection_failed`) rather than forcing a
    consumer to infer absence from an empty reference list.
14. Finalization and bundle read validate invocation linkage. Removing or changing one linked id
    must fail loudly; consumers never reconstruct calls from node names, timestamps, or adjacency.

These requirements use existing owners. They do not add an executor branch, provider parser in the
viewer, or product-specific accounting path.

## How We Should Implement

### Ownership

Use the existing event pipeline instead of adding a second accounting loop:

```text
Codex/Claude CLI stdout
  -> ai_workflow_tools provider parser/accumulator
  -> WorkflowUsageEvent with normalized token facts
  -> engine record_usage_event (single enrichment/aggregation/persistence door)
  -> injected NotionalPricingPolicy
  -> typed pricing result + notional_usd
  -> summary / snapshot / bundle / grouped read / viewer
```

- **`ai_workflow_tools` owns provider protocol:** argv flags, Claude envelope fields, Codex JSONL
  event shapes, raw-counter semantics, and incremental extraction.
- **`ai_workflow_engine` owns normalized accounting and pricing:** typed quantities, catalog
  validation, model/rate resolution, pricing source, unknown reasons, event enrichment, aggregation,
  and budgets.
- **`ai_workflow_viewer` only projects persisted engine truth:** it never recalculates prices or
  interprets provider JSON.
- **Products own catalog selection/configuration:** they may inject approved public or proxy rates;
  they do not parse CLI output or calculate costs.

### Typed contracts

Add a dependency-light usage/pricing contract owner rather than growing `models.py` or duplicating
dictionaries. Exact names may change during implementation, but the semantics are binding:

- `NormalizedTokenUsage`
  - non-negative uncached input;
  - cache-read input;
  - cache-created input;
  - non-reasoning output;
  - reasoning output;
  - provider raw input/output/total counters;
  - closed raw-counter schema such as `codex_inclusive` or `claude_disjoint_cache`;
  - validator enforcing raw/normalized consistency.
- `NotionalRate`
  - provider/model match;
  - finite non-negative per-million uncached-input, cached-input, cache-creation, and output rates;
  - source: `configured_public_rate` or `configured_proxy_rate`;
  - catalog/rate version and currency.
- `NotionalPricingResult`
  - known result: finite non-negative amount, source, version, quantities, and rates used;
  - unknown result: no amount and one closed unknown reason;
  - provider-reported Claude cost is represented as `provider_reported`, never as metered spend.
- `NotionalPricingPolicy` protocol
  - pure synchronous pricing of one normalized usage record;
  - injected once into the engine/runtime;
  - default implementation backed by a strict versioned catalog;
  - no provider calls, clocks, file reads, or product imports.

`WorkflowUsageEvent` remains the canonical persisted event and gains typed normalized/pricing
fields. Existing scalar totals remain useful aggregate projections, not an alternate pricing
contract. Remove stringly `metadata["cost_known"]` / `metadata["cost_source"]` as accounting
authority; viewer grouping and tests must derive known/unknown/source from the typed result. Do not
add a compatibility reader or dual schema.

### Request correction: invalid counters

The MageQA request uses `max(input-cached, 0)` in explanatory pseudocode. The implementation must
not clamp corrupt counters:

- `cached_input_tokens > input_tokens` under Codex's inclusive schema is invalid;
- `reasoning_output_tokens > output_tokens` is invalid;
- negative/non-integral/non-finite counters are invalid;
- inconsistent `total_tokens` is invalid when the provider supplies it.

Invalid usage produces an explicit unknown pricing result with a named reason and preserves the raw
diagnostic safely. It never creates a plausible zero/discounted price.

### Pricing rules

For valid Codex inclusive counters:

```text
uncached_input = input_tokens - cached_input_tokens
non_reasoning_output = output_tokens - reasoning_output_tokens

notional =
    uncached_input * uncached_input_rate
  + cached_input_tokens * cached_input_rate
  + output_tokens * output_rate
```

Rates are per million tokens, so divide the sum by `1_000_000`. Reasoning is displayed separately
but not added to `output_tokens` again. Claude cache-read and cache-creation quantities stay
separate. A provider-reported Claude `total_cost_usd` wins over configured calculation and is
clearly labeled `provider_reported`.

Do not round event-level arithmetic to six decimals: small calls must not become fake `$0.000000`.
Round only presentation or documented aggregate output. Use a deterministic decimal calculation or
equivalent exact rate arithmetic, then serialize one stable numeric representation.

### Configuration and injection

Add a first-class strict `pricing:` section to `WorkflowConfigBundle`, plus
`WorkflowEngineBuilder.with_notional_pricing_policy(...)` for programmatic composition. The builder
constructs one default catalog policy when no explicit policy is supplied. The policy is threaded
through `WorkflowRunner` into `WorkflowUsageContext`; `record_usage_event` applies it before capture,
summary aggregation, sink fanout, logs, and budget checks.

This preserves the existing invariant that capture, result ledger, bundle JSONL, and summary see
the same enriched event object. It also keeps simple consumers simple: no pricing config is needed
for known default public models, and unmatched models remain explicit unknowns.

The typed `pricing:` section replaces the old host-config
`WORKFLOW_MODEL_PRICE_OVERRIDES_JSON`/`workflow_model_price_overrides_json` path. Remove that
attribute read, root-app setting/override mapping, tests, and permanent documentation in the same
iteration. Do not retain a fallback, deprecation branch, dual source, or compatibility converter:
two price authorities would make the displayed catalog version untrustworthy.

Configuration must reject:

- unknown keys;
- blank catalog/rate versions or model names;
- duplicate/ambiguous matches;
- invalid currencies;
- negative, infinite, or NaN rates;
- a proxy rate mislabeled as public;
- model-less provider usage when no provider-reported total exists.

No secrets belong in the catalog.

### Process/cancellation settlement

Success, provider failure, and timeout already return bounded stdout to the tools layer. Caller
cancellation is different: `ExternalProcessCapability` kills/reaps the process, settles stream
readers, and re-raises `CancelledError` without returning output.

Do not weaken that behavior or replace the original cancellation. Add one generic, bounded
per-request stdout observation seam at the process-I/O owner:

- the observer receives bounded chunks while the existing collector continues draining;
- it is synchronous/non-blocking and stores only the latest valid usage event plus a bounded
  partial-line buffer;
- observer failure cannot stop pipe draining or leave a child alive;
- tools' Codex accumulator uses the seam; unrelated external processes pay only one `None` check;
- on cancellation, each CLI door records the latest completed usage once, marks the attempt
  incomplete/cancelled, then re-raises the original `CancelledError`;
- if no complete usage event arrived, the event is explicit unknown rather than zero;
- engine cancellation bundle finalization from v0.11.5/0.11.6 remains the single lifecycle owner.

The incremental JSONL parser must tolerate arbitrary chunk boundaries, UTF-8 boundaries, unrelated
events, non-JSON lines, and a bounded oversized line. It must take the final valid
`turn.completed`, never sum events, and never parse stderr protocol noise.

### Failure behavior

- Missing `turn.completed`: successful response remains usable; pricing is explicit unknown with
  `usage_event_missing`.
- Malformed final usage: response/failure truth remains unchanged; pricing is explicit unknown with
  a typed reason.
- Unknown model/rate: tokens persist; notional remains `None`; reason names catalog miss.
- Provider failure/timeout after usage: one failed/incomplete usage event retains the latest
  counters and price.
- Caller cancellation after usage: one cancelled/incomplete usage event is persisted before the
  original cancellation is re-raised.
- Pricing-policy bug or invalid injected result: fail loudly before partial persistence; do not
  persist one event shape to the summary and another to the bundle.
- Subscription notional never debits `max_estimated_usd` or per-call metered ceilings. Call/token/
  worker/time limits remain the hard safety controls.

## Prerequisite And User-Involvement Gate

| Prerequisite | Owner | Status | Why | Fallback |
|---|---|---|---|---|
| Local source/tag `engine-v0.11.6` | implementation owner | ready | immutable regression baseline | stop if tag/HEAD identity changes unexpectedly |
| Docker test environment via `./test.sh` | implementation owner | ready; escalation may be needed for Docker socket | binding test route | record exact environment blocker; do not use direct pytest |
| Installed `codex exec` with `--json` and `--output-last-message` | implementation owner | verified locally on CLI `0.144.6`; recheck at implementation | confirms supported argv composition | fixtures cover code; live canary remains blocked |
| Claude/Codex authenticated subscription CLIs | user/environment | not needed until release gate | two bounded live canaries | waive explicitly; release remains technically tested but live usage proof marked unverified |
| Paid/notional canary approval | user | **not granted by this plan** | permits one Claude + one Codex call | no paid call; candidate/tag remains blocked unless user explicitly waives |
| Tag/artifact publication approval | user | not granted | externally visible release action | build candidate wheels only; no tag/push/upload |

No production writes, consumer repins, destructive migration, push, upload, or paid provider calls
are permitted during implementation iterations.

## North-Star And Soul Alignment

- This moves universal provider accounting out of MageQA and into the one engine-owned usage door.
- Provider-specific complexity stays pluggable in `ai_workflow_tools`; the engine sees typed facts.
- Simple workflows pay no mandatory setup or runtime loop: default policy plus one event enrichment.
- Advanced consumers can inject versioned proxy/public catalogs without forking tools or viewer.
- The viewer remains a projection, not another calculator.
- No executor branch is added. No pricing code belongs in the executor.
- No product-specific field/name is added to engine contracts.
- No legacy parser, compatibility bridge, or dual bundle schema is preserved.
- Feature depth is justified: it adds universal functionality and removes duplicate product work;
  the Soul is not used as a veto against the necessary typed policy and cancellation seam.

## Milestone

**Milestone M-CLI-Economics:** all supported subscription CLI doors produce durable, explainable,
failure-honest observed usage and notional pricing under one engine policy.

The milestone spans two iterations. Iteration 1 ends with complete success/failure/timeout behavior
and durable projections. Iteration 2 closes caller-cancellation evidence and the release chain.

---

## Iteration 1 — Structured Usage And Engine Pricing

Purpose: deliver one complete structured-usage and pricing path for success, provider failure, and
timeout across all three Codex doors, persisted through the viewer.
Estimate: **20.5h**. This is slightly above the normal 15-20h target because the coherent boundary
crosses engine, tools, viewer, current-contract seal, and installed-wheel integration; splitting
before persistence would leave an unreviewable half-feature.
Owner: Codex end-to-end.
Entry prerequisites: source baseline intact; no paid credentials needed.
Integrated deliverable: fake-backed Codex and Claude calls produce typed usage/pricing truth through
engine result, summary, snapshot/resume, bundle/group read, and viewer.

### Phase 1.0 — Contract And RED Baseline (2h)

Purpose: prove the defect and freeze the target truth before production edits.

- [x] Build the requirement-to-owner-to-test matrix (estimate: 1h)
  - Purpose: map every MageQA requirement to one code owner and observing test.
  - Likely files/areas: this plan; tools assembly/capability/console; engine pricing/usage/budget;
    viewer grouping/rendering; current-contract seal.
  - Expected output: table covering three execution doors and success/failure/timeout/cancellation.
  - Acceptance criteria: no requirement is assigned to two production owners; cancellation is
    explicitly deferred to Iteration 2, not silently omitted.
  - Verification: source links and test names resolve with `rg`.

- [x] Add RED fixtures and reproductions for the current Codex gap (estimate: 1h)
  - Purpose: show `--json` omission, dropped counters, unknown notional, and absent viewer basis.
  - Likely files/areas: `packages/ai_workflow_tools/tests/fake_cli.py`,
    `test_cli_agent_assembly.py`, `test_cli_agent_capability.py`, `test_console_client.py`;
    engine/viewer integration tests.
  - Expected output: named tests that fail against `engine-v0.11.6` for the intended reasons.
  - Acceptance criteria: all three doors fail; Claude control remains green; fixtures include
    multiple completion events, malformed counters, noise, and timeout-after-usage.
  - Verification: focused tests through `./test.sh`; record exact RED names and messages.

**Phase 1.0 gate**

- Self-review: REDs observe product facts, not function/file existence.
- Testing: expected RED set fails; unrelated controls pass.
- Architecture: owner matrix has no pricing in tools/viewer/executor.
- Degradation: baseline response text, process settlement, and budgets are recorded for comparison.
- Checklist: no task marked complete without captured RED evidence.

### Phase 1.1 — Provider Extraction And All Non-Cancel Doors (5h)

Purpose: make provider protocol extraction complete and shared without pricing in tools.

- [x] Implement one bounded Codex JSONL usage accumulator (estimate: 2h)
  - Purpose: parse final valid cumulative usage independent of response text.
  - Likely files/areas: one small `ai_workflow_tools.cli_agents` usage/parser owner;
    existing shared parser.
  - Expected output: normalized provider usage plus closed parse outcome/unknown reason.
  - Acceptance criteria: arbitrary chunks/UTF-8/noise/unrelated events/multiple completions work;
    last valid event wins; oversized partial line stays bounded; malformed inclusions are rejected,
    not clamped.
  - Verification: table-driven parser tests plus memory-bound attack.

- [x] Enable structured Codex stdout on both argv builders (estimate: 1h)
  - Purpose: obtain JSONL while preserving canonical result-file text.
  - Likely files/areas: `cli_agents/assembly.py`, `cli_agents/console.py`.
  - Expected output: `--json` appears exactly once before positional prompt; result-file flag remains.
  - Acceptance criteria: raw `extra_argv` conflict cannot add/disable/duplicate the typed flag;
    Claude argv unchanged; Codex image ordering remains semantically unchanged.
  - Verification: exact argv tests; mutation removing `--json` must fail.

- [x] Route success, provider failure, and timeout through the accumulator (estimate: 2h)
  - Purpose: use one extraction path in `CliAgentCapability`, `ConsoleLLMClient`, and
    `ConsoleChatModel`.
  - Likely files/areas: `capability.py`, `console.py`, `models.py`, engine failed-call recording.
  - Expected output: each door emits/returns the same normalized usage and incomplete marker.
  - Acceptance criteria: no double event; failure exceptions carry typed usage; chat reply exposes
    real `usage_metadata`; timeout keeps latest usage; result text stays from the result file.
  - Verification: public-door tests for 3 doors x 3 outcomes; Claude parity controls.

**Phase 1.1 gate**

- Self-review: search for duplicate Codex parsing and any stderr protocol parse.
- Testing: parser/argv/door matrix green.
- Architecture: provider logic exists only in tools; process owner unchanged.
- Degradation: response text, images, artifacts, return codes, process I/O metadata, and timeout
  classifications match baseline.
- Mutation: remove `--json`, select first completion, sum completions, or skip one door; each dies.

### Phase 1.2 — Typed Pricing Policy And Single Event Enrichment (7.5h)

Purpose: calculate and persist notional value once at the engine usage boundary.

- [x] Add strict normalized usage and pricing contracts (estimate: 2h)
  - Purpose: make quantities, sources, rates, versions, and unknown reasons machine data.
  - Likely files/areas: new dependency-light engine contract module; `models.py` event fields;
    package exports and public seal.
  - Expected output: closed Pydantic models with finite/type/relationship validation.
  - Acceptance criteria: bools, negatives, NaN/Inf, unknown keys, impossible inclusive counters,
    hollow known/unknown results, and unversioned rates fail loudly.
  - Verification: constructor/schema/round-trip tests; intentional public deltas ledgered.

- [x] Implement versioned catalog and pure pricing policy (estimate: 2h)
  - Purpose: one exact pricing algebra for metered estimates and subscription notional.
  - Likely files/areas: `pricing.py`, config models/loader.
  - Expected output: deterministic public/proxy/provider-reported pricing with exact basis.
  - Acceptance criteria: independent uncached/cached/cache-create/output rates; reasoning never
    double-counted; provider total wins; model miss and invalid usage become typed unknown; no
    event-level fake-zero rounding; the old `WORKFLOW_MODEL_PRICE_OVERRIDES_JSON` door is removed
    repo-wide rather than silently coexisting with the typed catalog.
  - Verification: table tests with hand-calculated amounts and metamorphic counter/rate changes.

- [x] Inject policy through engine composition and usage scope (estimate: 2h)
  - Purpose: enrich every event before all sibling surfaces.
  - Likely files/areas: `WorkflowConfigBundle`, builder, engine, runner, `WorkflowUsageContext`,
    `usage_events.py`.
  - Expected output: one configured policy per run; event enriched once before capture/summary/sink.
  - Acceptance criteria: direct builder and YAML config produce identical policy; nested
    subworkflows/resume inherit the same policy; concurrent runs with different policies do not
    leak; invalid policy output leaves zero partial persistence.
  - Verification: barrier-forced concurrency test and summary/bundle object-identity assertions.

- [x] Migrate existing subscription event producers to typed pricing truth (estimate: 1.5h)
  - Purpose: remove stringly accounting authority rather than create a parallel system.
  - Likely files/areas: CLI tools, browser/image subscription producers, `llm_protocol.py`,
    `provider_usage.py`, failed-call paths, viewer grouping.
  - Expected output: typed source/unknown reason everywhere; scalar costs remain aggregate fields.
  - Acceptance criteria: no production read of metadata `cost_known`/`cost_source`; metered and
    notional remain separate; existing known and unknown providers stay honest.
  - Verification: repo-wide `rg` proof plus budget-matrix regressions.

**Phase 1.2 gate**

- Self-review: compare every event constructor and failure path to the typed contract.
- Testing: pricing, config, budget, usage capture, concurrency, public schema, and seal tests green.
- Architecture: policy is engine-owned/injected; no provider imports in engine; no pricing in viewer.
- Degradation: existing API/image/Claude costs remain equivalent except intentionally richer typed
  provenance; subscription notional still cannot debit metered budget.
- Mutation: cached-rate, output-rate, provider-total precedence, catalog source, event-enrichment
  order, and policy isolation mutations all killed.

### Phase 1.3 — Persistence, Viewer, And Iteration Gate (6h)

Purpose: prove the economics survives the complete durable and human-facing path.

- [x] Lock result, snapshot/resume, bundle, group, and aggregate propagation (estimate: 2h)
  - Purpose: prevent sibling surfaces from dropping typed pricing facts.
  - Likely files/areas: snapshot and observation tests, grouping, usage summary.
  - Expected output: one event identity and pricing basis through fresh run and resumed run.
  - Acceptance criteria: raw/normalized quantities, source, version, rates, amount/unknown reason,
    success/incomplete status all survive; aggregate counts unknowns from typed truth.
  - Verification: engine-run -> suspend -> resume -> grouped read integration test.

- [x] Render pricing basis and token decomposition without recalculation (estimate: 1.5h)
  - Purpose: make the persisted truth useful to operators and MageQA.
  - Likely files/areas: viewer projection/rendering/tests.
  - Expected output: amount, source/version, uncached/cache/reasoning quantities, unknown reason.
  - Acceptance criteria: provider-reported vs public vs proxy visibly distinct; unknown never
    appears as `$0`; no provider JSON interpreted; HTML escapes all labels/reasons.
  - Verification: real rendered HTML semantic assertions and served-viewer test.

- [x] Run cumulative Iteration 1 tests and sealed-contract accounting (estimate: 0.5h)
  - Purpose: establish a reproducible candidate before architectural review.
  - Likely files/areas: all affected test suites, current-contract sealer/ledger.
  - Expected output: focused matrix, full `./test.sh unit`, seal VERIFY, clean tree.
  - Acceptance criteria: zero unexpected skips/failures; every public delta ledgered then spent;
    no weakened/deleted tests.
  - Verification: recorded commands/counts and `git diff --check`.

- [x] Intermediate direction and architecture audit (estimate: 2h)
  - Milestones crossed: structured usage + pricing through durable/viewer surfaces.
  - Source goals/docs reread: MageQA request, binding spec, architecture Soul, this plan.
  - Cumulative implementation inspected: aggregate Iteration 1 diff and all event producers/readers.
  - Global architecture findings: verify one provider parser, one pricing owner, one event door,
    one persisted truth, no executor growth, no mandatory complexity for no-usage workflows.
  - Direction/soul alignment findings: confirm universal functionality moved out of products.
  - Simplifications, omissions, or degradation found: list explicitly; cancellation remains the only
    planned incomplete outcome.
  - Verdict: PASS | REPLAN | BLOCKED
  - Required plan changes: add checkbox tasks for every discovered surface; no silent folding.
  - Evidence/verification: import graph, `rg` duplicate sweep, module line/complexity comparison,
    test/degradation table.

**Iteration 1 acceptance criteria**

- All three Codex doors record correct typed usage on success/failure/timeout.
- Claude provider-reported usage/cost remains correct.
- Pricing policy is injected, versioned, and isolated per run.
- Invalid/missing facts become explicit unknown, never zero or a clamped price.
- Snapshot/resume/bundle/group/viewer preserve identical pricing truth.
- Full tier and current-contract seal pass.
- Architecture audit verdict is `PASS`.

**Iteration 1 end-to-end scenario**

Run a fake Codex structured completion that emits two cumulative completion events, cache/reasoning
counters, response text in the result file, and then a workflow suspension/resume. Assert the final
event (not sum) is priced, persisted, grouped, and rendered with public/proxy basis. Repeat with a
timeout after a usage event and with Claude provider-reported cost.

**Iteration 1 review gate**

Codex prepares one evidence handoff after the cumulative gate. Claude independently reviews the
complete iteration once. Codex resolves confirmed findings in one consolidated correction pass and
reruns the cumulative gate. Iteration 2 does not start on `FAIL`, `REPLAN`, or unresolved findings.

---

## Iteration 2 — Cancellation Evidence And Release

Purpose: preserve already-emitted usage during caller cancellation without weakening process
containment, then ship the complete immutable matrix.
Estimate: **18.5h**.
Owner: Codex end-to-end.
Entry prerequisites: Iteration 1 independent `PASS`; paid approval is not needed until Phase 2.2.
Integrated deliverable: cancellation re-raises the original exception while the finalized bundle
contains the last valid CLI usage and pricing truth; exact wheels pass bounded live canaries.

### Phase 2.0 — Incremental Process Observation And Cancellation (7h)

Purpose: close the only process lifecycle that cannot use returned stdout.

- [x] Add the generic bounded stdout-observer seam (estimate: 2h)
  - Purpose: expose streaming protocol facts without moving provider parsing into engine.
  - Likely files/areas: `engine/process_io.py`, `engine/external.py`, external-process tests.
  - Expected output: optional per-request observer fed before bounded retention/truncation.
  - Acceptance criteria: no observer means equivalent behavior; observer failure cannot stop drain,
    leak child, or exceed memory; timeout/cancel still kill and reap descendants.
  - Verification: chunk/observer failure/flood/cancel/process-tree tests and mutation kills.

- [x] Record cancellation usage exactly once across all three CLI doors (estimate: 3h)
  - Purpose: preserve latest facts before re-raising cancellation.
  - Likely files/areas: tools capability/console, engine failed-call/cancellation recording.
  - Expected output: cancelled/incomplete usage event with final observed counters and pricing.
  - Acceptance criteria: exact original `CancelledError` identity re-raised; no completed usage means
    typed unknown; one completed event means one ledger event; no duplicate from outer wrappers;
    cancellation before spawn produces no event.
  - Verification: barrier-controlled cancellation tests for agent, plain callable, and chat model.

- [x] Prove cancellation evidence reaches finalized observation bundles (estimate: 2h)
  - Purpose: integrate with v0.11.5 cancellation finalization rather than adding a bundle owner.
  - Likely files/areas: caller-cancellation tests, observation config, artifact/process cleanup.
  - Expected output: cancelled bundle/readback/viewer with usage and no live process.
  - Acceptance criteria: copy/off policies truthful; sink/finalization failure remains loud;
    completed prior artifacts remain; cancellation after resume preserves prior + new usage;
    child/fanout process cleanup unregressed.
  - Verification: public `engine.run`/`resume` tests with PID-level descendant proof.

**Phase 2.0 gate**

- Self-review: inspect every `CancelledError` boundary and usage-recording owner.
- Testing: cancellation/process/observation suites green.
- Architecture: generic process seam only; no provider branch in external owner; no second bundle.
- Degradation: exact cancellation identity, kill/reap timing, bounded I/O, artifacts, and prior
  cancellation contracts remain equivalent.
- Mutation: observer bypass, accumulator reset, duplicate recording, cancellation swallow, and
  reaper bypass all killed.

### Phase 2.0b — Invocation Linkage And Capture Truth (8h)

Purpose: close the omitted MageQA provider-call evidence contract before qualification.

- [x] Add the typed invocation/capture fields and strict bundle-link validator (estimate: 2h)
  - One opaque invocation id type is shared by trace, detail, usage, LLM/CLI transport contracts.
  - Bundle finalization and every current-schema reader reject missing, conflicting, duplicate, or
    dangling invocation links.
  - `captured` requires linked details carrying the same id; suppressed capture requires one typed
    reason and no detail references.

- [x] Thread one id through plain-callable and LangChain provider attempts (estimate: 2h)
  - Each retry/repair is a distinct invocation.
  - Success, provider failure, timeout, and cancellation keep the same id and elapsed time.
  - Console process request/result metadata carries the same id as engine usage and LLM details.

- [x] Thread one id through `CliAgentCapability` (estimate: 1.5h)
  - Request creation owns the id so the generic capability request trace can see it before spawn.
  - Tool result, process settlement, usage, generic trace, and details all retain that id.
  - Engine wall-clock elapsed time is present even when the provider omits duration.

- [x] Record capture suppression as typed truth (estimate: 1h)
  - `off`, missing sink, and projection failure remain distinguishable.
  - Trace remains available when detail projection fails.

- [x] Add real fake-process and mutation-shaped integration tests (estimate: 1.5h)
  - A Codex image-agent fixture proves one id joins prompt/image references, process result, final
    tool result, usage, elapsed time, bundle read/group, and viewer.
  - Removing any one link makes bundle finalization/read fail.
  - Observation-off and projection-failure cases record their exact typed reason.

**Phase 2.0b gate**

- Focused tests cover plain callable, LangChain console, CLI agent, failure, timeout, cancellation,
  capture-off, projection failure, persistence, grouped read, and viewer.
- Mutations removing usage/trace/detail/process links and capture reasons are killed.
- The full tier and contract seal pass with every schema delta explicitly ledgered and spent.

### Phase 2.1 — Qualification, Docs, And Candidate (5.5h)

Purpose: close the permanent contract and installed-wheel behavior before paid validation.

- [x] Add installed-wheel hermetic qualification (estimate: 2h)
  - Purpose: test the real package matrix without repository imports.
  - Likely files/areas: existing release/qualification harness and tests.
  - Expected output: fake Claude/Codex calls through all three doors, bundle/readback/viewer,
    cancellation, and unknown-rate case from installed wheels.
  - Acceptance criteria: imports resolve only from venv; no network; exact matrix enforced; response,
    usage, pricing, cancellation, and process cleanup asserted semantically.
  - Verification: reproducible two-build wheel smoke using release runbook.

- [x] Update permanent contracts and consumer handoffs (estimate: 1.5h)
  - Purpose: tell adopters exactly what is calculated and what is not.
  - Likely files/areas: spec status table, architecture pricing section, README, tools README,
    operations, misuse risks, MageQA/SlackAzz/GoPro/voice handoffs, promise registry.
  - Expected output: candidate wording, config example, formula/semantics, failure behavior, adoption
    gate, historical v0.11.6 reference.
  - Acceptance criteria: docs never call subscription notional billed spend; no claim that Codex CLI
    enforces a monetary cap; direct-provider bypass warning includes lost budget/usage truth; no
    stale current matrix.
  - Verification: doc-contract tests and version-string sweep.

- [x] Run cumulative mutation and degradation matrix (estimate: 2h)
  - Purpose: prove every new guard has a semantic killer and old functionality stayed intact.
  - Likely files/areas: mutation driver/evidence in this plan; affected tests.
  - Expected output: named kill table for argv, parsing, normalization, pricing, enrichment,
    persistence, viewer, observer, cancellation, and package matrix.
  - Acceptance criteria: each mutation observed failing under its named test; exact restore verified;
    no test weakening/deletion/xfail; full pre-v0.11.7 behavior table accounted.
  - Verification: clean rerun after all restores and current-contract VERIFY.

**Phase 2.1 gate**

- Self-review: candidate claims match installed behavior.
- Testing: focused, full tier, seal, import/layer guards, wheels, and hermetic E2E green.
- Architecture: no god-object growth; parser/pricing/process/viewer owners remain separate.
- Degradation: all intentional public schema/version changes ledgered; no compatibility path added.
- Checklist: paid/manual/release tasks remain unchecked.

### Phase 2.2 — Live Gate, Final Audit, And Release (6h)

Purpose: prove real provider semantics and release only the reviewed exact bytes.

- [x] Run one bounded Claude and one bounded Codex subscription canary, or record an explicit waiver (estimate: 1.5h)
  - Purpose: verify installed CLI output semantics, not economic quality.
  - Likely files/areas: existing clean-clone subscription qualification harness.
  - Expected output: exact candidate wheels produce provider-reported Claude notional and
    catalog-priced Codex notional through bundle/readback/viewer.
  - Acceptance criteria: requires fresh user approval; at most one call each; explicit timeout and
    call caps; target combined notional <= `$0.20`; no retry without new approval; `$0` metered;
    model/version/catalog recorded; CLI/auth/provider outage is an honest non-PASS.
  - Verification: machine-readable manifest, usage JSONL, bundle, rendered pages.
  - Disposition: **NOT RUN; explicitly waived for this tag by the release owner on 2026-07-21.**
    No paid provider call was made. Real installed-CLI semantics remain a consumer adoption-canary
    responsibility and are not represented as release evidence.

- [x] Perform user-side viewer acceptance, or record an explicit waiver (estimate: 0.5h)
  - Purpose: validate the actual operator-facing economics.
  - Likely files/areas: served qualification viewer.
  - Expected output: user sees counts, cache/reasoning split, amount, source/version, and no fake
    metered charge.
  - Acceptance criteria: both pages open; source labels are understandable; unknown and partial
    states are truthful; no raw provider JSON or stderr noise leaks.
  - Verification: explicit user `PASS` or documented waiver; no tag before one exists.
  - Disposition: **NOT RUN; explicitly waived for this tag by the release owner on 2026-07-21.**
    Hermetic installed-wheel bundle/readback/viewer smoke passed; no claim of manual inspection of a
    live-provider page is made.

- [x] Intermediate direction and architecture audit (estimate: 2h)
  - Milestones crossed: complete M-CLI-Economics milestone and release candidate.
  - Source goals/docs reread: consumer request, binding spec, Soul, both iteration reports.
  - Cumulative implementation inspected: complete baseline-to-candidate diff, all process/provider/
    pricing/persistence/viewer paths, remaining roadmap.
  - Global architecture findings: confirm one event loop, no product accounting, no executor branch,
    no pricing side effects, bounded observer, no duplicated model tables.
  - Direction/soul alignment findings: confirm functionality increased while simple consumers remain
    zero-config and complex consumers can inject policy.
  - Simplifications, omissions, or degradation found: list and either fix or replan; no hidden
    "future" for a required acceptance criterion.
  - Verdict: PASS | REPLAN | BLOCKED
  - Required plan changes: any finding becomes a checkbox task before release.
  - Evidence/verification: dependency/complexity scan, aggregate test matrix, provider-door and
    persisted-surface sweep.
  - Verdict: **PASS**; evidence is recorded in the offline candidate report below.

- [x] Build, verify, and cut the immutable release (estimate: 2h)
  - Purpose: bind reviewed source, exact wheels, evidence, and consumer action.
  - Likely files/areas: package versions, release manifest/runbook, permanent docs, local tag.
  - Expected output: reproducible wheels, SHA256SUMS, release-manifest-v2, smoke evidence, annotated
    local `engine-v0.11.7` tag.
  - Acceptance criteria: only after independent review PASS, paid/manual gates or explicit waivers,
    and user tag approval; old tags unmoved; detached clean build; verifier-before-install smoke;
    no push/upload/publication without separate approval; tools and viewer dependencies require
    `ai-workflow-engine>=0.11.7,<0.12`, so the old engine cannot resolve with the new packages.
  - Verification: tag peel, wheel RECORD/content, two-build equality, fresh-venv run, clean tree.

**Iteration 2 acceptance criteria**

- Caller cancellation preserves latest available structured usage and original cancellation.
- Descendant process kill/reap and bounded I/O remain proven.
- Exact installed wheels pass hermetic and approved live canaries.
- Viewer displays persisted pricing truth without calculation.
- Full suite, seal, architecture audit, independent review, and user-side gate pass.
- Immutable release record names commit, versions, wheel hashes, catalog version, tests, mutations,
  paid calls, waivers, and unverified items.

**Iteration 2 end-to-end scenario**

From installed candidate wheels, run a Codex-backed engine workflow whose subprocess emits a valid
`turn.completed` and is then caller-cancelled before ordinary return. Assert the child is gone, the
same `CancelledError` reaches the caller, the bundle is `cancelled`, exactly one incomplete usage
event contains the latest counters and configured notional basis, grouped read and viewer agree,
and metered spend remains unset. Then run normal Claude and Codex calls for live provider proof.

**Iteration 2 review gate**

Codex prepares one cumulative evidence handoff. Claude independently attacks the complete
Iteration 2 diff once. Codex resolves confirmed findings in one consolidated repair batch and
reruns the entire gate. No tag is created until counterpart `PASS`, user-side live/manual gates,
and explicit release approval. For this release, the release owner explicitly waived the formal
counterpart pass after the complete offline and installed-wheel evidence was presented; the waiver
does not convert the missing review into `PASS`.

## Automated Test Matrix

| Level | Required proof |
|---|---|
| Unit | JSONL chunking/final-event selection; counter normalization; invalid relationships; catalog/model matching; public/proxy/provider source; exact arithmetic; typed unknowns |
| Contract | `--json` argv on both builders; three CLI doors emit same semantic usage; policy injection/isolation; event enrichment happens before all sinks |
| Integration | success/failure/timeout/cancellation through real `ExternalProcessCapability` and fake CLI subprocess; PID cleanup; no duplicate events |
| Persistence | run result, summary, snapshot/resume, bundle JSONL, grouped read, aggregates preserve identical pricing details |
| Viewer | served page displays amount/source/version/cache/reasoning/unknown reason from persisted data and escapes content |
| Regression | Claude envelope/provider cost; metered budgets; images; result file; process I/O caps; cancellation artifacts; waits/resume; model attribution |
| Seal | all public/schema/corpus deltas explicit, minimal, ledgered, consumed, and deterministic |
| Installed wheel | detached reproducible matrix with zero repository imports |
| Live | one approved Claude + one approved Codex subscription call from exact candidate |

No shallow existence-only test satisfies a gate. Every risky guard needs an attack-shaped test and
at least one mutation that proves the test observes the behavior.

## Mandatory Mutation Matrix

At minimum:

1. remove Codex `--json`;
2. choose first `turn.completed`;
3. sum cumulative completions;
4. parse stderr;
5. clamp cached > input;
6. add reasoning twice;
7. price cached tokens at uncached rate;
8. prefer configured price over provider-reported total;
9. treat proxy as public;
10. round a small known event to zero;
11. enrich after summary/sink;
12. share policy across concurrent runs;
13. omit failure/timeout usage;
14. omit one of the three execution doors;
15. derive unknown count from old metadata;
16. drop pricing basis in snapshot/bundle/group;
17. recalculate in viewer;
18. bypass stdout observer on cancellation;
19. record cancellation twice;
20. swallow or replace `CancelledError`;
21. skip process reap;
22. accept stale package matrix/version.

Every mutation record names the modified line, killer test, observed failure, revert proof, and
clean rerun. A surviving mutation adds a new checkbox task; it is never explained away.

## Manual Validation Scenarios

### Scenario A — Operator reads one normal Codex call

- Setup: exact candidate wheels, fake or approved live Codex, configured public-rate catalog.
- Action: run one CLI-backed workflow and open its grouped viewer page.
- Expected: final response is unchanged; token/cache/reasoning quantities and public-rate version
  are visible; notional is shown; metered spend is absent.
- Failure: `$0`, unknown despite valid usage, raw JSON, duplicate event, or mismatched totals.

### Scenario B — Unknown model remains honest

- Setup: use a model name absent from the catalog and no provider-reported cost.
- Action: run and inspect result, bundle, and viewer.
- Expected: tokens remain; notional is absent; explicit `model_rate_not_found` is visible.
- Failure: zero cost, fallback to another model's rate, or failed response solely because price is
  unknown.

### Scenario C — Timeout after usage

- Setup: fake CLI emits `turn.completed`, then sleeps past the engine window.
- Action: run through `CliAgentCapability`.
- Expected: process is reaped; result remains timeout/partial; one incomplete usage event persists
  with notional basis.
- Failure: accepted result, missing usage, duplicate usage, or surviving PID.

### Scenario D — Caller cancellation after usage

- Setup: fake CLI emits usage and holds; observation full/copy; run via public `engine.run`.
- Action: cancel the caller task.
- Expected: same `CancelledError`; child gone; cancelled bundle; one usage/pricing record; prior
  artifacts retained.
- Failure: swallowed/replaced cancellation, live process, missing bundle usage, or false completion.

### Scenario E — Real subscription proof

- Setup: exact installed wheels, authenticated CLIs, approved two-call budget.
- Action: one Claude and one Codex call; serve exported bundles.
- Expected: Claude source `provider_reported`; Codex source `configured_public_rate` or approved
  proxy; both visible in viewer; `$0` metered.
- Failure: extra call, cap/timeout breach, source ambiguity, or unsupported CLI event schema.

## Risks And Open Questions

### Blockers

- No implementation blocker is known.
- Paid live validation and release/tag actions remain blocked until fresh user approval.

### Assumptions

- Current Codex JSONL uses inclusive `input_tokens`/`cached_input_tokens` and
  `output_tokens`/`reasoning_output_tokens`; the parser treats schema drift as unknown rather than
  guessing.
- `--json` and `--output-last-message` remain combinable; argv tests and the live canary verify.
- Claude's `total_cost_usd` is an API-equivalent/provider-calculated notional for subscription use,
  not per-call billed spend.

### Risks of doing the work

- Public event/config schemas change; latest-only policy means consumers must repin the complete
  matrix fresh.
- Incremental process observation is high-risk: a bad implementation could block pipes, retain
  unbounded memory, duplicate events, or interfere with cancellation. Iteration separation and
  PID/memory/mutation gates address this.
- A stale public-rate catalog can produce precise-looking but outdated notional. Version/source/date
  are therefore mandatory and proxy rates are visibly distinct.
- Tiny prices can disappear if rounded too early. Event-level math must retain precision.

### Risks of not doing the work

- MageQA adoption remains blocked.
- Products duplicate provider parsers/rates and drift from engine accounting.
- Subscription economics remain unavailable precisely where observed counters exist.

### Explicitly out of scope

- Per-user/day account ledger or billing system.
- A hard dollar stop for Codex subscription CLI; Codex exposes no supported monetary cap.
- Pricing-driven control flow or retries.
- Provider rate auto-download/network lookup.
- Product dashboards; they consume engine truth after adoption.
- Backward-compatible readers, dual event models, wait/snapshot migrations, or moved old tags.

## Definition Of Done

- Both iterations and every task are `[x]`; newly discovered work is represented as new tasks.
- All phase gates and both intermediate direction/architecture audits return `PASS`.
- Three CLI execution doors preserve structured usage on success/failure/timeout/cancellation.
- Counter normalization and pricing provenance are typed, strict, versioned, and persisted once.
- Provider-reported, public-rate, proxy-rate, and unknown states are visibly distinct.
- No malformed counter is clamped into a plausible amount; no known small amount becomes fake zero.
- Metered and subscription-notional totals remain separate; existing safety caps stay enforced.
- Cancellation keeps original exception identity, kills/reaps descendants, and finalizes evidence.
- Result, summary, snapshot/resume, bundle/group, viewer, and installed wheels agree.
- Meaningful unit/contract/integration/persistence/viewer/cancellation/release tests pass via
  `./test.sh`; full tier and current-contract seal pass.
- Required mutations are killed and restored cleanly.
- Permanent docs and all consumer handoffs describe the exact current release truth.
- One independent counterpart review occurs after each completed iteration; findings are repaired in
  one consolidated pass rather than task-by-task ping-pong.
- Approved live canaries and manual viewer acceptance pass, or each waiver is explicit and the
  unverified claim is not represented as PASS.
- Reproducible wheels, hashes, manifest, source commit, and annotated local tag are verified.
- Old tags are unchanged; no push/upload/publication/repin occurs without explicit approval.

## Offline Candidate Gate Report — 2026-07-20 (Historical Checkpoint)

Verdict at this checkpoint: **PASS for source implementation and offline repository gates.** The
release was still blocked at that time. The final release record below supersedes this checkpoint;
waived gates remain recorded as `NOT RUN`, never rewritten as `PASS`.

### Delivered

- Codex CLI structured JSONL usage is collected on capability, console-completion, and chat-model
  doors without changing canonical response-file output.
- Claude provider-reported cost and Codex normalized counters feed one engine-owned typed pricing
  policy. Cached input is subtracted from inclusive input, reasoning remains a subset of output,
  provider totals take precedence, and unknown rates remain typed unknown rather than fake zero.
- Metered spend and subscription notional stay separate; subscription notional cannot debit metered
  budget caps.
- One invocation id links provider request/response traces, details, usage, process settlement,
  snapshots, observation bundles, grouping, and viewer projection. Current-schema writers and
  readers fail loudly on duplicate, dangling, or conflicting links.
- Success, provider failure, timeout, and caller cancellation retain the latest available usage.
  Cancellation still re-raises the original `CancelledError`, retains completed artifacts, and
  preserves process kill/reap behavior.

### Test Evidence

- Full repository tier: `./test.sh unit` — **1650 passed, 2 optional skips, 0 failed,
  152 deselected**, coverage **85.85%**.
- Release-contract/current-seal tier: **66 passed, 0 failed**.
- Corrected affected matrix: **319 passed, 0 failed**.
- The first full run exposed 11 compatibility failures in dynamic mock-backed provider surfaces and
  direct notional test fixtures. The guards and fixtures were corrected; the exact failure set
  passed before the clean full rerun.
- Contract seal: deterministic **EQUAL**, zero remaining corpus/public deltas, and zero active
  intentional-break permissions.
- Static gates: `git diff --check` clean; simple-tier, layering, public-export, documentation,
  promise-registry, and package-matrix guards green.

### Mutation Evidence So Far

| # | Fault reintroduced | Named killer | Result |
|---|---|---|---|
| 1 | Remove Codex `--json` | exact Codex argv contract | killed, restored |
| 2 | Add reasoning tokens a second time | hand-calculated pricing contract | killed, restored |
| 3 | Price inclusive input without subtracting cache-read input | Codex normalization/pricing contract | killed, restored |
| 4 | Corrupt the GPT-5.6 Sol catalog rate | exact catalog contract | killed, restored |
| 5 | Book subscription/provider notional into metered spend | metered-vs-notional budget split | killed, restored |
| 6 | Drop failed/timeout Codex usage | failure and timeout door matrix | killed, restored |
| 7 | Drop cancelled plain-LLM usage | cancelled bundle linkage regression | killed, restored |
| 8 | Remove detail-to-trace invocation ownership validation | observation-integrity attack matrix | killed, restored |
| 9 | Trust dynamic provider-object `cost_class` attributes | `test_dynamic_provider_objects_cannot_fabricate_usage_identity_or_counters` | killed, restored, clean rerun |
| 10 | Select the first cumulative Codex completion | final-cumulative-turn parser contract | killed, restored |
| 11 | Sum cumulative Codex completion snapshots | final-cumulative-turn parser contract | killed, restored |
| 12 | Parse provider protocol from stderr diagnostics | `test_codex_usage_protocol_never_reads_stderr_diagnostics` | initial gap added, killed, restored |
| 13 | Clamp cached/reasoning counters into plausible subsets | impossible-counter rejection matrix | killed, restored |
| 14 | Price cached input at the uncached rate | hand-calculated pricing contract | killed, restored |
| 15 | Ignore provider-reported total in favor of catalog calculation | provider-total precedence contract | killed, restored |
| 16 | Relabel configured proxy rates as public rates | `test_configured_proxy_rate_never_masquerades_as_public_pricing` | initial gap added, killed, restored |
| 17 | Round a known sub-microdollar event to zero | `test_known_sub_microdollar_event_never_rounds_to_fake_zero` | initial gap added, killed, restored |
| 18 | Persist an unpriced copy into the canonical summary | pricing-before-summary contract | killed, restored |
| 19 | Replace run-local policy with the default/shared policy | barrier-forced policy-isolation contract | killed, restored |
| 20 | Omit normalized usage from the console-completion door | console Codex structured-usage integration | killed, restored |
| 21 | Drop pricing basis from a suspended snapshot | snapshot/resume/group/viewer pricing contract | killed, restored |
| 22 | Replace persisted viewer notional with synthetic `$0` | exact viewer projection contract | killed, restored |
| 23 | Bypass the bounded stdout observer | process-observer contract | killed, restored |
| 24 | Disable cancellation-event deduplication and record twice | cancelled plain-LLM bundle contract | killed, restored |
| 25 | Replace the original caller `CancelledError` | cancelled plain-LLM bundle contract | killed, restored |
| 26 | Disable the process reaper | PID-level cancellation cleanup contract | first target survived through the finally belt; owner-level retarget killed, restored |
| 27 | Drift tools package version behind the documented matrix | derived package-matrix contract | killed, restored |

All 22 mandatory mutation categories are covered by the 27 concrete mutations above; extra rows
separate compound requirements and retain the initially surviving reaper attempt. Installed-wheel
qualification remains unchecked below.

### Architecture And Degradation Audit

- Provider protocol parsing has one owner in `ai_workflow_tools.cli_agents.usage`.
- Pricing contracts/catalog/policy have one dependency-light engine owner in `usage_contract.py`;
  canonical enrichment occurs only at `record_usage_event`.
- Cross-record evidence validation has one owner in `observation_integrity.py`.
- The viewer projects persisted pricing/linkage truth and performs no provider parsing or pricing.
- No provider-pricing branch was added to the executor. No-usage workflows do not load tools or
  viewer facilities.
- Existing response text, artifacts, process settlement, wait/resume, cancellation identity,
  metered budget behavior, and simple-tier imports stayed green in the full tier.
- Direction/Soul verdict: **PASS**. The feature is optional, pluggable, and kept out of the
  orchestration coordinator.

### Explicitly Open

- [x] Complete the remaining mandatory mutation matrix and clean rerun.
- [x] Build two reproducible candidate wheel sets and pass the installed-wheel hermetic smoke.
- [x] Formal counterpart review **NOT RUN; explicitly waived by the release owner for this tag on
  2026-07-21** after source/offline/installed-wheel evidence and a consumer preflight were reported.
  This is a waiver, not a `PASS` verdict.
- [x] Paid Claude/Codex canary **NOT RUN; explicitly waived by the release owner for this tag on
  2026-07-21**. No paid call has been made.
- [x] Manual live-provider viewer acceptance **NOT RUN; explicitly waived by the release owner for
  this tag on 2026-07-21**. Hermetic installed-wheel viewer evidence is green; manual live evidence
  is not claimed.
- [x] Flip candidate docs to released truth, build the final release bundle, verify hashes/manifest,
  and create the local annotated `engine-v0.11.7` tag. The local tag and bundle are complete; no
  push, upload, or consumer repin occurred.

### Candidate Artifact Evidence

- Candidate source commit: `23ca57b`.
- Two independent detached clean clones built with `SOURCE_DATE_EPOCH=1784589655`,
  `PYTHONHASHSEED=0`, and umask `022`.
- Both builds produced byte-identical wheels:
  - `ai_workflow_engine-0.11.7-py3-none-any.whl`:
    `0035c37fe41596902a230a5f28f7de97fa27992a32cade51801c248c6c7a35d5`
  - `ai_workflow_tools-0.5.2-py3-none-any.whl`:
    `fe227d396609ace481931e44fe286f8477a0c0fdae9b2bb73016857e1c7b7ee5`
  - `ai_workflow_viewer-0.3.2-py3-none-any.whl`:
    `6046ab0678b84756e91674fdc50ad6f6d6ace827f4a8dca26c71a47ce77a4939`
- A fresh out-of-repository virtual environment installed those exact bytes and passed
  `release_artifacts.py smoke-installed`.
- These are candidate hashes, not the final release-manifest identities. The final release bundle
  must be rebuilt from the reviewed annotated tag and verified independently by the release tool.

## Final Release Record — 2026-07-21

Verdict: **RELEASED LOCALLY.** The immutable source tag and complete `release-manifest-v2` directory
are verified. This record does not authorize a push, upload, or consumer deployment.

### Source And Package Identity

- Annotated tag: `engine-v0.11.7`
- Tag object: `9e1769a9d31ffab1b9222ab9056aff5db920fc19`
- Peeled source commit: `d8cd637dcb68c696cfd3370ec9f30a0e4aa382c8`
- Package matrix: engine `0.11.7`, tools `0.5.2`, viewer `0.3.2`
- Previous immutable tag `engine-v0.11.6` remains at
  `266d2cf21179df5f56d207cc004c96a26d5157e9`.

### Final Artifact Identity

Verified release directory:
`/private/tmp/tghandy-v0117-release-work/bundle`

- `ai_workflow_engine-0.11.7-py3-none-any.whl`:
  `7f4a882da2e9acb7cc24d202b205167848ed51a94dd723fd1dd7900780c58af3`
- `ai_workflow_tools-0.5.2-py3-none-any.whl`:
  `26bdb4389470babf128306887b6bd0e3849ddd592e5cff6f3a648f2f6e43a2a2`
- `ai_workflow_viewer-0.3.2-py3-none-any.whl`:
  `6d8dd2ae90efff06641ac42f496d2a270214a7a2c06a488a07bbc65cf31d6489`
- `release-manifest.json`:
  `85d6323fbc00c79395d2929e312919cfc778a2c01c0a9f1064aaa75f849e891a`
- `SHA256SUMS`:
  `28deeccb2edbfb41ea5724873f31269e9513be0abd1f0d86196b78ee46041676`

### Final Tagged Evidence

- Two independent detached builds produced byte-identical wheels.
- Tagged full tier via `./test.sh unit`: **1650 passed, 2 optional skips, 0 failed,
  152 deselected** in 116.77 seconds.
- Fresh out-of-repository venv installed the exact final wheels and passed
  `release_artifacts.py smoke-installed`.
- `release_artifacts.py verify-bundle` passed against the assembled directory and binds every
  wheel, evidence record/log, verifier source, tag object, and source commit.
- The first test-gate attempt stopped before pytest because the clean clone lacked the ignored
  `.env`; the rerun supplied the local test environment and is the only test evidence in the
  bundle. The first smoke attempt was denied dependency-network access; the fresh-venv rerun with
  dependency access passed and is the only smoke evidence in the bundle.

### Explicitly Unverified

- Paid Claude/Codex live canary: **NOT RUN; release-owner waiver.**
- Manual inspection of live-provider viewer pages: **NOT RUN; release-owner waiver.**
- Formal counterpart cumulative review: **NOT RUN; release-owner waiver.**

These waivers do not weaken product adoption gates. MageQA and other consumers verify the complete
release directory, pin the exact consumed wheel hashes, and run their own authenticated provider and
product canaries before changing a deployed pin.
