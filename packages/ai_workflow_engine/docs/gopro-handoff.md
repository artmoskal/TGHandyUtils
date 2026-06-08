# GoPro Handoff - AI Workflow Engine

Status: package-level handoff for GoPro pilot adoption
Source docs checked:
`/Users/artemm/PycharmProjects/gopro-streaming/docs/architecture/workflow-execution-engine-requirements.md`,
`/Users/artemm/PycharmProjects/gopro-streaming/docs/universal_event_descriptor/HOME_INVENTORY_CASE.md`

## Fit

GoPro needs a reusable task engine, not a video-specific proof:

```text
task/profile
  -> evidence strategy
  -> agent/tool plan
  -> structured observations
  -> external/domain system
  -> feedback/search/notification
```

The workflow engine covers the reusable runtime layer:

- `WorkflowProfile` compiles task goal, evidence strategy, allowed tools, model/backend policy,
  budgets, fail mode, and scheduling into an inspectable `RuntimePlan`.
- `WorkflowRunner` forwards explicit graph runtime config and lets GoPro-owned fallback/fail policy
  handle bounded graph recursion exhaustion without leaking framework exceptions to users.
- `WorkflowConfigLoader` / `WorkflowConfigBundle` load GoPro YAML profiles and model/backend
  settings with app-supplied env override prefixes and secret-file rejection.
- `EvidenceRef` carries frame/file/URI references and evidence roles without storing raw bytes in
  shared workflow state.
- `SessionState` models task-scoped state without owning durable domain CRUD.
- `CapabilityRuntime` runs typed tools for frame selection, OCR/VLM inspection, object extraction,
  search, user clarification, and domain handoff.
- `AgentCapability` runs bounded reasoning/tool episodes with scoped registered tools, step caps,
  tool-call history, and subscription-mode metadata when a profile needs agentic selection or
  inspection instead of a fixed graph edge.
- `WorkflowScheduler` supports live drop-stale, run-latest, queue, and single-flight-cancel policy.
- `ExternalProcessCapability` can wrap CLI workers or local tools with timeout and partial-output
  salvage.
- `ExternalAdapterCapability` / `ExternalWriteRequest` can hand compact observations to a
  product-owned inventory/index system with idempotency and privacy checks.
- `gather_capabilities` supports bounded parallel evidence/tool calls.
- `JsonlCheckpointStore` and `InMemoryCheckpointStore` can persist engine loop progress without
  storing raw frame bytes.

## Home Inventory Mapping

Home inventory stresses the universal pieces:

```text
location_context evidence
  -> transition evidence
  -> contents evidence
  -> object/location extractor
  -> dedupe/merge
  -> inventory/index adapter
```

Register product capabilities like:

```python
registry.register(CapabilitySpec(name="select_inventory_evidence", kind="deterministic"), evidence_builder)
registry.register(CapabilitySpec(name="extract_visible_items", kind="llm"), item_extractor)
registry.register(CapabilitySpec(name="dedupe_inventory_observations", kind="deterministic"), deduper)
registry.register(CapabilitySpec(name="ask_location_clarification", kind="human"), ask_user)
registry.register(CapabilitySpec(name="write_inventory_observations", kind="external"), inventory_adapter)
```

The inventory/index system remains outside the engine. It owns rooms, drawers, shelves, items,
corrections, moved/removed lifecycle, search, and UI. The engine owns task/session orchestration,
evidence roles, capability selection, trace, budget, and handoff.

## Minimal Pilot

The package already contains a fake-backed inventory proof that uses the reusable runtime shape
GoPro needs: `run_toy_inventory_pilot(InventoryPilotInput(...))` in
`ai_workflow_engine.examples`. It uses `EvidenceRef` instead of raw bytes, `WorkflowScheduler`
drop/latest decisions, `HumanClarificationCapability` for ambiguous location, deterministic dedupe,
and `ExternalAdapterCapability` for the inventory write. The proof is covered by
`tests/unit/test_workflow_engine.py::test_toy_inventory_pilot_uses_evidence_refs_scheduler_and_clarification`.

Pilot flow:

```text
WorkflowGoal("inventory this cabinet/drawer")
  -> load YAML profile/model registry with WorkflowConfigLoader
  -> compile RuntimePlan from inventory profile
  -> select location_context + transition + contents EvidenceRef set
  -> extract_visible_items
  -> evaluate coverage/readability
  -> ask_location_clarification through HumanClarificationCapability if location is ambiguous
  -> dedupe_inventory_observations
  -> write_inventory_observations through adapter
```

## Acceptance Gate

The GoPro pilot is acceptable only if:

- task state stores `EvidenceRef` and compact observations, not raw frame bytes;
- unknown strategy/tool/profile names warn or fail according to strictness, never silently no-op;
- live scheduling uses `WorkflowScheduler` policy rather than ad hoc timer drops;
- every external/domain write is a registered capability with side-effect metadata;
- compiled `RuntimePlan.safety.allowed_side_effects` allows the side-effect classes each capability
  declares, or `CapabilityRuntime` will deny the capability before the handler executes;
- location and item observations include evidence roles and confidence;
- ambiguous location can route to human clarification instead of inventing a stable location;
- background work can be dropped/coalesced without releasing single-flight locks incorrectly.

## Known Limit

The package-level inventory pilot is fake-backed. GoPro still has to bind real camera/frame
selection, VLM/OCR/object extraction, location/domain state, and inventory search/index adapters.
The current engine has checkpoint stores and loop checkpoint writes, but GoPro still owns durable
home-inventory/domain state. Before promising restart-safe long-running sessions, wire latest
engine checkpoints to the product's task state and verify resume semantics with camera/profile
scheduling enabled.
