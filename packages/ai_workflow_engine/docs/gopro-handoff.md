# GoPro Engine Adoption Guide

Status: **`engine-v0.11.9` is the release candidate; do not re-pin until it is cut.**
`engine-v0.11.8` remains the current immutable release. The candidate adds an optional generic
OpenAI-compatible provider without changing GoPro's direct-VLM workflow contract. GoPro may
continue consuming only the engine wheel.
Until the candidate is cut, GoPro should pin tag `engine-v0.11.8`, record the source commit and wheel hash, and
run its sidecar canary before changing the production image. Do not
infer the actual GoPro pin from this document; the consumer repository's
pin file is authoritative for deployed state.

Read first: [getting started](getting-started.md), [framework concepts](concepts.md), and
[misuse risks](misuse-risks.md). This file contains only GoPro-specific mapping.

The line is **latest-only** — no migration guides exist. Adopt the current contract fresh; data written under older tags is rejected loudly and stays inspectable with its matching historical tag.

## Product Outcome

GoPro's current product shape is a direct video nanny/manual investigation workflow with companion
memory: bounded model workers inspect selected frames or clips, deterministic workers validate
grounding and safety policy, and the product decides when to notify, remember, or request review.
Inventory/deduplication is an example future domain pack, not the primary adoption target.

Recommended machine:

```text
video/frame evidence refs + current companion context
  -> select evidence
  -> inspect/describe (VLM or agent)
  -> validate grounded observations and uncertainty
  -> branch: accept | retry/reinspect | human/manual review
  -> project bounded companion memory
  -> produce an idempotent notification/report intent when policy allows
```

The engine owns execution and gates. GoPro owns video/nanny meaning, companion policy, storage,
and delivery. Inventory may reuse the same mechanics later without changing the engine.

## Package Choice

| Package | GoPro use |
|---|---|
| `ai-workflow-engine==0.11.9` | Required in the detection/sidecar runtime |
| `ai-workflow-tools==0.6.0` | Add for generic OpenAI-compatible clients, CLI agents, or media helpers |
| `ai-workflow-viewer==0.3.2` | Developer/diagnostic service; not required in the detector image |

> **Candidate matrix:** `engine-v0.11.9` + `ai-workflow-tools==0.6.0` +
> `ai-workflow-viewer==0.3.2`.
> Do not install this matrix until `engine-v0.11.9` is cut.


Vendoring only the engine wheel is the correct lightweight configuration for the current direct-VLM
path.

## Ownership Boundary

### Engine owns

- node execution, branching, evaluate/retrace/fallback, bounded fan-out;
- per-node model and memory selection;
- byte-safe state/checkpoint validation;
- budgets, timeouts, trace, usage, observation bundles, and evidence manifests;
- local/durable wait mechanics if GoPro later adds human review.

### GoPro owns

- frame/segment extraction and evidence lifecycle;
- nanny/companion domain models, reducers, confidence/notification policy, and DB transactions;
- configured VLM/provider clients and credentials;
- sidecar process/thread lifecycle and final inventory writes;
- durable `MemoryStore` backend and meaning of learned flows/finding history.

## One Provider Door

Construct model clients in one reviewed GoPro composition/factory module and inject them into
capabilities. Workflow modules must not instantiate provider SDK clients. This preserves model
routing, test replacement, cost attribution, and observation.

For a sidecar with one engine event loop, marshal calls from worker threads using
`asyncio.run_coroutine_threadsafe(engine.run(...), engine_loop)`. Do not create one event loop per
frame or share one executor across unrelated loops.

## Memory Mapping

GoPro was the named consumer for the shipped prompt-projection policies:

- `StructuredStateMemory` — derive compact current inventory/inspection state with a pure reducer;
- `WindowedMemory` — bound recent episode history while retaining an explicit dropped-history notice;
- `CompactingMemory` — deterministic threshold-gated compaction, no hidden model call;
- per-node `memory=` — apply rich state only to workers that need it.

Recommended namespace for companion memory:

```python
MemoryNamespace("gopro", tenant_id, subject_or_location_id, "companion_history")
```

Reducers emit dictionaries/lists/scalars/value objects or evidence references. They do not return
frames, raw bytes, iterators, arbitrary domain objects, or database handles. Memory is advisory prompt
input; current sensor evidence and deterministic validators remain authoritative.

## Evidence Rules

