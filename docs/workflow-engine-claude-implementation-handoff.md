# Workflow Engine Claude Implementation Handoff

Status: implementation contract for `polish/workflow-engine`
Audience: Claude / next implementation agent
Date: 2026-06-08

## Read First

This handoff exists because the first implementation produced useful runtime primitives, but did not
finish the engine as Artem defined it.

Do not implement another set of interfaces. Do not call primitives "done" because they exist. The
finished product is an executable, LLM-oriented workflow builder/runtime: product implementers define
workflows, gates, capabilities, prompts, schemas, adapters, limits, and policies; the engine executes
the full workflow and owns orchestration mechanics.

Think "typed n8n / LangGraph-like runtime for AI workflows, wired through a DI/builder container,"
not "a folder of helper classes."

## Source Documents

- `/Users/artemm/PycharmProjects/TGHandyUtils/docs/workflow-engine-architecture.md`
- `/Users/artemm/PycharmProjects/TGHandyUtils/docs/workflow-engine-implementation-plan.md`
- `/Users/artemm/PycharmProjects/TGHandyUtils/docs/workflow-engine-acceptance-criteria.md`
- `/Users/artemm/PycharmProjects/TGHandyUtils/packages/ai_workflow_engine/README.md`
- `/Users/artemm/PycharmProjects/gopro-streaming/docs/_discussion/2026-06-08-executable-workflow-engine-contract.md`
- `/Users/artemm/PycharmProjects/gopro-streaming/docs/_discussion/2026-06-08-ai-workflow-engine-review-handoff.md`
- `/Users/artemm/PycharmProjects/gopro-streaming/docs/architecture/workflow-execution-engine-requirements.md`
- `/Users/artemm/PycharmProjects/MageQA/docs/17-qa-orchestrator-architecture.md`

## Current State

Committed base:

```text
1e3e1c2 feat(anki): add reusable AI workflow engine
```

Current branch:

```text
polish/workflow-engine
```

The package already has useful primitives:

- `CapabilitySpec`, `CapabilityRegistry`, `CapabilityRuntime`
- `RuntimePlan`, `WorkflowProfile`, `RuntimePlanCompiler`
- `WorkflowRunner`
- `WorkflowLoopController`
- `EvaluationController`
- `StructuredLLMNode`
- trace sinks
- checkpoint stores
- scheduler policy helper
- external process/agent/adapter capabilities
- image and voice seams
- fake-backed GoPro/MageQA/calendar pilots
- Anki graph integration through capability runtime

But the engine is not finished. The missing product-level feature is the executable workflow harness:

```text
WorkflowDefinition + WorkflowGoal + RuntimePlan + registered capabilities/adapters
  -> WorkflowEngine.run(...)
  -> complete workflow execution
```

## Non-Negotiable Contract

Product owns:

- domain goal
- workflow definition and domain graph shape
- node names and domain branch labels
- prompts
- schemas
- validation rubrics
- domain tools/capabilities
- external adapters
- product state, storage, delivery, UI transport

Engine owns:

- workflow execution
- node dispatch
- branching and conditional gates
- supervisor loop mechanics
- retries
- retrace
- fallback
- structured LLM parsing and repair
- fan-out/gather
- scheduling and backpressure
- cancellation semantics
- side-effect enforcement
- privacy and raw-media export policy
- cost/budget limits
- trace
- checkpoints
- pause/resume
- human clarification mechanics
- subworkflow execution
- recursive workflow-as-capability execution

If GoPro, MageQA, Anki, or a calendar workflow must manually sequence `runtime.invoke(...)` calls or
implement branch/retry/retrace/fallback/scheduler/evaluator loops, the engine is not finished.

## Target Developer Experience

The product implementer should be able to wire a workflow as a constructor/builder, similar to a DI
container plus workflow DSL.

Example:

```python
workflow = (
    WorkflowBuilder("home_inventory")
    .step("select_evidence")
    .branch(
        "evidence_quality_gate",
        branches={
            "enough": "extract_items",
            "ambiguous": "ask_location",
            "bad": "fallback_or_fail",
        },
    )
    .step("extract_items")
    .evaluate("quality_gate", on_reject=Retrace("select_evidence"))
    .step("write_inventory")
    .build()
)

engine = (
    WorkflowEngineBuilder()
    .with_config("gopro.inventory.yaml")
    .with_prompt_root("prompts")
    .with_trace_sink(JsonlTraceSink("runs/inventory.jsonl"))
    .register_capability("select_evidence", SelectEvidenceTool(...))
    .register_capability("extract_items", VlmItemExtractor(...))
    .register_capability("ask_location", HumanClarification(...))
    .register_capability("write_inventory", InventoryAdapter(...))
    .register_workflow(workflow)
    .build()
)

result = await engine.run("home_inventory", video_segment)
```

