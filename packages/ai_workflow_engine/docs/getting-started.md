# Getting Started

This guide takes a consumer from an immutable engine pin to one tested workflow. Read
[`concepts.md`](concepts.md) first if the ownership split is unfamiliar.

## 1. Choose Packages

Always install `ai-workflow-engine`. Add `ai-workflow-tools` only for reusable CLI/media tools and
`ai-workflow-viewer` only where bundles are rendered. Keeping optional packages out of simple runtime
images preserves the complexity gradient.

For OpenAI-compatible API or local-model endpoints, install
`ai-workflow-tools[openai]` and construct the client through the product's one provider factory.
The reusable adapter supports text, images, structured tools, usage truth, cancellation, and
engine-window timeout narrowing without adding provider code to engine core. See the
[tools provider guide](../../ai_workflow_tools/README.md#openai-compatible-provider).

The current immutable release is `engine-v0.11.10`. Do not install packages from a branch. Consume
the complete published directory, verify it with the tagged verifier as described in
[operations](operations.md#release-artifacts-and-verification-v0119), then record the exact bytes
installed:

```text
engine_tag=engine-v0.11.10
engine_source_commit=<git rev-parse engine-v0.11.10^{}>
engine_wheel_sha256=<sha256 of vendored wheel>
release_manifest_sha256=<sha256 of verified release-manifest.json>
```

Consumers do not rebuild as verification: wheel identity belongs to the published release bundle,
while the annotated tag identifies source. Never vendor engine source or install a live branch.
**The line is latest-only**: there is no
migration path between lines — adopt the current contract fresh. Runs, snapshots, wait records,
and observation bundles written under an older tag are rejected loudly by the current engine and
viewer; inspect that data with its matching historical tag instead.

## 2. Declare, Register, Run

The smallest executable example is [`examples/minimal_step.py`](examples/minimal_step.py). Run it
from the repository root after installing the engine:

```bash
python packages/ai_workflow_engine/docs/examples/minimal_step.py
```

The shape is always:

```python
definition = WorkflowBuilder("work_id").step("worker_name").build()
builder.register_capability("worker_name", handler, input_model=Input, output_model=Output)
builder.register_workflow(definition)
engine = builder.build()
result = await engine.run("work_id", Input(...))
```

Use strict Pydantic input/output models at external and AI boundaries. A plain deterministic helper
may use Python values, but explicit schemas make product contracts visible and fail malformed output
before downstream work runs.

## 3. Configuration

Use application YAML for stable behavior and deployment environment variables for overrides. Secrets
remain in the product's secret provider and configured clients; the engine config loader rejects
secret-looking keys and values.

Minimal YAML:

```yaml
workflow:
  profile_id: audit.default
  workflow_type: audit
  fail_mode: fail_closed
  scheduling:
    mode: run_immediately
    max_queue_size: 1
  limits:
    max_steps: 24
    max_retries: 2
    max_retrace: 1
    max_parallel_children: 4
    max_worker_calls: 12
    max_estimated_usd: 2.0
  safety:
    allowed_side_effects: [read_only]

models:
  planner:
    provider: product_provider_key
    model: configured-model-name
    temperature: 0

observation:
  enabled: true
  bundle_dir: data/observations
  capture: full
  artifacts: copy
  retention_limit: 100

# Optional. Omit to use the engine's versioned default public catalog.
pricing:
  catalog_version: product-approved-2026-07
  rates:
    - provider: codex_exec
      model_prefix: gpt-5.4
      rate_version: openai-2026-07
      source: configured_public_rate
      currency: USD
      uncached_input_per_1m: 2.5
      cached_input_per_1m: 0.25
      cache_creation_input_per_1m: 2.5
      output_per_1m: 15.0
```

Load and wire:

```python
engine = WorkflowEngine.from_config("config/workflow.yaml")
engine.register_pack(ProductPack(configured_clients))
```

Precedence is defaults → YAML files in order → explicitly supported environment overrides. Unknown
structural keys and unsafe secrets fail or warn according to the loader contract; do not build a
second config parser in the product.

The `pricing:` catalog is strict and non-secret. Longest matching model prefix wins within the
same provider; duplicate matches, unknown keys, blank identities, non-USD currencies, and
negative/non-finite rates fail at load. Use `configured_proxy_rate` when a product intentionally
models internal plan value rather than a published provider rate. There is no environment-JSON
override or compatibility fallback.

## 4. Register A Pack

Group a product workflow and its capabilities behind one small registration object:

```python
class ProductPack:
    def __init__(self, client):
        self.client = client

    def register(self, builder):
        builder.register_capability(
            "inspect", self.inspect, kind="agent",
            input_model=InspectionInput, output_model=InspectionOutput,
            side_effects=["read_only"], timeout_s=120,
        )
        builder.register_workflow(
            WorkflowBuilder("inspection").step("inspect").build()
        )
```

The pack owns domain schemas and configured adapters. It must not own a loop that calls capabilities
or interprets node statuses.

## 5. Enable Observation

With `ObservationConfig(enabled=True)`, the engine opens and finalizes a bundle automatically. The
product does not tee sinks or call bundle writers. See [`examples/observed_workflow.py`](examples/observed_workflow.py)
and [`observability-levels-feedback.md`](observability-levels-feedback.md).

## 6. Add Waits Only When Needed

- Local wait: `LocalWaitPolicy()` returns a snapshot; the caller must retain and resume it.
- Durable wait: `DurableWaitPolicy(timeout_s=...)` registers state in a product-owned
  `WaitCoordinator`; the product persists the complete returned `WaitHandle` and delivers signals
  or timeouts with `engine.deliver_wait_event(handle, event)`. A `wait_id` by itself is not a
  continuation capability.

Use [`examples/durable_wait.py`](examples/durable_wait.py) as the executable contract and read
[`operations.md`](operations.md#durable-wait-lifecycle) before implementing persistent storage.

## 7. First Consumer Contract Tests

Every adopter should test:

1. The installed version, source commit, and wheel hash match its pin file.
2. Product code imports engine APIs but engine code imports no product package.
3. All provider clients are created through one reviewed product factory/adapter door.
4. No product loop selects nodes, retries capabilities, or maintains a second run-status vocabulary.
5. Side-effect denial happens before the handler runs.
6. Result statuses project into product state through an exhaustive closed mapping.
7. Observation is enabled through configuration and a completed/failed run finalizes a readable
   bundle.
8. Any persistent `WaitCoordinator` passes
   `ai_workflow_engine.testing.run_wait_registration_conformance` plus product transaction tests,
   including separate-process creator/reuser races against the real store, exact crash retry,
   stale-handle rejection, and cancellation before handle exposure.

Run engine-side tests through this repository's root `./test.sh`; use the consumer's own approved
wrapper in its repository.

## 7b. One-Call Facade (trivial single-LLM needs)

For a single LLM/transform call you do NOT need to hand-roll a provider client — that would lose
budget, usage, trace, and parse/repair. Use the engine-backed one-call facade:

```python
from ai_workflow_engine import run_single_llm, run_single_step

text = await run_single_llm(prompt, ...)             # one real engine-run behind a one-liner
result = await run_single_step(capability, payload)  # your capability on the real executor
```

It is sugar over `engine.run`, never a bypass: budget, usage, trace, and loud failures stay intact,
and the simple tier loads no advanced modules. Pass `return_result=True` to `run_single_step` for the
full run envelope.

## 8. Adoption Definition Of Done

- One real product workflow runs through `engine.run` with no local orchestration substitute.
- Product schemas, prompts, clients, and storage remain product-owned.
- Budgets, safety, trace, usage, and observation are visible on both success and failure.
- A restart/retry test covers every durable adapter the product owns.
- The product handoff's acceptance checklist passes.
- Pin and operational recovery are documented in the consumer repository.

Next: read [`operations.md`](operations.md) and the matching product handoff.
