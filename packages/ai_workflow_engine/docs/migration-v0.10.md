# Migration guide — `engine-v0.9.2` → `engine-v0.10.0`

For consumers moving their pin from `engine-v0.9.2` to `engine-v0.10.0`.
**Target tag: `engine-v0.10.0`** (tools `0.4.0`, viewer `0.2.2`). Read this before re-pinning;
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
Previously declared but inert, `timeout_s` now bounds workflow and capability work through the
engine-owned execution window. A run that quietly relied on `timeout_s` being ignored now reaches a
typed timeout result. A cooperatively stopped capability is `partial`; code that suppresses
cancellation is `failed` with `timeout_reason="cancellation_containment_failed"`. Leave the limit
`None` for an unbounded run (unchanged behavior). The window intersects the task request, capability
limit, remaining run budget, and parent invocation window; the tightest wins and every bound is
named.

The graph-level fail-safe uses the remaining run budget as work time and records a separate bounded
cancellation allowance. If arbitrary in-process graph code suppresses cancellation, the engine
returns a loud containment failure but Python cannot forcibly kill that code. Put untrusted or
side-effectful hard-bounded work behind a process capability.

### 4. Interruptibility is declared, and `external` no longer implies process-backed
`CapabilitySpec.timeout_enforcement` (`process` | `cooperative` | `none`) states what the engine can
actually interrupt.
- An **uninterruptible inline sync** capability (`none`) under *any finite window* (task,
  capability, run, or parent) is now **rejected before the handler runs** — Python cannot kill a
  synchronous thread, so the engine refuses to pretend it can. Make bounded in-process work async
  (`cooperative`), put hard-bounded work behind a process-backed capability, or leave the entire
  invocation unbounded. There is no run-limit exception that silently weakens enforcement.
- **`kind="external"`/`"agent"` no longer defaults to `process` enforcement.** A database/write
  adapter is external but owns no killable subprocess. Only a capability that *declares*
  `timeout_enforcement="process"` (or the real `ExternalProcessCapability` / `CliAgentCapability`,
  which do) is treated as process-backed. Async handlers default to `cooperative`, sync to `none`.

Process-backed doors receive the same engine soft/hard window. Their trace metadata separates work
time, terminate grace, and reap/result-settlement reserve, all inside the hard deadline. The viewer
projects these values; it does not infer them from elapsed time. The shared process owner bounds
stdin delivery as part of work and, on POSIX, owns a new process group so timeout/cancellation stops
spawned descendants as well as the direct CLI PID. `ConsoleChatModel` uses this same owner; do not
reintroduce a raw `subprocess.run` side door.

### 5. `CliAgentRequest.timeout_s` no longer defaults to 600s
The hidden ten-minute default is gone; the default is `None`. The CLI subprocess is driven by the
engine's **soft** window; an explicit `timeout_s` may only **narrow** it, never enlarge it. A CLI
run with **no engine window and no explicit timeout fails loudly** rather than silently selecting a
bound nobody declared. `workspace_dir` is now optional (the capability mints a private temp
workspace when omitted). If you constructed `CliAgentRequest` relying on the 600s default, pass an
explicit positive `timeout_s` or invoke it under an engine window.

### 6. A published capability spec is the complete registration contract
If a handler exposes `handler.spec` (for example `ExternalProcessCapability` or
`CliAgentCapability`), registration arguments that would otherwise be ignored now raise. Configure
the handler/spec itself, or register a plain handler using keyword arguments; never provide both.
This prevents a stronger side-effect ledger, schema, metering flag, or timeout from disappearing at
registration.

### 7. Package identities advanced (no reused wheels)
`ai-workflow-engine==0.10.0`, `ai-workflow-tools==0.4.0`, `ai-workflow-viewer==0.2.2`. The tools
wheel advanced from `0.3.0` because its code changed (CLI request/assembly/console) — two different
wheels must never share `name+version`. Repin all three and rebuild.

## Additive (no action required)
- Typed `RetraceProvenance` (`round` / `evaluator_node` / `source_node` / `target_node` /
  `criticism`) is delivered to the retraced capability's context (`context.retrace_provenance`) and
  recorded in trace; retry/replan rounds never impersonate a retrace.
- The generic viewer projects the effective soft/hard window, clamps, enforcement, timeout reason,
  process work/cleanup/settlement, and retrace round → target at the relevant node — from persisted
  trace truth, saying `not recorded` where an older bundle lacks the record rather than guessing.
- `TaskExecutionRequest` on `PlanTask.execution` lets a planner declare a per-task
  timeout / completion reserve; the engine resolves it into the window the capability inherits.

## Verify after re-pinning
- Rebuild all three wheels from the immutable tag in a clean checkout; record commit + wheel hashes.
- Run your adapter/canary tests. In particular, confirm any status handler now accepts `partial`,
  and that any workflow relying on `RuntimeLimits.timeout_s` being inert still behaves acceptably now
  that it is enforced.