The product should not write:

```python
evidence = await runtime.invoke("select_evidence", ...)
if evidence.bad:
    ...
items = await runtime.invoke("extract_items", ...)
if items.low_confidence:
    retry_or_retrace_somehow()
...
```

That is a product mini-engine and is forbidden.

## Required Public Concepts

Keep public engine APIs product-neutral. Product workflow IDs may contain product names as data, but
engine class/field names must not contain Anki, GoPro, MageQA, Telegram, cloze, card, browser, frame,
or calendar-specific concepts.

Implement these concepts in `packages/ai_workflow_engine/ai_workflow_engine/`:

- `WorkflowDefinition`
- `WorkflowNode`
- `WorkflowEdge`
- `WorkflowBuilder`
- `WorkflowExecutor`
- `WorkflowEngine`
- `WorkflowEngineBuilder`
- `WorkflowPack` or equivalent registration hook
- `NodeExecutionState`
- `NodeResult`
- `BranchDecision`
- `Retry`
- `Retrace`
- `Fallback`
- `SubworkflowRef`

Use existing primitives where possible. Do not rewrite working code for aesthetics.

## Required Node Types

The engine must ship executable node types, not documentation-only labels:

- deterministic Python step
- typed capability/tool step
- structured LLM step
- AI decision/gate
- branch
- fan-out/gather
- evaluator/QA gate
- retry
- retrace
- fallback
- subworkflow
- recursive workflow-as-capability
- human clarification
- external process/script/tool
- external/domain adapter call
- media/image node
- voice node

Unsupported node types must fail loudly and trace the failure. Missing capability, missing model,
forbidden side effect, forbidden raw-media export, and budget exhaustion must never silently fall
back to a simpler path.

## Builder / DI Requirements

The engine should behave like an IoC container for workflow execution.

Products inject:

- prompts
- schemas
- LLM clients or factories
- Python tools
- external adapters
- validators
- QA rubrics
- cost limits
- model profiles
- trace sinks
- checkpoint stores
- storage/delivery adapters
- media/voice providers

The engine wires these into the executable workflow and enforces runtime policy.

Required ergonomic patterns:

```python
engine = WorkflowEngineBuilder().with_config(path).register_pack(ProductPack()).build()
```

```python
class ProductPack:
    def register(self, builder: WorkflowEngineBuilder) -> None:
        builder.register_capability(...)
        builder.register_workflow(...)
        builder.register_prompt_set(...)
```

```python
result = await engine.run("workflow_id", input_payload)
```

The pack may define domain specifics. It must not implement execution mechanics.

## Workflow Execution Semantics

`WorkflowExecutor` must own:

- node lifecycle
- input/output mapping
- state passing
- structured output validation
- JSON repair where configured
- branch selection
- fan-out/gather execution
- evaluator gate handling
- retry/retrace/fallback/fail policy
- subworkflow invocation
- human clarification wait/provisional/resume behavior
- scheduler and backpressure decisions
- side-effect/privacy/budget checks before handler invocation
- trace/checkpoint/artifact/usage emission
- terminal result shaping

The engine should expose enough hooks for project-specific behavior without requiring product loops.

## Subworkflow-As-Capability

Recursive composition is required, not optional.

Example:

```text
GoPro home_inventory
  -> visual_inspection_subworkflow
  -> clarification_subworkflow
  -> inventory_write_subworkflow

Anki create_card
  -> text_card_subworkflow
  -> visual_card_subworkflow
  -> voice_card_subworkflow

MageQA audit_site
  -> page_discovery_subworkflow
  -> accessibility_subworkflow
  -> performance_subworkflow
  -> report_subworkflow
```

Acceptance:

- one workflow can be registered as a capability;
- another workflow can call it;
- parent and child workflow IDs appear in trace;
- input/output/artifact/cost/failure boundaries are visible;
- child budget is inherited from parent unless explicitly narrowed.

## Anti-Simplification Gates

Add tests that fail if the engine drifts back to primitives.

Required guards:

1. No interface-only delivery:
   - every new protocol/ABC must have a working default/reference implementation;
   - every done primitive has a behavior test and at least one wiring.

