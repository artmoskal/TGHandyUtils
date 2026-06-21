# MageQA T2 (cross-run) memory — questions to forward (S1)

Status: draft for Artem to forward to MageQA — **[claude] answered inline 2026-06-21** (see "Answers" section below; one verified correction up front)
Created: 2026-06-21
Purpose: S1 from the engine plan — gather MageQA's concrete cross-run-memory requirement so we can
(a) decide whether the **engine ships a durable T2 backend** or **MageQA implements one behind the
shipped contract**, and (b) confirm the shipped `MemoryStore` contract shape is sufficient before we
tag the engine and write the MageQA memory recipe (Track U). This gates the MageQA handoff.

---

## Context (answer against what's ALREADY shipped, not in a vacuum)

The engine already ships the **memory contract + a dev backend**; durable/semantic backends are
deliberately deferred until your need is concrete. Specifically:

- **`MemoryStore` Protocol** (deterministic, no LLM in the loop):
  `put(record, idempotency_key=None)` · `get(namespace, key)` ·
  `search(namespace, query=None, metadata_filter=None)` · `delete(namespace, key)`.
- **`MemoryRecord`**: `namespace` (a structured tuple), `key`, `value: dict`,
  `evidence_refs: [EvidenceRef]`, `metadata: dict`, `source`, `schema_version`.
- **`InMemoryMemoryStore`** (dev/test only): single-process, **not durable**; `search` is
  substring-over-value + exact `metadata_filter` — **no semantic/vector recall**; `put` enforces
  idempotency (reusing a key for a *different* record raises).
- **Design stance:** recalled memory is **non-authoritative + evidence-linked**; the engine owns the
  contract, products own record shapes + recall policy; orchestrator memory is **deterministic
  put/get**, not LLM-extracted.

**The decision your answers drive:** does the engine build you a **durable** backend (sqlite/postgres/
your infra), or do you implement a durable `MemoryStore` behind the Protocol above (engine stays as-is)?

---

## Questions

**Must-answer (these size the work + the decision):**

1. **Records.** What exactly do you want to remember *across runs*? Give 2–3 concrete examples with
   fields (e.g. `test_id`, outcome, failure reason, the approach that worked, flaky flag). We map
   these to `MemoryRecord.value` / `metadata`.
2. **Recall shape.** Is **exact-match + filter** enough (`get` by key, `search` by `metadata_filter`),
   or do you need **semantic/similarity** recall ("find tests *like* this")? The shipped `search` is
   substring+filter only — semantic needs an embedding backend we'd build only if you need it.
3. **Namespace & isolation.** What are your natural scoping dimensions and key? (per target app? per
   test suite? per orchestrator run-family? per tenant?) What must **never** leak across (the hard
   isolation boundary)?
4. **Concurrency.** Do multiple orchestrations run **in parallel** writing the **same** namespace? Do
   you need idempotency / write-ordering / locking guarantees? (This is the main driver of durable
   backend choice.)
5. **Volume & retention.** Rough record count and growth rate; any TTL / eviction / "forget after N"?
6. **Backend ownership (the key call).** Do you want the **engine to ship a durable backend** — and if
   so, on what storage (sqlite? postgres? your existing infra?) — **or** will you implement a durable
   `MemoryStore` **behind the shipped Protocol** yourselves (fastest; no engine change)?

**Helpful (shape the recipe, not blocking):**

7. **Read/write points.** Where in your orchestrator flow do you **read** memory (a planner node? a
   capability? a subworkflow?) and **write** it (after each test? at run end?)?
8. **Provenance.** OK that recall is **non-authoritative** and every record carries
   `source` + `evidence_refs` + `schema_version`? Do you need to trace a recalled fact back to its
   origin run?
9. **Extraction.** Deterministic put/get (you write structured facts) — or do you actually want
   **LLM-driven extraction/consolidation** from transcripts (langmem-style)? We planned deterministic
   for the orchestrator; flag if that's wrong for you.

---

## Answers — [claude] (MageQA side), 2026-06-21

**[claude] correction first (verified, gates everything below):** the `MemoryStore` Protocol +
`MemoryRecord` + `InMemoryMemoryStore` exist in `ai_workflow_engine/memory.py` on the
`polish/workflow-engine` HEAD, but they are **NOT in `engine-v0.2.0`** — the tag MageQA actually
vendors (`vendor/ai_workflow_engine-0.2.0-py3-none-any.whl`; v0.2.0 `__init__` exports
`InMemoryCheckpointStore` etc. but no `MemoryStore`). So "already shipped" = shipped on HEAD, not in
the wheel we consume. **Net: regardless of Q6, the one engine action required is to cut a tag that
includes `memory.py`, which MageQA then vendors.** The contract shape below is fine as-is; no
contract change needed before that tag.

