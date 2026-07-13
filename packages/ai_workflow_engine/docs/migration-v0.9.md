# Migration guide — `engine-v0.8.1` → `engine-v0.9.x`

For consumers moving their pin from `engine-v0.8.1` (GoPro, MageQA) to `engine-v0.9.x`.
**Target tag: `engine-v0.9.2`** (viewer `0.2.1`). The `engine-v0.9.0` and `engine-v0.9.1`
tags are superseded releases — do not newly pin them. Read this
before re-pinning; each item is a real contract change verified against the tag, not a style note.
Everything else is additive. Rebuild wheels from the immutable tag in a clean checkout, record the
source commit + wheel hashes, then run your adapter/canary tests before deploying.

## Breaking changes

### 1. `.human(node)` now requires a wait policy
Every human/suspension node must declare how it waits:
- `.human("gate", wait_policy=LocalWaitPolicy())` — byte-for-byte the old v0.8 suspend/resume
  (snapshot returned to the caller). This is the mechanical no-behavior-change edit.
- `.human("gate", wait_policy=DurableWaitPolicy(timeout_s=..., ...), timeout_to="escalate")` — a
  registered durable wait with a finite timeout and a declared timeout route (both mandatory).

### 2. `RuntimeLimits` is the ONE budget source, and it is strict
- Typed `RuntimeLimits` ceilings enforce **unconditionally**. The old usage-tracking flag no longer
  disables engine budgets — if you relied on a flag to turn budgets off, remove the ceilings instead.
- `RuntimeLimits` is `extra="forbid"`: an **unknown/typo'd limit key now fails config load** rather
  than being silently ignored. Fix the key name.
- `0` is an **honest hard-zero cap** (rejects every priced call); "no cap" = leave the field `None`.
  If a v0.8 config passed `0` meaning "unlimited", change it to `None`. (Products that build
  `RuntimeLimits` from their own config should map their "0 = unlimited" convention to `None` at that
  boundary — the engine treats `0` literally.)
- `budget_from_limits(RuntimeLimits | None)` is the only budget constructor; host-config/duck-typed
  budget objects are rejected. `+inf`/`NaN` ceilings are rejected (finite-only).

### 3. Planner / flow-author firewall markers are TYPED fields
The recursion firewall now reads only the typed `CapabilitySpec.is_planner` and
`CapabilitySpec.is_flow_author` fields. **Metadata / handler-attribute markers are no longer read.**
A capability that was marked a planner via `metadata["planner"]` (or similar) silently loses the
firewall unless you set the typed field — set `is_planner=True` / `is_flow_author=True` on the spec.

### 4. Failed provider attempts consume call budget
An attempted-but-failed provider call now counts against the call/USD ceilings (transport succeeded,
the model produced output that failed parse/repair). Runs that previously squeaked under a ceiling by
not counting failed attempts may now stop earlier. This is honest accounting, not a regression —
raise the ceiling only after proving the workload needs it.

### 5. `FlowArtifact` schema is discriminated / v1.5a
Authored flows use the v1.5a schema (bounded `fanout` with a REQUIRED `max_items`,
reject-never-clamp on `max_parallel`, strict unknown-field rejection). If you author flows as data,
validate against the current `FlowNodeSpec`; malformed/extra fields are rejected loudly.

## Additive in v0.9 (no action required)
Durable waits + observation segments + Related-run identity (`correlation_id`); artifact evidence
rendering + served `/artifact` route in the viewer; the `run_single_llm` / `run_single_step`
one-call facade (see `getting-started.md`); FlowArtifact v1.5a authored fanout + turnkey author.

## Reusable runtime mechanics — where they live
- **Scheduling / backpressure** (`SchedulingPolicy`: `single_flight_cancel`, `live_latest_only`,
  `run_latest`, `drop_stale`, `coalesce`, `queue`, `drop_not_queue`; `backend_key` lanes;
  `max_backend_concurrency`; slot held until the worker actually completes) and **raw-media
  double-consent** (`WorkflowNode.allow_raw_media_export` per-node opt-in AND the profile ceiling):
  see `operations.md` → "Backend Scheduling And Raw-Media Consent".
- **Observation bundle schema** (`meta.json.bundle_schema_version`, currently `1` — fail loudly on an
  unknown version) and the status-projection recipe: see `operations.md` → "Observation Lifecycle".

## Verify after re-pin
1. Installed version / source commit / wheel hash match your pin file.
2. `RuntimeLimits` config loads (no unknown keys); ceilings behave (`0` = hard-zero, `None` = no cap).
3. Any capability you treat as a planner/flow-author has the typed `is_planner`/`is_flow_author` set.
4. Every `.human(...)` node declares a `wait_policy`.
5. One representative real workflow runs with observation enabled; inspect result, costs, artifacts,
   and the viewer lifecycle.
