# Runnable Examples

These scripts are intentionally small and use public APIs only. They complement the broader,
test-backed product-neutral examples in `ai_workflow_engine/examples.py`.

Run from the repository root after installing the engine packages:

```bash
python packages/ai_workflow_engine/docs/examples/minimal_step.py
python packages/ai_workflow_engine/docs/examples/observed_workflow.py
python packages/ai_workflow_engine/docs/examples/durable_wait.py
```

| Example | Proves | Adds |
|---|---|---|
| [`minimal_step.py`](minimal_step.py) | One typed deterministic capability through `engine.run` | Nothing optional |
| [`observed_workflow.py`](observed_workflow.py) | Config-first bundle lifecycle and standalone group reader | `ai-workflow-viewer` |
| [`durable_wait.py`](durable_wait.py) | Explicit timeout route, coordinator registration, signal delivery, grouped completion | Wait coordinator + observation |

These are framework examples, not product templates. Product-specific graph shapes and acceptance
gates live in the handoffs one directory above.