**Q1 — Records (3 concrete kinds MageQA wants across runs).**
- `learned_flow` — per-target navigation for a flow. `value`: `{flow, steps:[{action,selector,url}], success_count}`; `metadata`: `{target, flow, status: "verified"|"stale"}`; `key`: the flow name (`"add_to_cart"`, `"checkout"`, `"login"`); `evidence_refs`: the screenshot that last proved it. (This is the draft §5 "learned stable selectors/paths" + the §6 compile-to-static substrate.)
- `finding_history` — per-target finding across runs (drives **persistent** regression diff + false-positive suppression). `value`: `{check_id, title, severity, first_seen_run, last_seen_run, human_status, reviewer_note}`; `metadata`: `{target, category, severity, human_status: "kept"|"dismissed"|"false_positive"}`; `key`: a stable finding identity (`check_id|category|url|title`). NOTE: this is the cross-run counterpart of the `baseline_diff` capability MageQA just re-homed (`packs/regression/baseline.py`) — that capability would read/write THIS record kind.
- `target_profile` — detected platform/signals. `value`: `{platform, version, signals, relevant_probes}`; `metadata`: `{target, platform}`; `key`: `"profile"`.
- (helpful) `flaky_check` — `value`: `{check_id, divergence_count}`; `metadata`: `{target, flaky: true}`.

**Q2 — Recall shape: exact-match + filter is ENOUGH for launch.** `get` by key (flows, profile), `search` by `metadata_filter` (`{target, human_status}` for findings). Semantic/similarity ("findings *like* this across DIFFERENT targets" — cross-target learning) is a genuine future enhancement but **not blocking** — do NOT build the embedding backend yet. The shipped substring+filter `search` covers the launch need.

