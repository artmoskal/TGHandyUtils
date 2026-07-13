# Migration guide — `engine-v0.9.2` → `engine-v0.10.0`

For consumers moving their pin from `engine-v0.9.2` to `engine-v0.10.0`.
**Target tag: `engine-v0.10.0`** (tools `0.4.0`, viewer `0.2.2`) — **pending: the tag is not cut yet;
do not re-pin until it exists.** Read this before re-pinning;
each item is a real contract change verified against the tag, not a style note. Everything else is
additive. Rebuild wheels from the immutable tag in a clean checkout, record the source commit +
wheel hashes, then run your adapter/canary tests before deploying.

This release makes three runtime contracts that MageQA had as blocking acceptance gates true for
every consumer: truthful `partial` planner tasks, an engine-owned and *enforced* execution window,
and typed retrace provenance delivered to the retraced capability.

## Breaking changes

### 1. A timed-out capability is `partial`, not `failed`
A capability that exceeds its execution window now returns
`CapabilityResult(status="partial")` with `timeout_reason="execution_window_exceeded"` and the
resolved window in `metadata["execution_window"]` — it is **no longer a `failed` result**. The work
was bounded and stopped; already-incurred usage stays counted.
- **If you branch on capability/task status, add a `partial` arm.** A handler that only knew
  `accepted`/`failed` will now see `partial` for timeouts (and for any capability that legitimately
  returns partial output). Treating an unknown status as success is the exact false-green this
  release removes.
- Fan-out counts `accepted` / `partial` / `failed` separately; a partial child makes the parent
  **partial** (not failed, not silently accepted). The `decision="fanout"` trace metadata carries
  `succeeded` / `partial` / `failed` counts.

### 2. Planned `partial` results stay terminal `partial`
A `PlanTask` whose capability returns `partial` now stays `PlanTask.status="partial"` with its
error, output reference, artifact references, and result metadata **preserved** — it is no longer
rewritten to `done` with the error cleared. A plan whose only non-accepted outcome is partial
reports the workflow **partial**, never a green success.
- **If you read plan task statuses, handle `"partial"`.** `PlanTaskStatus` gained the member.

### 3. `RuntimeLimits.timeout_s` is now ENFORCED
Previously declared but inert, `timeout_s` now bounds workflow and capability execution through the
engine-owned execution window. A run that quietly relied on `timeout_s` being ignored will now
actually stop at the deadline (as a partial). Leave it `None` for an unbounded run (unchanged
behavior). The window intersects the task request, the capability limit, the remaining run budget,
and any parent invocation window; the tightest wins and every bound is named.

### 4. Interruptibility is declared, and `external` no longer implies process-backed
`CapabilitySpec.timeout_enforcement` (`process` | `cooperative` | `none`) states what the engine can
actually interrupt.
- An **uninterruptible inline sync** capability (`none`) that is handed a *declared finite* window
  (a task or capability timeout) is now **rejected before the handler runs** — Python cannot kill a
  synchronous thread, so the engine refuses to pretend it can. Put work that needs a hard bound
  behind an async or a process-backed capability, or don't declare a finite task/capability timeout
  for it (a run-budget-only bound stays boundary-only and does not reject).
- **`kind="external"`/`"agent"` no longer defaults to `process` enforcement.** A database/write
  adapter is external but owns no killable subprocess. Only a capability that *declares*
  `timeout_enforcement="process"` (or the real `ExternalProcessCapability` / `CliAgentCapability`,
  which do) is treated as process-backed. Async handlers default to `cooperative`, sync to `none`.

### 5. `CliAgentRequest.timeout_s` no longer defaults to 600s
The hidden ten-minute default is gone; the default is `None`. The CLI subprocess is driven by the
engine's **soft** window; an explicit `timeout_s` may only **narrow** it, never enlarge it. A CLI
run with **no engine window and no explicit timeout fails loudly** rather than silently selecting a
bound nobody declared. `workspace_dir` is now optional (the capability mints a private temp
workspace when omitted). If you constructed `CliAgentRequest` relying on the 600s default, pass an
explicit positive `timeout_s` or invoke it under an engine window.

### 6. Package identities advanced (no reused wheels)
`ai-workflow-engine==0.10.0`, `ai-workflow-tools==0.4.0`, `ai-workflow-viewer==0.2.2`. The tools
wheel advanced from `0.3.0` because its code changed (CLI request/assembly/console) — two different
wheels must never share `name+version`. Repin all three and rebuild.

## Additive (no action required)
- Typed `RetraceProvenance` (`round` / `evaluator_node` / `source_node` / `target_node` /
  `criticism`) is delivered to the retraced capability's context (`context.retrace_provenance`) and
  recorded in trace; retry/replan rounds never impersonate a retrace.
- The generic viewer projects the effective soft/hard window, clamps, enforcement, timeout reason,
  and retrace round → target at the relevant node — from persisted trace truth, saying
  `not recorded` where an older bundle lacks the record rather than guessing.
- `TaskExecutionRequest` on `PlanTask.execution` lets a planner declare a per-task
  timeout / completion reserve; the engine resolves it into the window the capability inherits.

## Verify after re-pinning
- Rebuild all three wheels from the immutable tag in a clean checkout; record commit + wheel hashes.
- Run your adapter/canary tests. In particular, confirm any status handler now accepts `partial`,
  and that any workflow relying on `RuntimeLimits.timeout_s` being inert still behaves acceptably now
  that it is enforced.
