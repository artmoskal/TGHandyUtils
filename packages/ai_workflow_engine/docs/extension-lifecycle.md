# Framework Requests And Feedback Lifecycle

This process prevents two failure modes: products quietly rebuilding engine mechanics, and the core
absorbing product-specific behavior that should remain an adapter or pack.

## Where Requests Live

1. **Consumer repository:** create one transient request under that repository's
   `docs/_discussion/YYYY-MM-DD-engine-<topic>-request.md`.
2. **Engine repository:** the engine owner records analysis and delivery status in an existing active
   iteration/review document under `docs/_discussion/`; do not create permanent design claims before
   the direction is accepted.
3. **Permanent engine docs:** after implementation and acceptance, migrate the stable contract to the
   binding spec and the relevant generic/product guides.
4. **Consumer permanent docs:** after adopting the released tag, record pin, adapter mapping,
   operational ownership, and product tests in that consumer's permanent architecture/deployment
   docs.

Discussion files are coordination artifacts, not dependencies. Products consume tagged code and
permanent docs.

## Request Template

```markdown
# Engine request: <short capability name>

Consumer: <product/repository>
Owner: <person/team>
Current engine pin: <tag + commit + wheel hash>
Status: proposed

## User/workload outcome
What real workflow must become possible? Include representative input/output and scale.

## Current blocked shape
Show why WorkflowBuilder + registered capabilities/packs/adapters cannot express it without
product-owned orchestration or loss of guarantees.

## Proposed ownership
Candidate layer: L0 engine | L1 executor | L2 tool pack | L3 domain pack | product adapter.
State what must remain product-owned.

## Required guarantees
Bounds, replay, side effects, privacy/data, budgets/cost, evidence, observation, persistence,
concurrency, failure behavior, and compatibility.

## Acceptance evidence
Hermetic contract tests, consumer canary, real scenario if needed, and degradation checks for simple
consumers.

## Alternatives considered
Why an existing seam, local adapter, or product capability is insufficient.
```

## Decision States

| Status | Meaning | Who advances it |
|---|---|---|
| `proposed` | Consumer documented a concrete need | Consumer |
| `needs-evidence` | Missing workload proof, failure reproduction, or acceptance criteria | Consumer + engine owner |
| `accepted-adapter` | Existing seam is sufficient; implementation stays product-side | Consumer |
| `accepted-engine` | Universal mechanic belongs in engine/tooling | User/engine owner after architecture review |
| `planned` | Tasks, gates, risks, and release target are explicit | Implementation owner |
| `implemented-unreleased` | Code is on a branch; consumers must not adopt it | Engine owner |
| `released` | Reviewed immutable tag and permanent docs exist | Engine owner |
| `adopted` | Consumer pin and contract tests are green | Consumer |
| `closed` | Consumer confirms the workload outcome; transient docs can be removed | Both |
| `rejected` | Wrong layer, unsafe, duplicate, or no demonstrated need | User/engine owner with rationale |

Only the user settles high-impact changes to framework/runtime choice, persistence boundaries,
privacy/security, release scope, or removal of a major capability. Agents may resolve factual and
low/medium-impact details but must preserve explicit disagreement until settled.

## Engine Triage Questions

1. Is this a universal mechanic or a product policy?
2. Can an existing capability, pack, adapter, or configuration express it cleanly?
3. Would adding it force simple consumers to import/configure unused machinery?
4. What new state, persistence, side-effect, replay, budget, or observation contract appears?
5. Is every cycle/wait/fan-out/recovery path bounded?
6. Can a second product plausibly reuse the mechanism without engine special cases?
7. What prevents a consumer from bypassing it and reintroducing local orchestration?

## Delivery Requirements

An accepted engine request needs:

- a named consumer and executable acceptance scenario;
- tasks below four human-engineer hours inside one coherent iteration;
- tests for happy path, failure path, concurrency/recovery where relevant, and misuse rejection;
- degradation proof that simple imports/runs remain lightweight;
- updates to binding spec, generic guide, affected product handoff, and release notes;
- immutable tag, wheel smoke, consumer canary, and independent review.

The product remains pinned to its existing release until all release gates pass.

## Feedback After Adoption

Classify feedback before acting:

- **Defect:** tagged behavior contradicts public contract. Include minimal reproduction, tag, result,
  observation bundle or trace, and expected behavior.
- **Documentation defect:** exact snippet/claim, actual API evidence, and proposed canonical owner.
- **Missing mechanic:** use the full request template.
- **Product-specific improvement:** keep it in the product pack unless reusable evidence emerges.
- **Operational incident:** include timeline, run/wait/correlation IDs, provider/cost class, bundle,
  coordinator health, and recovery action; redact only exported copies, not internal evidence.

## Documentation Defects

Documentation bugs can break adoption as severely as runtime bugs. A correction is complete only
when:

1. the copy-paste snippet executes or validates;
2. API names/signatures are checked against the real public surface;
3. current pin/version instructions agree everywhere;
4. relative links resolve;
5. product handoffs link shared mechanics rather than duplicating them;
6. a contract test prevents recurrence where the claim is load-bearing.

## Closing A Request

The engine owner posts the annotated tag, source commit, package versions, verified release bundle,
known limits, and command-derived test/smoke evidence. The consumer verifies the published bytes,
installs its required package subset, runs product contract/canary tests, records the pin, and
reports `adopted` or a concrete defect. Only then migrate durable conclusions and delete transient
discussion files.