**Q3 — Namespace & isolation.** Namespace tuple: `("mageqa", tenant_id, target_origin, record_kind)`; `key` = the within-kind id from Q1. `target_origin` = scheme+host (staging vs prod stay separate). **Hard isolation boundary = `tenant_id`** (agency client): one client's target memory must NEVER surface in another's audit. Even single-tenant today, carry `tenant_id` in the namespace so isolation is structural, not a query convention.
> **[claude] review (2026-06-21):** ⚠️ arity mismatch — the shipped `MemoryNamespace` is
> `tuple[str, str, str, str, str, str]` (a **fixed 6-string** pydantic tuple). Your 4-field
> `("mageqa", tenant_id, target_origin, record_kind)` will **fail `MemoryRecord` validation** (needs
> exactly 6). Reconcile before tag: pad to the 6 defined positions, or right-size/name the tuple.
> See the consolidated review below (correction #2).
> **[codex] resolved (2026-06-21):** right-sized to
> `MemoryNamespace(product, tenant, subject, kind)`; MageQA uses `target_origin` as `subject`.

**Q4 — Concurrency (the backend-sizing answer): modest.** Within one run, the scenario fanout writes the same target namespace but DIFFERENT keys (distinct flows/findings) → no contention. Across runs, two audits of the same target could update the same key (e.g. a selector) → **last-write-wins is acceptable**, and the shipped `put` idempotency is sufficient. **No distributed locking / strict write-ordering needed.** Atomic per-key put is all the durable backend must guarantee.

**Q5 — Volume & retention: sqlite-scale.** ~100s of records per target; an agency with 100s of sites → ~10k–100k total, growing a handful of records per audit. Retention: **keep indefinitely** (findings = audit trail, flows = the value); **no hard eviction.** Staleness is a FLAG (mark a flow `stale` if not re-verified in N runs / X months), not a delete. Hard delete only on **tenant/target offboarding** (GDPR-style "forget this customer's target") → needs a `delete`-by-namespace sweep (the shipped `delete(namespace, key)` covers per-key; a namespace-wide purge helper would be convenient but MageQA can loop `search`→`delete`).

**Q6 — Backend ownership: BYO.** MageQA implements a durable **sqlite-backed `MemoryStore` behind the shipped Protocol**; the engine ships NO durable backend. Rationale: requirements are sqlite-scale + last-write-wins + exact recall (Q4/Q5); MageQA already owns a sqlite persistence layer (`mageqa.db` / event-journal) that the dashboard reads, so a sqlite `MemoryStore` reuses that infra and keeps storage choices in the product (matches your design stance "products own record shapes + recall policy"). The only engine action is the tag from the correction above.

**Q7 — Read/write points.** READ at **plan** (the strategy/planner node loads `target_profile` + `finding_history` + `learned_flow` → shapes the plan: re-check prior findings, reuse selectors, skip suppressed FPs) and at **run_scenario** (the browser-agent instrument reads the `learned_flow` for its flow). WRITE incrementally when a scenario **confirms** a working selector/flow, and at **run-end** after adjudication (persist `finding_history` for next-run diff, update `target_profile`, flag stale flows).

**Q8 — Provenance: yes, required.** Non-authoritative + `source` + `evidence_refs` + `schema_version` is exactly right and MageQA needs the back-trace: the dashboard shows "selector last verified in run X", "finding first seen in run Y", and staleness keys off the origin run. The shipped `MemoryRecord` already carries all of this — **no contract change**.

**Q9 — Extraction: deterministic, agreed.** MageQA writes STRUCTURED facts it already holds as typed objects (findings, selectors, profiles); the judgment happens in the audit capabilities (vision/skeptic) and the RESULT is a structured fact → deterministic `put`. No LLM extraction from transcripts for orchestrator memory. (LLM-driven cross-target consolidation could be a future pattern-miner, not this path.)

**Contract-sufficiency verdict:** the shipped `MemoryStore` Protocol + `MemoryRecord` shape is **sufficient for MageQA's launch as-is** — namespace-as-structured-tuple holds `(mageqa, tenant, target, kind)`; the only deltas vs the dev backend are (a) **durability** → MageQA's sqlite impl (BYO), (b) **semantic search** → deferred. So: **cut the engine tag including `memory.py` unchanged; MageQA ships the sqlite store + the memory recipe.** A namespace-wide `purge(namespace)` helper would be a nice-to-have (tenant offboarding) but is not blocking.

## [claude] engine-side review of MageQA's answers + for-codex checklist (2026-06-21)

Verified against the engine repo (git tags, `memory.py`, `__init__.py`). **The answers are sound and
the Q6 = BYO-sqlite decision is the right lightweight path** (engine ships the contract, MageQA ships
the durable store). Three corrections to the wrap-up verdict, all code-verified:

1. **The memory slice is UNCOMMITTED, not "on HEAD."** `memory.py`/`test_memory.py` are **untracked**
   (`git status: ??`) and absent from `HEAD`; the validator/wiring files are modified-not-committed.
   So the real first action is **commit Track E+M+V (green) → THEN tag**, not "just cut a tag." (The
   top correction was right that `engine-v0.2.0` lacks memory; it was wrong that it's on HEAD.)
2. **Namespace arity mismatch (the one real contract-fit issue).** `MemoryNamespace =
   tuple[str, str, str, str, str, str]` — a **fixed 6-string positional tuple, undocumented**; pydantic
   enforces exactly 6. MageQA's verdict ("holds `(mageqa, tenant, target, kind)`" = 4) does **not** fit
   as-is. Resolve before tag: (a) define + document the 6 positions and have MageQA pad, or (b)
   right-size/name the tuple. A 6-`str` positional tuple with no field names is itself a smell — a small
   frozen dataclass / `NamedTuple` would be safer.
   **[codex] resolved 2026-06-21:** chose (b), `MemoryNamespace(product, tenant, subject, kind)`.
3. **Exports OK (confirmed):** the working-tree `__init__` exports `MemoryStore`/`MemoryRecord`/
   `MemoryNamespace`/`InMemoryMemoryStore`, so MageQA's BYO import works **once tagged** (v0.2.0
   exported none of these).

**Otherwise confirmed sound:** Q2 exact+filter, semantic deferred ✅; Q4 last-write-wins + shipped
idempotency → simple sqlite is enough ✅; Q7 read-at-plan/write-at-run-end is MageQA's own capabilities
calling the injected store (engine just exposes the contract) ✅; Q8 provenance already in
`MemoryRecord` ✅; Q9 deterministic `put` ✅. Optional (not blocking): the `purge(namespace)` helper.

**[open] for codex to check:**
- **a.** Namespace decision (#2): keep 6-`str` tuple (MageQA pads) vs right-size/name the fields —
  does the shipped 6-`str` match codex's original structured-namespace intent, or did it drift?
- **b.** Commit boundary before tag (#1): Track E+M+V are intermixed with unrelated dirty files
  (anki/docs/scripts) — codex confirms a clean **engine-only** commit before tagging.
- **c.** MageQA repo claims unverifiable here: `packs/regression/baseline.py`, `mageqa.db`/event-journal
  (Q1/Q6) — codex or MageQA confirms they exist as described.
- **d.** Does BYO-sqlite need ANY engine change beyond exposing the contract (e.g. make `MemoryStore`
  `runtime_checkable`, add `purge`)? codex confirms the Protocol is implementable as-is.

## [claude] proposed engine changes (for codex to review + implement) — 2026-06-21

Scope discipline: MageQA's answers VALIDATE the minimal core (BYO durable store, deterministic `put`,
exact-match recall) — so do NOT add a durable backend, semantic/vector search, langmem, or any T2
read/write wiring. Only one change has a concrete named-consumer justification; two are optional.

**P-mem-1 — Namespace ergonomics + field reconciliation. DO before the tag (it's a contract MageQA builds against).**
- *Problem:* `MemoryNamespace = tuple[str, str, str, str, str, str]` (`memory.py:16`) is positional +
  undocumented. The designed 6 fields (product, tenant, workflow_type, run_family, schema_version,
  memory_kind) have **no obvious slot for the `target`/subject dimension MageQA scopes by**
  (`(mageqa, tenant, target_origin, record_kind)`), and MageQA proposed a 4-tuple that fails the
  fixed-6 pydantic validation.
- *Proposal:*
  - (a) Make it **self-documenting** — a `NamedTuple` (or frozen dataclass) with named fields in
    `memory.py`. A `NamedTuple` stays tuple-compatible: hashable `InMemoryMemoryStore` dict key,
    pydantic validates field-wise, positional construction still works → existing tests/usages keep
    working (codex verifies).
  - (b) **Reconcile the field SET with real consumer scoping** — confirm the fields actually serve
    MageQA (product, tenant, **target**, kind) AND GoPro; if `target`/subject has no home, add or
    rename a field rather than forcing consumers to overload `workflow_type`/`run_family`. codex owns
    this design call (codex designed the namespace).
- *Constraint:* backward-compatible with current namespace construction + `InMemoryMemoryStore` keying
  + `MemoryRecord` validation; all existing memory tests stay green; no raw-byte/privacy change.
- *Acceptance:* fields are named/documented; a MageQA-shaped namespace constructs + validates **without
  padding meaningless slots**; `./test.sh … test_memory.py test_state_machine.py test_agent_planner.py` green.
- *Decision for codex:* `NamedTuple` vs frozen dataclass vs document-only; and the final field set.

**P-mem-2 — `purge(namespace) -> int` helper. OPTIONAL, defer unless codex sees a reason.**
- For tenant/target offboarding (MageQA Q5, GDPR "forget this customer"). MageQA can loop
  `search→delete` today, so add to the Protocol + `InMemoryMemoryStore` only if codex judges it belongs
  in the contract; otherwise leave to products. Named-consumer-gated.

**P-mem-3 — `@runtime_checkable` on `MemoryStore`. OPTIONAL, trivial.**
- Lets the engine/products `isinstance`-validate a BYO store at registration. Tiny DX win; codex's call.

**Sequencing:** P-mem-1 lands in the **same commit as Track E+M+V, before the tag** (so MageQA builds
against the final namespace). P-mem-2/3 are non-blocking. Reminder (review correction #1): the engine
work is **uncommitted** — codex commits engine-only (clean boundary) → applies P-mem-1 → tags.

**[codex] implementation note (2026-06-21):** accepted P-mem-1 with
`MemoryNamespace(product, tenant, subject, kind)` as a tuple-compatible `NamedTuple`; `subject` is the
consumer-owned target/project/run-family slot, so MageQA uses
`MemoryNamespace("mageqa", tenant_id, target_origin, record_kind)` without padding. Added
`@runtime_checkable` to `MemoryStore` for BYO-store ergonomics. Deferred `purge(namespace)` because it
would expand the required Protocol surface; MageQA can loop `search` -> `delete` until a second
consumer or offboarding implementation proves the helper belongs in core.

## What we'll do with the answers
- **If BYO** (Q6 = you implement behind the Protocol): we finalize the MageQA memory recipe + tag the
  engine — minimal/no new engine code; you ship your durable store.
- **If engine ships durable**: we scope a durable `MemoryStore` backend from Q1–Q5 (deferred work,
  unblocked by these answers).
- **Either way**: Q1–Q4 confirm whether the shipped contract shape needs any change *before* we cut
  the engine tag and write the handoff.
