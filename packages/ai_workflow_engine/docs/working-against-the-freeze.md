# Working against the engine freeze (GoPro + MageQA)

Status: active during the state-machine round (2026-06-12 → until tag `engine-v0.2.0` exists)
Audience: GoPro and MageQA integrators. Read this BEFORE wiring the engine into your repo.

## TL;DR

Pin the tag, build a wheel, never track the branch. The engine branch is under **authorized
breaking changes** right now; the tag is immutable and fully green.

```bash
# in TGHandyUtils (tags are local to this machine; both consumer repos live here too)
git -C /Users/artemm/PycharmProjects/TGHandyUtils tag -l "engine*"
#   engine-v0.1.0          <- pin THIS (= commit 50117b1, the handoff state, 706/200/21 green)
#   engine-v0.1.0-gopro    <- same commit; GoPro's earlier alias
```

GoPro already does it right: wheel built from the tag (`tools/build_engine_wheel.sh` in their
repo), vendored into their image, plus a canary test that runs the engine's toy pilot. MageQA:
copy that pattern (tag → wheel → vendor → canary). Do NOT use an editable/path dependency on the
live checkout — it will shatter under this round's changes.

## What is FROZEN-STABLE (code against this freely)

Everything your guides document at v0.1.0, in particular:

- `WorkflowBuilder` verbs (`step/branch/evaluate/fanout/plan/subworkflow/human`) and
  `WorkflowEngineBuilder`/`WorkflowEngine` registration + `await engine.run(...)`
- `LLMCallable` protocol, plain-callable clients, CLI agents, `ConsoleLLMClient`
- Budgets matrix, cost classes (`metered`/`subscription_notional`, `cost_known`), trace sinks,
  `format_trace_events`, planner + `ReplayPlanner`, flow-as-data (`FlowArtifact`),
  bounded planner depth, visualizer
- The §2 compatibility law still applies to v0.1.0 consumers: your suites stay green against
  your pinned tag by construction (a tag cannot move).

## What v0.2.0 WILL change (already implemented on the branch, tests green)

Breaking — both are mechanical, and **builder-based code does not see them**:

1. `WorkflowEdge` → `Transition` (`conditional: bool` → `policy:
   "always"|"decision"|"on_accept"|"on_reject"`); `WorkflowDefinition.edges` →
   `WorkflowDefinition.transitions`. Affects ONLY code that constructs or introspects raw
   definitions (in this repo only Anki did).
2. **Every loop-closing decision transition must declare a pre-set gate.** A branch label that
   targets an earlier node (a cycle) now REQUIRES `max_traversals` — declared via
   `.branch(..., bounds={"label": N}, exhausted={"label": "escape_label"})`. Machines with
   ungated loops fail validation loudly at build/preflight (previously they spun until the blunt
   global recursion limit). If your goal-compiler emits loops, it must emit bounds — that is the
   point.

New in v0.2.0 (additive):

- **Durable suspend/resume:** `requires_user_input` runs now return
  `result.snapshot: MachineSnapshot` (JSON-serializable when payloads are);
  `await engine.resume(snapshot_or_json, event_payload)` fast-forwards through completed nodes
  with ZERO re-execution and continues live — budgets cumulative across halves, works on a fresh
  engine instance/process with the same registrations.
- **Deterministic guards:** `engine.register_guard(name, fn)` — `fn(payload) -> label` routes a
  branch with zero LLM calls; trace records `decision_policy="deterministic"`.
- **Authored bounded loops:** `FlowNodeSpec.branch_bounds`/`branch_exhausted` — AI-authored flows
  may declare cycles, with the same mandatory gates.
- **Machine-as-data guarantee:** `WorkflowDefinition.model_dump_json()` round-trips and runs
  identically (store/transport machines as data — the goal-compiler storage path).
- Trace: `transition:taken`, `transition:exhausted`, `machine:resumed`, `machine:fastforward`;
  viz: gate annotations (`⟲≤N`), policy-distinct edges, suspended state highlighted.

## Upgrade protocol (when the round ships)

1. I cut tag `engine-v0.2.0` with migration notes appended to YOUR guide
   (`gopro-handoff.md` / `mageqa-handoff.md`).
2. You: retag → rebuild wheel → run YOUR suite → fix per migration notes (expected work:
   near-zero for builder-based code; bounds declarations if you author loops).
3. Never upgrade implicitly. The freeze model is permanent: consumers move tag-to-tag,
   deliberately.

## While I'm working: rules of engagement

- **Engine gaps you hit are requests, not forks.** Extend by addition in your repo (own
  capabilities, own packs, own `LLMCallable` clients) — never patch engine source in a vendored
  copy. File needs in your repo's discussion docs; they reach the engine through the next round.
- Anything expressible as `WorkflowBuilder` + `register_capability` needs no engine change by
  design — if you think you need an engine edit, that is itself the signal to file it.
- Cross-process resume requires JSON-serializable payloads; `MachineSnapshot.to_json()` fails
  loudly otherwise (in-process resume has no such constraint). Design your payloads accordingly
  if you want durable suspensions at v0.2.0.