2. No product mini-engine loops:
   - package examples and skeletons must not manually implement branch/retry/retrace/fallback/fan-out/
     scheduler/evaluator loops;
   - a test fixture with a hand-written `runtime.invoke` retry/branch loop should fail the guard.

3. Same executor proof:
   - one `WorkflowExecutor` runs Anki migration target, GoPro inventory toy, MageQA site-audit toy,
     and calendar-builder toy.

4. No silent fallback:
   - missing capability, unsupported node, missing model, forbidden side effect, forbidden raw-media
     export, and budget exhaustion must trace and fail/fallback only according to policy.

5. Worker-integrated scheduling:
   - long-running fake worker holds backend slot until real completion;
   - cancellation request does not release slot early;
   - second local-model call cannot start concurrently;
   - queued/preempted/dropped/cancelled decisions are traceable.

6. No product leakage:
   - engine public API stays product-neutral;
   - product names may only appear in examples, docs, workflow IDs, and tests.

7. No legacy preservation by default:
   - obey `AGENTS.md`;
   - do not preserve obsolete aliases, shapes, or code paths unless Artem explicitly approves.

## Required Acceptance Criteria

The implementation is not done until all of these are green.

### AC-1 Workflow Definition

`WorkflowDefinition` and `WorkflowBuilder` can express:

- nodes
- edges
- branches
- conditions
- subworkflows
- retry/retrace/fallback policy
- evaluator gates
- capability binding
- scheduling policy
- side-effect requirements
- budget/limit requirements

Proof:

- builder unit tests;
- invalid definition fails validation with useful error;
- unknown target node fails before execution.

### AC-2 Workflow Executor

`WorkflowExecutor` runs `WorkflowDefinition + WorkflowGoal + RuntimePlan + CapabilityRegistry` end to
end.

Proof:

- deterministic branch workflow passes;
- structured LLM node workflow passes with fake LLM;
- unsupported node fails loudly and traces;
- result includes status, output, trace summary, usage summary, artifacts, and error/fallback reason.

### AC-3 Builder / DI

`WorkflowEngineBuilder` can assemble config, prompt loader, model profiles, capabilities, workflows,
trace sink, checkpoint store, and provider adapters.

Proof:

- product-neutral toy pack registers via `register_pack`;
- run succeeds from builder-created engine;
- swapping config/profile changes selected model/limits without changing workflow code.

### AC-4 Branch / Gate

Engine-owned branch/gate node routes based on a deterministic or structured LLM decision.

Proof:

- same workflow takes two branches for two inputs;
- branch decision is traced;
- invalid branch label fails according to profile.

### AC-5 Evaluator / Retry / Retrace / Fallback

Engine-owned evaluator node handles:

- accept
- retry capability with criticism
- retrace to earlier node with criticism
- fallback
- fail
- ask user

Proof:

- fake bad output retry accepts;
- fake incomplete output retraces and accepts;
- cap exhaustion fails/fallbacks without loop;
- product code does not implement the loop.

### AC-6 Fan-Out / Gather

Engine-owned fan-out node runs N child calls with bounded parallelism and partial failure isolation.

Proof:

- 3 children, 1 timeout, 2 success;
- parent receives partial results;
- per-child trace and shared budget are recorded.

### AC-7 Subworkflow-As-Capability

Workflow can call another workflow.

Proof:

- parent workflow invokes child workflow;
- child failure boundary is visible;
- parent can fallback or fail based on child result;
- trace shows parent/child relationship.

### AC-8 Human Clarification

Human clarification is an engine mechanic with product transport adapter.

Proof:

- workflow pauses/waits;
- workflow resumes from answer;
- workflow can continue with provisional value if policy allows;
- trace records pending, answered, and provisional states.

### AC-9 Scheduling / Cancellation

Scheduling/backpressure is integrated with worker lifecycle.

Proof:

- long-running fake worker starts;
- cancellation/manual-priority request arrives;
- backend slot stays locked until worker actually completes;
- second backend call is denied/queued;
- trace records decisions.

### AC-10 Side-Effect / Privacy / Raw-Media Gate

Engine blocks forbidden side effects and raw-media export before invocation.

Proof:

- forbidden external call handler is not called;
- forbidden raw bytes export handler is not called;
- trace records denied policy;
- fail/fallback follows profile.

### AC-11 Same Executor Across Four Workflows

The same executor runs:

- Anki migration target
- GoPro inventory toy
- MageQA site-audit toy
- calendar-builder toy

Proof:

- tests for all four workflows use `WorkflowEngine.run(...)`;
- examples provide workflow definitions and packs, not manual orchestration.

