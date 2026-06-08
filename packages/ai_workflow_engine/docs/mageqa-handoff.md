# MageQA Handoff - AI Workflow Engine

Status: package-level handoff for MageQA pilot adoption
Source docs checked: `/Users/artemm/PycharmProjects/MageQA/docs/17-qa-orchestrator-architecture.md`,
`/Users/artemm/PycharmProjects/MageQA/docs/14-agentic-tester-architecture.md`

## Fit

MageQA wants a deterministic harness around an agentic core:

```text
site/rubric/budget
  -> coordinator plans scenarios
  -> bounded browser agents execute scenarios
  -> deterministic rules/adjudication
  -> coordinator reviews/deepens/curates
  -> deterministic report
```

The workflow engine now covers the reusable runtime pieces MageQA should not reimplement:

- `WorkflowGoal` / `WorkflowProfile` / `RuntimePlanCompiler` for the audit goal, rubric, budget,
  safety, and selected testers/tools.
- `WorkflowRunner` for explicit graph runtime config and workflow-owned recursion fallback when a
  MageQA subflow embeds LangGraph or another bounded graph runner.
- `WorkflowConfigLoader` / `WorkflowConfigBundle` for YAML-backed MageQA profiles and model
  registries with app-supplied env override prefixes and secret-file rejection.
- `CapabilitySpec` / `CapabilityRegistry` / `CapabilityRuntime` for coordinator, executor,
  catalog, performance, accessibility, report, and video-generation capabilities.
- `StructuredLLMNode` for schema-validated coordinator decisions and report plans.
- `ExternalProcessCapability` for subscription CLI workers such as `claude -p` / `codex exec`, with
  timeout and partial-output salvage.
- `gather_capabilities` for bounded scenario fan-out and gather.
- `WorkflowScheduler` for run-latest/single-flight/drop-stale policies where MageQA needs them.
- `WorkflowUsageSummary` and trace events for per-capability prompt/response/cost/artifact audit.
- `ExternalAdapterCapability` / `ExternalWriteRequest` for idempotent report/store handoff when a
  MageQA product system owns persistence.

## Capability Mapping

| MageQA role | Engine capability |
| --- | --- |
| Coordinator plan/review/curate | `CapabilitySpec(kind="llm")` backed by `StructuredLLMNode` |
| Browser executor agent | `AgentCapability` over scoped registered browser/MCP tools, or a product adapter that wraps `ExternalProcessCapability` |
| Deterministic catalog checks | `CapabilitySpec(kind="deterministic")` |
| Lighthouse/perf/accessibility scripts | `CapabilitySpec(kind="tool")` or `kind="external"` |
| Parallel scenario dispatch | `gather_capabilities(..., max_parallel=N)` |
| Safety and budget | `RuntimePlan.limits`, `SafetyPolicy`, usage context, capability metadata |
| Adjudication/grounding | deterministic MageQA product capability; engine traces it |
| Report/video narration | product capability; can call voice/media adapters and `ExternalAdapterCapability` where useful |
| Human review or clarification | `HumanClarificationCapability` with a MageQA-owned channel adapter |
| Restart inspection | `JsonlCheckpointStore` for engine progress; MageQA owns finding/report persistence |

When a compiled `RuntimePlan` is passed into `CapabilityContext`, `CapabilityRuntime` denies any
capability whose declared `side_effects` are not listed in `RuntimePlan.safety.allowed_side_effects`.
MageQA should bind its real browser/MCP or subscription-CLI worker as a product adapter, but the
generic episode loop, max steps/tool calls, allowed tool names, subscription metadata, and broad
side-effect class gating are no longer left to product code.

## Minimal Pilot

The package already contains a fake-backed proof that uses the same runtime shape MageQA should
adopt: `run_toy_site_audit_pilot(SiteAuditInput(...))` in
`ai_workflow_engine.examples`. It plans scenarios, fans out fake scenario executors, runs catalog
checks, adjudicates only evidence-grounded findings, and writes a report through
`ExternalAdapterCapability`. The proof is covered by
`tests/unit/test_workflow_engine.py::test_toy_site_audit_pilot_fans_out_adjudicates_and_writes_report`.

For real MageQA adoption, create a MageQA package/adapter that registers these capabilities:

```python
registry.register(CapabilitySpec(name="plan_qa_session", kind="llm"), plan_qa_session)
registry.register(browser_agent.spec, browser_agent)
registry.register(CapabilitySpec(name="run_catalog_checks", kind="deterministic"), catalog_checks)
registry.register(CapabilitySpec(name="adjudicate_findings", kind="deterministic"), adjudicate)
registry.register(CapabilitySpec(name="curate_report", kind="llm"), curate_report)
```

Pilot flow:

```text
WorkflowGoal("QA this website")
  -> load YAML profile/model registry with WorkflowConfigLoader
  -> compile RuntimePlan from rubric/budget/safety/tester profile
  -> plan_qa_session
  -> gather run_browser_scenario + run_catalog_checks
  -> adjudicate_findings
  -> optional review/deepen under max rounds
  -> curate_report
  -> deterministic report renderer
```

## Acceptance Gate

The MageQA pilot is acceptable only if:

- no MageQA code implements its own supervisor loop, fan-out, retry/retrace, trace, or budget runtime;
- browser/CLI workers are registered capabilities, not raw subprocess calls in workflow code;
- every worker has max steps/time/budget and salvages partial output;
- every finding has grounded evidence before deterministic adjudication accepts it;
- coordinator outputs are structured and schema-validated;
- trace records capability name, prompt/output or command output, cost/subscription metadata,
  artifacts, failure reason, and fallback/deepen decision.

## Known Limit

The package-level site-audit pilot is fake-backed. MageQA still has to bind real browser/MCP or
subscription-CLI workers, real screenshot/performance/accessibility adapters, and product-owned
report/finding persistence. The engine has in-memory and JSONL checkpoint stores plus loop
checkpoint writes, but MageQA must decide how a restarted QA run maps latest engine checkpoints to
its product domain state before calling restart recovery production-ready.