- Pass images to model transports as `ImageInput`.
- Persist `EvidenceRef` and archived artifact paths/fingerprints.
- Link every accepted observation to the frame/segment evidence that supports it.
- An unavailable/unconsumed image produces an inconclusive/blocked result, never a fabricated item.
- Configure artifact retention separately from the inventory database.

## Workflow Pack Shape

The current pack should model video investigation and companion decisions. The inventory pack below
is intentionally only an example of how another GoPro domain can reuse the same engine boundary.

GoPro should expose a domain pack, for example:

```python
class InventoryPack:
    def register(self, builder):
        builder.register_capability("select_evidence", ...)
        builder.register_capability("inspect_frame", ..., kind="agent")
        builder.register_capability("validate_observations", ...)
        builder.register_capability("dedupe_inventory", ...)
        builder.register_capability(
            "persist_inventory", ..., kind="external",
            side_effects=["external_write"],
        )
        builder.register_workflow(inventory_definition)
```

No product planner/executor loop belongs around this pack. If a model must plan frame work, register
it as a bounded planner capability and keep the resulting machine/plan visible.

## GoPro-Specific Misuse Risks

- Do not serialize frames/base64 into state or memory to “make replay easier.” Replay stores evidence
  references and recorded actions.
- Do not let confidence alone create inventory facts; require evidence and deterministic validation.
- Do not conflate episode memory with inventory truth.
- Do not hand-build a growing `history_json` prompt for an `AgentEpisodePlanner`; use an engine memory
  projection or request a generic projection hook through the framework lifecycle.
- Do not put viewer/tool packages into the detector image unless that runtime uses them.
- Do not bypass engine scheduling/budget policy for GPU/VLM concurrency.

## Adopter Contract AC

GoPro adoption is complete when its repository proves:

1. **Pin integrity:** installed version, tag commit, and wheel SHA match the pin record.
2. **One provider door:** workflow/domain modules cannot construct provider clients.
3. **No product orchestration loop:** product code does not choose/retry nodes around `engine.run`.
4. **Byte safety:** raw frame bytes/base64 never enter result state, memory, checkpoint, or trace.
5. **Evidence grounding:** accepted observations retain resolvable evidence references.
6. **Memory behavior:** a weak-model regression demonstrates reduced repeated inspection without
   changing control state.
7. **Budget/concurrency:** VLM calls and parallel children stop at configured limits; bounded inline
   workers are async/cooperative or process-backed, never synchronous code behind a fake timeout.
8. **Observation:** one real frame run produces a readable bundle with prompt/response, usage, and
   archived preview.
9. **Failure honesty:** unavailable vision/evidence is blocked/inconclusive, not accepted inventory.
10. **Deployment:** Mac Mini/sidecar image canary runs the pinned wheel through the public API.
11. **Economics when CLI tools are enabled:** persisted usage distinguishes metered spend from
    subscription notional, retains cache/reasoning quantities and rate versions, and treats unknown
    usage/rates as unknown. Keep call/token/window limits finite; notional is not a billing cap.

Engine-side examples: `ai_workflow_engine.examples.run_toy_inventory_pilot` and the live
qualification's GoPro scenario. Consumer-side requirements remain in GoPro's own architecture docs.

## Upgrade And Feedback

Follow [runtime upgrade operations](operations.md#upgrade-lifecycle). File missing mechanics and
post-adoption evidence using [the framework request lifecycle](extension-lifecycle.md); do not patch a
second memory, retry, or observation runtime into the sidecar.


## v0.11 Release Contract

`engine-v0.11.9` is the pending candidate for the **latest-only** line; `engine-v0.11.8` remains
current until the new tag and release directory exist. The line uses strict versioned persisted
contracts (snapshot `v0.11`, bundle meta v2, wait records `wait-v2`), one strict viewer loader, and
NO migration layer — the sealed current-contract corpus shows 0 behavior deltas vs v0.10.1, but old
persisted data is rejected loudly naming its historical tag. Adopt by re-pinning fresh
(after release: pin tag `engine-v0.11.9`, verify the published `release-manifest-v2` directory, then
install only the engine `0.11.9` wheel for the current sidecar) and
re-run your canaries before changing any deployed pin. The release makes node-context binding
explicit for every node kind and adds native Codex image attachment in the optional tools wheel;
the sealed behavior corpus is unchanged. `wait-v1` records written by v0.11.5 are non-current:
settle or discard them under v0.11.5 and start a new coordinator namespace before v0.11.6 writes
`wait-v2`. Read the annotated tag for
the exact source commit and the published release manifest for wheel hashes.
