# Voice Brain Integration — hand-off to the ai_workflow_engine author

> Same shape as `gopro-handoff.md` / `mageqa-handoff.md`.
> Consumer/owner: **aws_deploy `twilio-ai`** voice pipeline (artemm).
> Status: design agreed 2026-06-15. First milestone = voice → engine →
> `claude -p` capability → spoken summary.
> **Purpose of this doc: a punch-list of engine asks for the author, each with a
> "how hard?" prompt so we can scope before committing.**

## Context

`twilio-ai` is a real-time voice pipeline (phone/web: STT → brain → TTS, with
barge-in). We're adopting **`ai_workflow_engine` as the universal brain**:
transcript → agent loop → spoken answer. Capabilities are pluggable per **voice
profile**. We consume the engine as a **dependency, not a fork**; capabilities
and all voice/transport code stay on our side. Eval verdict: excellent fit
(transport-agnostic, `CapabilityRegistry`, `gpt-5.4-mini` default, bounded +
cost-honest).

## What already works (no ask — just confirming our understanding)
- **`CliAgentCapability` + the `claude_p` flavor already ship** (`ai_workflow_tools/cli_agents`).
  So our v1 "coding-session" capability is basically `register(CliAgentCapability(claude_p))`
  — near-free. (There's a `codex_exec` flavor too.) ✅
- **`AsyncQueueTraceSink` already streams trace/step events.** That covers our
  "speak periodic progress" need (`running tool X…`) without any change. ✅
- Transport-agnostic core, no Telegram in the engine package. ✅

So the punch-list below is small.

## Ask 1 — stream the FINAL answer tokens  (HIGH; the one real blocker for voice)
Trace streaming (above) gives us progress, but the **final assistant answer**
comes back only as a complete `AgentRunResult` (the `action="finish"` path).
For voice that adds latency: we pipe LLM tokens into a sentence-splitter → TTS so
the agent starts *speaking the first sentence* within a few hundred ms (our
`taras` profile is tuned for this). Non-streaming = wait for the whole answer
before any audio.

- For **tool-using turns** this barely matters (tool execution dominates) — so
  our first milestone is unaffected and we can ship v1 without this.
- For **plain conversational turns** it's a real regression vs our `astream`
  path, and it's what blocks us routing *all* profiles through the engine.

**Suggested approach (you'll know better):** the underlying client is langchain
`ChatOpenAI`, which already supports `astream`. The gap is only that the engine's
finish path calls the LLM in request/response mode and returns a complete
`LLMResponse`. Options, cheapest first:
1. An `on_token`/async-iterator callback threaded only through the **finish**
   LLM call (tool-call steps stay request/response).
2. A `stream=True` mode on `AgentCapability`/`build_llm_agent_capability` that
   yields final-answer chunks.
3. Allow the `LLMCallable` to emit tokens via a side-channel callback while still
   returning the final `LLMResponse` (smallest contract change — see open Q).

**→ How hard is this, roughly? Which of the three fits the engine's design?**
This is the single most useful answer for us to plan around.

## Ask 2 — built-in session memory / context window  (MED; discuss after v1)
Engine is stateless per call (`AgentRunRequest.prompt` is a string). For
multi-turn voice we'll prepend prior turns ourselves for v1. A built-in
**bounded, session-keyed context window** (turns/token budget) would be cleaner
and reusable across consumers (voice/Telegram/HTTP). Not blocking.
**→ Is this in scope for the engine, or should it stay product-owned? Rough effort if engine-side?**

## Ask 3 — cancellation / barge-in  (MED; voice-specific)
Voice users interrupt mid-answer. The brain must **abort an in-flight agent run**
when the user barges in — cleanly (cancel the current LLM/tool call, no dangling
side effects). Does the agent loop support cooperative cancellation today (e.g.
honoring `asyncio.CancelledError` between steps, or a cancel token), and is
tool-teardown safe on abort?
**→ Already supported? If not, rough effort to add a cancel path?**

## Ask 4 — distribution / pip-installability  (MED; needed to consume it)
Our services are docker images built from `requirements.txt`. To depend on the
engine we need it installable: an index, a stable
`pip install "git+ssh://…#subdirectory=packages/ai_workflow_engine"`, or an
agreed vendoring path. (Today: `pip install -e packages/ai_workflow_engine`.)
**→ What's the intended distribution? We'll wire the container build to it.**

## Open question
Does `LLMCallable` already permit a bring-your-own client that streams internally
(side-channel `on_token`) while still returning the final `LLMResponse`? If yes,
that's likely the cheapest path to Ask 1 with **zero** change to the agent
contract — confirm and we'll build to it.

## What we are NOT asking
- No transport/voice/STT/TTS knowledge in the engine (it stays transport-agnostic).
- No capability code from you (capabilities are ours, via `CapabilityRegistry`).
- No fork — we depend on the package and add capabilities around it.

## Why unify (context for Ask 1)
End-state: every voice profile is an engine workflow — a plain chat profile is
just the **zero-tool** case; richer profiles add capabilities (RAG we ran before
and turned off; coding-session; etc.). One path, additive complexity. Only Ask 1
(streaming) blocks unifying the plain profiles; until then tool-profiles run on
the engine and plain profiles stay on our `astream` path.

---

## Reply from the engine author (claude, 2026-06-15)

Verified each ask against the code (citations are `ai_workflow_engine/...`). TL;DR: your
punch-list is even smaller than you scoped — **Ask 1 needs no agent-contract change**, Ask 3 is
already supported, Ask 4 is "pin a git tag." Details:

### Open Q + Ask 1 (streaming final tokens) — **YES, and it's near-free. This is the headline.**
`LLMCallable` is exactly `async __call__(LLMRequest) -> LLMResponse` (`llm_protocol.py:99-104`),
and the agent planner just does `response = await self.llm(llm_request)`
(`engine/agent_planner.py:73`). The engine does not care *how* your client produces the response.
So: **implement a streaming `LLMCallable` that wraps `ChatOpenAI.astream`, pipes content deltas
into your sentence-splitter→TTS, accumulates the full text, and returns the final `LLMResponse`.**
Zero change to the agent contract.

It even handles the tool-vs-answer distinction for free: on a tool-deciding turn the OpenAI stream
emits *tool_call* deltas (no content), so your client speaks nothing and returns
`LLMResponse(tool_calls=[...])`; on an answer turn it emits *content* deltas you stream to TTS and
return `LLMResponse(text=...)`. The planner routes on `response.tool_calls` either way
(`agent_planner.py:88-95`). And a plain (zero-tool) profile is a single LLM call returning text —
perfect for streaming. **You can ship plain-profile streaming today without me.**

The *one* thing worth an engine change is **per-run sink routing for concurrency.** Your client is
baked at `build_llm_agent_capability(llm=...)` construction, so one capability instance + N
concurrent callers would cross TTS streams unless each call carries its own sink. `AgentRunRequest`
already has a `.metadata` dict (`models.py:204`), but the planner builds the `LLMRequest.metadata`
fresh and does **not** merge `request.metadata` into it (`agent_planner.py:65-72`). Merging it is a
**~2-line additive change** — then your client reads `request.metadata["on_token"]` (or a session
id) per call and routes to the right caller's TTS. That's your option 3, and it passes our
protocol-fattening fence (named consumer + additive + single-turn behavior identical). Your options
1 and 2 (on_token through the finish call / `stream=True` on `AgentCapability`) are **not needed** —
don't build them.

**SHIPPED — `engine-v0.4.1` (commit 93f2aa8).** `AgentRunRequest.metadata` now flows into
`LLMRequest.metadata` (engine observability keys win on collision; empty metadata = byte-identical
behaviour). Pin v0.4.1, put your TTS sink / session id in `AgentRunRequest.metadata`, read it as
`request.metadata["on_token"]` inside your streaming `LLMCallable`. Per-profile streaming needs
nothing at all. **Pushback: do NOT build options 1 or 2** (on_token through the finish call /
`stream=True` on `AgentCapability`) — they re-implement, as a privileged path, what the plain
`LLMCallable` contract already does; option 2 fattens the agent contract with no behaviour the BYO
client can't already deliver. The protocol-level path is correct and now wired.

### Ask 3 (cancellation / barge-in) — **already supported at the asyncio level; no change needed for read-only tools.**
The agent loop is a plain `async for` over `await` points — `planner.next_step(...)` (the LLM call)
and `await self.tool_runtime.invoke(...)` (`engine/agent.py` loop + `:124`). Cancel the asyncio task
running the agent run and `CancelledError` is raised at the next await and unwinds cleanly between
*and* within steps — the in-flight LLM/`astream` request aborts (httpx cancels), local loop state
(`history`) is just GC'd. So barge-in = `task.cancel()` on the run coroutine. **Caveat (be
honest):** the engine has no 2-phase commit — if you cancel mid-*tool*, that tool's own
`try/finally` runs but partial external side effects are the tool's responsibility. For voice
(RAG/lookup/coding-session tools are read-only or idempotent) this is a non-issue; just keep
side-effecting tools idempotent. A first-class cancel-token + teardown-hook API is possible (~1 day)
but I'd **try plain task cancellation first** — it almost certainly covers you, and I'm not building
a cancel-token API speculatively (it adds no behaviour `task.cancel()` lacks for read-only/idempotent
tools). Bring a case where asyncio cancellation provably isn't enough and I'll revisit. (Note the threading
contract: the executor binds one event loop; if your brain runs on a different thread, marshal via
`asyncio.run_coroutine_threadsafe(...)` and `.cancel()` the returned future. Single-loop pipelines
just cancel the task.)

### Ask 2 (session memory / context window) — **product-owned for v1; engine-side only at the 2-consumer bar.**
The engine is deliberately stateless per call (`AgentRunRequest.prompt` is a string; `history` is
per-episode *tool* steps, not cross-turn conversation). Prepend prior turns yourself for v1 — that's
the right call and matches how Telegram does it. A bounded, session-keyed context window (turns +
token budget) is a reasonable **L1 helper** later, *not* core-loop state — but per our graduation
rule it earns a place in the shared library only once a 2nd consumer wants the same shape (voice +
Telegram + HTTP would qualify). Rough effort if/when engine-side: ~0.5–1 day for a
`SessionMemory(session_id, max_turns/max_tokens)` that assembles the prompt. **Pushback: keep this
product-owned — I'm not adding it to the engine now.** Cross-turn conversation is product state, not
orchestration; baking a session store into a stateless-by-design loop is the scope creep the
layering exists to prevent. It graduates to a shared L1 helper ONLY when a 2nd consumer wants the
identical shape (voice + Telegram + HTTP). Want it shared from day one? That's a deliberate
architectural decision — raise it explicitly and we'll scope it then.

### Ask 4 (distribution / pip) — **git-tag + subdirectory pip install. Two packages, not one.**
It's already pip-installable; there's no PyPI/private index (none planned unless demand justifies).
Consume it the way GoPro does — pin an **immutable tag** (`engine-v0.6.0`), build a wheel, and never
track the live branch (see the GoPro/MageQA handoff for the full adoption recipe). **Important:** since v0.4.0,
`CliAgentCapability` + the `claude_p` flavor live in **`ai_workflow_tools`**, not the engine
(`ai_workflow_tools/cli_agents/`). `ai-workflow-tools` depends on `ai-workflow-engine`, so your
`requirements.txt` needs **both**, pinned to the same tag:

```
ai-workflow-engine @ git+ssh://git@<host>/<repo>@engine-v0.4.1#subdirectory=packages/ai_workflow_engine
ai-workflow-tools  @ git+ssh://git@<host>/<repo>@engine-v0.4.1#subdirectory=packages/ai_workflow_tools
```

(or build wheels from the tag via a detached worktree and vendor them, like GoPro's
`tools/build_engine_wheel.sh`). Current tags: `engine-v0.4.1` is latest (the Ask-1 passthrough lands here). Effort on my side: zero —
just pick your pin. If an `ssh`-less HTTPS path or a wheel-publish step would help your CI, say so.

### Net
Ship v1 now (`register(CliAgentCapability(claude_p))` + a streaming `LLMCallable` for plain
profiles); nothing blocks you. The one engine change worth making — the
`AgentRunRequest.metadata → LLMRequest.metadata` passthrough for concurrent per-run TTS routing —
**is shipped in `engine-v0.4.1`**. Asks 2/3/4 need no engine change, and I'm declining to build
session-memory or a cancel-token API speculatively — bring a 2nd consumer or a concrete failing
case. Nothing blocks your v1.
