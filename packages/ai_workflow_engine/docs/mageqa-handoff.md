# MageQA — AI Workflow Engine Usage Guide

Status: **engine implemented and ready for adoption** (2026-06-08). The `WorkflowDefinition` /
`WorkflowExecutor` / DI layer this doc previously waited on is live and proven (Anki migrated +
live-tested; one `WorkflowExecutor` runs four example workloads — including the site-audit fan-out).
Source needs: `/Users/artemm/PycharmProjects/MageQA/docs/17-qa-orchestrator-architecture.md`,
`/Users/artemm/PycharmProjects/MageQA/docs/14-agentic-tester-architecture.md`.

This is a **how-to**: declare your audit flow, register your capabilities, run it. The engine owns the
deterministic harness (plan→fan-out→adjudicate→deepen→report mechanics, scheduling, side-effect/budget
gates, trace, subworkflows); you own the agentic/domain core (browser agents, rubric, grounding,
report). **You write no coordinator loop** (a static guard enforces this).

---

## 1. The whole adoption in three steps

```python
from ai_workflow_engine import (
    WorkflowBuilder, WorkflowEngine, Retrace, Fallback, BranchDecision,
)

# (1) DECLARE the audit flow — plan -> fan-out scenarios -> adjudicate -> deepen -> report.
audit_site = (
    WorkflowBuilder("audit_site")
    .step("plan_qa_session")                         # rubric/budget in -> list of scenario inputs
    .fanout("run_scenarios", capability="run_browser_scenario",   # bounded parallel browser agents
            items_key="plan_qa_session", max_parallel=4)          # partial-failure isolated
    .step("adjudicate_findings")                     # keep only evidence-grounded findings
    .evaluate("coverage_gate", on_reject=Retrace("plan_qa_session"))  # deepen: re-plan if thin, bounded
    .subworkflow("report", workflow=REPORT_FLOW)     # report sub-flow (curate -> render -> write)
    .build()
)

# (2) WIRE dependencies via DI.
engine = WorkflowEngine.from_config("config/mageqa.audit.yaml")   # rubric/budget/per-role models/safety
engine.register_pack(SiteAuditPack())

# (3) RUN.
result = await engine.run("audit_site", SiteAuditInput(url="https://example.test", rubric=[...]))
# result.status / .output (report) / .node("run_scenarios").output / .usage / .trace
```

No MageQA-owned supervisor loop, fan-out loop, retry/deepen loop, or trace/budget runtime. The guard
`test_examples_contain_no_product_orchestration_loops` fails the build if a pack hand-rolls those.

---

## 2. Node kinds → MageQA roles

| Builder call | Node | MageQA role |
|---|---|---|
| `.step("plan_qa_session")` (capability `kind="llm"` via `StructuredLLMNode`) | step | coordinator plan: schema-validated scenario list |
| `.fanout(id, capability="run_browser_scenario", items_key=…, max_parallel=N)` | fanout | bounded browser/agent scenario execution, partial-failure isolated, shared budget |
| `.step("adjudicate_findings")` (deterministic) | step | grounding/adjudication: accept only evidence-backed findings |
| `.evaluate(id, on_reject=Retrace("plan_qa_session"))` | evaluate | review/deepen: re-plan or re-run scenarios under a bounded round cap, with criticism |
| `.branch(id, {label: target}, decider=…)` | branch | route by severity / coverage / "needs human review" |
| `.subworkflow("report", workflow=…)` | subworkflow | report sub-flow (curate → render → write), recursive, own budget |
| `.human(id)` | human | optional human review/triage (pause / provisional / resume) |
| `.step("write_report", side_effects=["external_write"])` | step | idempotent report/store handoff |

Browser/CLI workers are **capabilities**, not raw subprocess calls in flow code:
- an in-engine **bounded agent**: `AgentCapability` over scoped registered browser/MCP tools (step
  caps, tool-call caps, scoped tool names, salvage/partial, subscription-mode metadata); or
- a product adapter wrapping `ExternalProcessCapability` (e.g. `claude -p` / `codex exec`) with
  timeout + partial-output salvage.

---

## 3. Registering capabilities (a `WorkflowPack`)

```python
class SiteAuditPack:
    def register(self, builder) -> None:
        builder.register_capability("plan_qa_session", PlanQaSession(...),       # StructuredLLMNode-backed
                                    kind="llm", metered=True, output_model=SessionPlan)
        builder.register_capability("run_browser_scenario", browser_agent,       # AgentCapability or adapter
                                    kind="agent", side_effects=["browser_drive"], timeout_s=120)
        builder.register_capability("adjudicate_findings", Adjudicate(...), kind="deterministic")
        builder.register_capability("coverage_gate", CoverageEvaluator(...), kind="deterministic")
        builder.register_capability("write_report", ReportAdapter(sink),
                                    kind="external", side_effects=["external_write"])
        builder.register_workflow(audit_site, profile=audit_profile)
        builder.register_workflow(REPORT_FLOW)        # registered so the subworkflow node can call it
```

Handler signature: `def handler(context, payload) -> output | CapabilityResult` (sync or async); an
object exposing `.spec` (e.g. `AgentCapability`, `ExternalAdapterCapability`) is accepted directly.
The `CapabilitySpec` carries input/output schema, `kind`, `side_effects`, `metered` (cost class),
`timeout_s`, `max_attempts`; the engine enforces them and **denies a capability whose `side_effects`
are not in the profile's `allowed_side_effects` — before the handler runs**.

