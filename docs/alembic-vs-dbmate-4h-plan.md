# TGHandyUtils Database Migration Hardening Plan

## Goal

Deliver a production-safe migration architecture with a **single runtime migration authority**, explicit failure behavior, deterministic upgrade path for legacy DBs, and test coverage for corner cases.

Hard planning rule: **every task in this plan is capped at 4 hours**.  
If a task exceeds 4h, split it before implementation.

---

## Target Architecture (End State)

1. `main.py` calls one bootstrap entrypoint: `database/migration_runner.ensure_schema_ready()`.
2. Versioned migrations live under framework migration folders only.
3. No DDL in repositories/services/startup outside migration runner.
4. Legacy DBs are upgraded through a controlled bridge path, then version-stamped.
5. Schema contract verification runs after migration and fails fast with actionable logs.
6. Startup migration is concurrency-safe (lock + retry policy).

---

## Global Success Criteria

1. Fresh DB startup succeeds and creates all required tables/columns/indexes.
2. Existing pre-framework DB startup succeeds without data loss.
3. `user_preferences_unified.utc_offset` exists after bootstrap in all paths.
4. No "applied migration but missing column" state is possible after bootstrap.
5. Migration failure is explicit and aborts startup.
6. Test matrix covers all listed corner cases.

---

## Work Breakdown (Granular, <=4h per Task)

## Phase A - Contract + Tooling Foundation

| ID | Max Time | What (Where) | Why | Output |
|---|---:|---|---|---|
| A1 | 2h | Extract canonical schema contract from `database/migrations.py` into a checklist doc (`docs/schema-contract.md`). | Remove ambiguity about required DB state. | Signed-off schema contract (tables, columns, indexes, constraints). |
| A2 | 3h | Add `database/schema_contract.py` containing machine-readable expected schema for runtime verification. | Single source for verification and tests. | `EXPECTED_SCHEMA` + helper checks. |
| A3 | 2h | Add migration framework dependencies in `environment.yml`; regenerate local env. | Ensure runtime and CI can execute migrations. | Updated dependency manifest and successful env build. |
| A4 | 2h | Initialize migration framework scaffold (`alembic.ini`, `alembic/env.py`, `alembic/versions/`). | Establish versioned migration structure. | Migration framework files committed. |
| A5 | 2h | Configure dynamic DB URL injection in `alembic/env.py` for runtime-provided `DATABASE_PATH`. | Needed for per-env DB paths and test DBs. | URL override works from code and CLI. |

## Phase B - Baseline + Legacy Bridge

| ID | Max Time | What (Where) | Why | Output |
|---|---:|---|---|---|
| B1 | 4h | Create baseline revision in `alembic/versions/*_baseline.py` matching contract in A1/A2 (all current tables/indexes). | Establish deterministic starting version. | Baseline revision with upgrade/downgrade functions. |
| B2 | 3h | Create `database/migration_runner.py` with `ensure_schema_ready(db_path: str)`. | Centralized runtime authority. | Migration runner module and logger instrumentation. |
| B3 | 4h | Implement legacy-bridge logic in `migration_runner.py`: detect non-versioned DB, run legacy migrator once, then version-stamp to head. | Safe adoption for already-deployed DBs. | Controlled legacy->versioned transition path. |
| B4 | 3h | Implement preflight checks in runner: path exists/creatable, writable directory, sqlite open test, busy timeout setup. | Fail early with clear errors. | Explicit preflight failures and messages. |
| B5 | 3h | Add pre-migration DB backup step for existing DBs (`.bak` with timestamp) before first bridge run. | Rollback safety on migration failure. | Backup file creation + restore instructions logged. |
| B6 | 2h | Add process-level migration lock (file lock in `data/db/`), bounded wait and timeout. | Prevent dual startup race conditions. | Single-run guarantee for startup migration. |
| B7 | 1h | Wire runner in `main.py` and remove direct startup use of legacy migration entrypoint. | Enforce single runtime path. | `main.py` uses only `ensure_schema_ready`. |

## Phase C - Verification + Safety Nets

| ID | Max Time | What (Where) | Why | Output |
|---|---:|---|---|---|
| C1 | 3h | Move and harden schema verification into reusable function (`database/schema_contract.py` or `database/migration_runner.py`). | Prevent drift and silent partial upgrades. | Verification callable used by runner and tests. |
| C2 | 2h | Add index verification (not only table/column checks). | Runtime correctness and query performance expectations. | Index presence checks and errors. |
| C3 | 3h | Add targeted repair path only for explicitly allowed legacy anomalies (e.g., missing `utc_offset`), no broad silent mutation. | Correct known legacy defects while avoiding unsafe generic repair. | Guarded repair function + structured logs. |
| C4 | 2h | Add post-migration health logging summary: path taken, from-version, to-version, verification result. | Operability and debugging speed. | Startup logs usable as migration audit trail. |

## Phase D - Tests (Unit + Integration + Failure Injection)