### AC-12 Manual Loop Guard

Tests prevent product examples from doing engine work manually.

Proof:

- static or AST guard fails on direct orchestration loops in examples/skeletons;
- allowed direct `runtime.invoke` usage is limited to engine internals and low-level unit tests.

### AC-13 Product Neutrality

Engine public API remains product-neutral.

Proof:

- existing product-name guard stays green;
- new modules/classes do not introduce Anki/GoPro/MageQA/calendar-specific public fields.

### AC-14 Anki Green

Anki must remain functional.

Proof:

- focused Anki graph tests pass;
- current Telegram smoke behavior is not degraded;
- no obsolete compatibility layer is added silently.

## Implementation Split

Keep tasks under 4 hours. Commit at logical gates.

### Phase P1: Definition And Builder

- Add workflow definition models.
- Add workflow builder.
- Add validation for missing nodes, duplicate node IDs, invalid branch targets, unsupported node
  kinds.
- Add builder tests and product-neutral examples.

Gate: AC-1 green.

### Phase P2: Executor Core

- Add `WorkflowExecutor`.
- Run sequential step nodes through `CapabilityRuntime`.
- Add state passing and output mapping.
- Emit trace/checkpoint/result envelope.
- Add `WorkflowEngineBuilder` and `WorkflowEngine.run(...)`.

Gate: AC-2 and AC-3 green.

### Phase P3: Control Nodes

- Add branch/gate node.
- Add fan-out/gather node.
- Add evaluator node using existing `EvaluationController`.
- Add retry/retrace/fallback node behavior.

Gate: AC-4, AC-5, AC-6 green.

### Phase P4: Subflows And Human Clarification

- Add workflow-as-capability registration.
- Add subworkflow node.
- Wire parent/child trace and budget.
- Wire clarification node with existing `HumanClarificationCapability`.

Gate: AC-7 and AC-8 green.

### Phase P5: Scheduling, Privacy, Side Effects

- Integrate scheduler decisions into worker lifecycle, not only policy state.
- Add raw-media/privacy policy fields if missing.
- Enforce policy before invocation.
- Trace queued/preempted/dropped/cancelled/denied decisions.

Gate: AC-9 and AC-10 green.

### Phase P6: Same-Executor Proof

- Port calendar toy to `WorkflowEngine.run(...)`.
- Port GoPro inventory toy to `WorkflowEngine.run(...)`.
- Port MageQA site-audit toy to `WorkflowEngine.run(...)`.
- Create Anki migration target or adapter flow that uses same executor enough to prove adoption path.
- Add manual-loop guard.

Gate: AC-11, AC-12, AC-13, AC-14 green.

## Suggested Module Layout

```text
packages/ai_workflow_engine/ai_workflow_engine/
  workflow.py          # WorkflowDefinition, WorkflowNode, edges, builder
  executor.py          # WorkflowExecutor, WorkflowEngine
  builder.py           # WorkflowEngineBuilder, pack registration
  nodes.py             # node type enums/config models
  policies.py          # Retry/Retrace/Fallback helpers if not kept in models.py
  examples.py          # should use WorkflowEngine.run, not manual loops
```

Use existing `models.py` if that is cleaner, but do not create a giant unmaintainable file.

## Testing Commands

At minimum before claiming done:

```bash
./test.sh unit -- tests/unit/test_workflow_engine.py tests/unit/test_workflow_config_loader.py tests/unit/test_anki_generation_graph.py --tb=short --cov-fail-under=0
```

Then:

```bash
./test.sh unit -- --cov-fail-under=0
```

Also run:

```bash
git diff --check
```

## What Must Not Be Said

Do not say:

- "Engine is done because CapabilityRuntime exists."
- "GoPro can wire these primitives itself."
- "MageQA can implement its own coordinator loop around the runtime."
- "Anki works, so this proves GoPro/MageQA."
- "Subworkflow support exists because a callable can be registered."
- "Scheduling is done because policy decisions are unit-tested."

Correct wording until all AC are green:

```text
The runtime primitives exist. The executable workflow harness is under implementation.
The engine is done only when product workflows run from WorkflowDefinition + registered capabilities
through WorkflowEngine.run without product-owned orchestration loops.
```

## Final Done Statement

The implementation can be called finished only when this sentence is true and proven by tests:

```text
I can delete product-specific orchestration loops and still run Anki, GoPro inventory, MageQA
site-audit, and calendar-builder workflows from WorkflowDefinition + RuntimeProfile + registered
capabilities/adapters through the same WorkflowEngine.run executor.
```