**Coordinator decisions are data, not loops:** `plan_qa_session` returns a structured plan
(schema-validated, JSON-repaired by `StructuredLLMNode`); `coverage_gate` returns
`CapabilityResult(status="rejected", metadata={"criticism": "thin coverage on checkout"})` and the
engine deepens via `Retrace`, bounded by `RuntimeLimits.max_retrace`.

---

## 4. Config & profile (`from_config`)

```python
profile = WorkflowProfile(
    workflow_type="audit_site",
    safety=SafetyPolicy(fail_mode="fail_closed",
                        allowed_side_effects=["browser_drive", "external_write"]),
    limits=RuntimeLimits(max_parallel_children=4, max_retrace=2, max_estimated_usd=2.00),
)
```

Per-role models go in the YAML model registry (`ModelProfile` per capability/role); rubric/budget come
in via `engine.run(..., constraints={...})` or the goal. Swap the profile to change models/budget/
safety without touching the workflow (proven by `test_engine_from_config_swaps_profile`).

---

## 5. MageQA-specific engine features (do NOT reimplement)

- **Fan-out / gather.** `.fanout(..., max_parallel=N)` runs N scenarios concurrently with bounded
  parallelism and **partial-failure isolation** (a timed-out/failed scenario doesn't sink the run;
  the parent gets the successes, status `partial`) — proven by `test_fanout_gather_isolates_partial_failure`.
- **Bounded agents.** `AgentCapability` runs a scoped tool/reasoning episode with step + tool-call
  caps, allowed-tool scoping, salvage/partial output, and subscription-mode metadata — so a runaway
  agent can't blow the budget. MCP/browser tools are reachable as registered capabilities.
- **Deepen loop = evaluator retrace.** `.evaluate("coverage_gate", on_reject=Retrace("plan_qa_session"))`
  re-plans/re-runs under `max_retrace`, threading criticism — the engine owns the loop (proven by
  `test_evaluate_retrace_to_earlier_node_then_accepts`).
- **Grounding gate.** Adjudication is a deterministic capability that accepts only evidence-grounded
  findings; carry evidence as `EvidenceRef(role=…, uri=…)` (no raw bytes in state). Findings reference
  their `evidence_ref_id`.
- **Cost / subscription.** Mark metered capabilities `metered=True`; the engine tracks usage/cost and
  **denies metered calls once `max_estimated_usd` is exhausted** (proven by
  `test_budget_exhaustion_denies_metered_capability`). Subscription/flat-rate workers report
  subscription metadata via `AgentCapability`.
- **Per-role models.** Register a `ModelProfile` per role; `from_config` selects them; swapping config
  changes models without code edits.
- **Trace.** `format_trace_events(result.trace, usage=result.usage)` renders capability name,
  decision, key output, timings, cost, artifacts, and deepen/fallback decisions — your `ProcessingTrace`
  maps onto the engine `TraceSink` (`InMemoryTraceSink` / `JsonlTraceSink`).
- **Subworkflows (recursive).** `page_discovery` / `accessibility` / `performance` / `report` are
  separate `WorkflowDefinition`s registered as capabilities and composed via `.subworkflow(...)` on the
  same executor, with inherited/narrowed budget and parent/child trace.
- **Fail-closed safety propagation.** `SafetyPolicy(fail_mode="fail_closed")`; forbidden side effects /
  missing capabilities / unsupported nodes fail loudly + trace, never silently no-op.

---

## 6. Migration recipe

1. Keep your coordinator/agent/check/report functions — register each as a capability with a
   `CapabilitySpec` (schema + side-effect class + timeout + cost). Wrap browser/CLI workers as
   `AgentCapability` or `ExternalProcessCapability`.
2. Make the coordinator emit a **structured plan** (scenario list); make review/deepen an **evaluator**
   returning accept/reject + criticism; make routing a **branch decider**.
3. Express plan→fan-out→adjudicate→deepen→report with `WorkflowBuilder`; delete your coordinator loop,
   fan-out/gather loop, retry/deepen loop, and any trace/budget runtime.
4. Move per-role models / rubric defaults / budget / safety into a YAML profile; load via `from_config`.
5. Replace your run entrypoint with `await engine.run("audit_site", site_input)`.

**Done when** you can delete MageQA orchestration loops and still run from
`WorkflowDefinition + registered capabilities` through `engine.run`. Verify with a `format_trace_events`
dump + the no-product-loop guard.

---

## 7. Reference (import from `ai_workflow_engine`)

`WorkflowBuilder, WorkflowDefinition, WorkflowNode, WorkflowEdge, WorkflowEngine, WorkflowEngineBuilder,
WorkflowPack, WorkflowExecutor, WorkflowRunResult, NodeResult, BranchDecision, Retry, Retrace, Fallback,
SubworkflowRef, AgentCapability, EvidenceRef, ExternalWriteRequest, ExternalWriteResult,
ExternalAdapterCapability, ExternalProcessCapability, HumanClarificationCapability, StructuredLLMNode,
SchedulingPolicy, SafetyPolicy, RuntimeLimits, WorkflowProfile, ModelProfile, WorkflowGoal,
InMemoryTraceSink, JsonlTraceSink, format_trace_events`. Runnable example: `ai_workflow_engine/examples.py`
(`build_demo_engine`, `SiteAuditPack`, `run_toy_site_audit_pilot`).