| ID | Max Time | What (Where) | Why | Output |
|---|---:|---|---|---|
| D1 | 3h | Add `tests/unit/test_migration_runner_paths.py` for fresh DB, versioned DB, non-versioned legacy DB. | Validate decision branches deterministically. | Unit tests passing for all path branches. |
| D2 | 4h | Add `tests/unit/test_migration_runner_failures.py` for read-only dir, lock timeout, invalid DB file, backup failure. | Ensure explicit failure semantics. | Failure-path tests with actionable assertions. |
| D3 | 2h | Update existing integration tests to call new runner entrypoint (replace startup init imports where needed). | Keep integration suite aligned with new architecture. | Updated tests compile and pass. |
| D4 | 4h | Add legacy DB fixture matrix: synthetic old schemas (missing `utc_offset`, missing `migration_history`, ghost table presence). | Protect against historical regression modes. | Matrix tests for known bad states. |
| D5 | 3h | Add startup concurrency integration test: two concurrent runner calls against same DB. | Verify lock behavior and no corruption. | Race test proving single migrator execution. |
| D6 | 2h | Add smoke test: start app bootstrap with temp DB and ensure scheduler query paths run without column errors. | Real startup compatibility check. | End-to-end startup smoke test. |

## Phase E - Developer/Operator Workflow

| ID | Max Time | What (Where) | Why | Output |
|---|---:|---|---|---|
| E1 | 2h | Add migration command wrappers (e.g., `scripts/db_migrate.sh`, `scripts/db_current.sh`). | Reduce operator error and standardize usage. | Reusable scripts with strict exit codes. |
| E2 | 3h | Update `DEVELOPMENT.md` with migration workflow, failure handling, and local test commands. | Prevent drift back to ad-hoc migration behavior. | Updated dev runbook. |
| E3 | 3h | Add operations runbook `docs/db-migration-runbook.md`: rollout, validation queries, rollback from backup. | First-try production confidence. | Operator-ready playbook. |
| E4 | 2h | Add explicit "forbidden patterns" section in docs: no runtime DDL in repositories/services. | Preserve architecture boundary long-term. | Guardrail documentation for contributors. |

## Phase F - Rollout + Cleanup

| ID | Max Time | What (Where) | Why | Output |
|---|---:|---|---|---|
| F1 | 4h | Dry-run on copy of real DB: run migration runner, validate schema contract, run smoke tests. | Production rehearsal with realistic data. | Dry-run report with logs and validation output. |
| F2 | 2h | Production rollout checklist execution with pre/post checks. | Controlled deployment without surprises. | Deployment evidence (before/after checks). |
| F3 | 2h | One-release stabilization monitoring (logs + alerts review) and issue triage template. | Catch latent migration edge cases quickly. | Stabilization checklist + incident template. |
| F4 | 2h | Remove runtime legacy bridge after stabilization window, keep one offline recovery utility only. | Finalize architecture and reduce complexity debt. | Legacy startup path removed; offline fallback documented. |

---

## Recommended Execution Order

1. A1 -> A2 -> A3 -> A4 -> A5  
2. B1 -> B2 -> B3 -> B4 -> B5 -> B6 -> B7  
3. C1 -> C2 -> C3 -> C4  
4. D1 -> D2 -> D3 -> D4 -> D5 -> D6  
5. E1 -> E2 -> E3 -> E4  
6. F1 -> F2 -> F3 -> F4

---

## Corner Cases and Exception Handling Matrix

| Scenario | Detection | Required Behavior | Implement In | Test Coverage |
|---|---|---|---|---|
| Fresh DB file missing | DB path not found | Create DB + run migrations to head + verify | `migration_runner.py` | D1 |
| Existing DB without version table | `alembic_version` missing | Run controlled legacy bridge once, then version-stamp | `migration_runner.py` | D1, D4 |
| Existing DB missing `utc_offset` | Contract check fails | Apply targeted repair, re-verify, fail if still wrong | `migration_runner.py` + contract module | D4 |
| Legacy ghost table exists (`task_recipients_unified`) | sqlite schema scan | Ignore safely unless conflicting; log warning | verifier + runner logs | D4 |
| DB directory read-only | preflight write check | Fail startup with actionable error | preflight in runner | D2 |
| DB locked by another process | sqlite lock timeout / file lock contention | Wait bounded time, then fail clearly | lock + retry logic | D2, D5 |
| Corrupted sqlite file | sqlite open/pragma failure | Abort startup, keep backup untouched | preflight + migration execution | D2 |
| Migration fails mid-run | exception from migration engine | Abort, preserve backup, no silent retry loops | runner transaction boundary | D2, F1 |
| Two containers start simultaneously | lock acquisition race | Exactly one migrates, second waits/continues safely | lock logic | D5 |
| Version table exists but schema drifted | contract verification mismatch | Fail startup and force manual fix path | verifier | D1, D4 |
| Backup creation fails | IO error before migration | Abort migration (do not proceed) | runner backup step | D2 |
| Wrong DB path via env | preflight path check | Fail fast with precise env var guidance | preflight | D2 |

---

## Non-Negotiable Guardrails

1. No silent fallback to alternate schema paths.
2. No DDL in repository/service code.
3. No migration run without preflight and post-verify.
4. No bridge-path success without version-stamp.
5. No production rollout without successful F1 dry-run evidence.

---

## Done Definition

The plan is complete when:

1. Tasks A1 through F3 are complete and verified.
2. All migration and failure-path tests pass.
3. Production rollout has pre/post evidence and no startup schema errors.
4. Stabilization window passes; then F4 removes runtime legacy bridge.
