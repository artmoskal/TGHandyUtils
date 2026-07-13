# Voice Brain Engine Integration Guide

Status: **the engine supports the required execution boundary as of the immutable
`engine-v0.10.1` release; voice
integration remains product-owned and must pin tag `engine-v0.10.1` before running its own
latency/cancellation canary.** This replaces
the historical June request/reply transcript.

Read first: [getting started](getting-started.md), [framework concepts](concepts.md), and
[operations](operations.md).

## Product Outcome

A voice brain may use the same engine for bounded model/tool workflows while streaming the final
answer to TTS, handling barge-in, and keeping session conversation state outside workflow control.

Recommended shape:

```text
transcript + session context
  -> intent/route
  -> optional read-only tools or subworkflow
  -> final response client streams text deltas to session TTS
  -> full LLMResponse returns to engine for result/usage/trace
```

## Package Choice

- `ai-workflow-engine==0.10.1` for the runtime and custom `LLMCallable`.
- `ai-workflow-tools==0.4.1` only when using `CliAgentCapability`/console tools. CLI subscription
  workers are usually unsuitable for a low-latency conversational hot path.
- `ai-workflow-viewer==0.2.3` in diagnostics, not the real-time audio path.

## Streaming Boundary

The engine's LLM protocol returns a final `LLMResponse`; streaming happens inside the injected
client:

1. Client receives `LLMRequest` with per-run metadata.
2. Client starts provider streaming.
3. Content deltas go to the product sentence splitter/TTS sink.
4. Tool-call deltas are accumulated but not spoken.
5. Client returns the complete text/tool calls and usage as one `LLMResponse`.

`AgentRunRequest.metadata` flows into `LLMRequest.metadata`, so a shared configured client can route
tokens by session ID or per-call sink without storing one global callback.

Do not add a privileged streaming node or separate voice executor. The provider client owns
transport streaming; engine result, usage, budgets, and observation remain consistent with non-stream
clients.

## Cancellation And Barge-In

- Keep the engine run on one asyncio event loop.
- Barge-in cancels the task/future running the current workflow.
- A different thread marshals with `asyncio.run_coroutine_threadsafe` and cancels the returned future.
- Provider clients and tools must propagate `CancelledError` and release resources in `finally`.
- A finite profile rejects synchronous inline handlers because Python cannot stop them. Use async
  cooperative clients for the voice hot path and process-backed capabilities for hard-bounded tools.
- Read-only/idempotent tools are preferred. Cancellation cannot roll back a partial external effect;
  such effects require product idempotency and explicit policy.

Request a first-class cancellation/teardown mechanic only with a reproducer showing normal task
cancellation cannot meet the need.

## Session Memory

Cross-call conversation history is product state, not workflow control. The voice product owns:

- session key and participant identity;
- bounded turn/token window;
- transcript persistence and privacy/retention;
- interruption state and audio delivery state.

The engine may receive a rendered context through payload/prompt memory, but that memory cannot
choose transitions or resume a suspended machine. Use engine `MemoryStore` only when the product can
map its storage semantics cleanly; do not force all voice sessions into workflow memory.

## One Provider Door

Construct streaming/provider clients in one voice composition module and inject them. Per-run sink
routing belongs in request metadata; provider objects, credentials, callbacks, and audio buffers do
not enter workflow state or observation details.

## Observation And Privacy

Full capture includes prompts/responses/tool payloads and may contain transcripts. Configure bundle
storage access and retention as product policy. Audio bytes remain product artifacts, not state or
trace. Correlate calls with `correlation_id` only when product privacy policy allows it.

## Voice-Specific Misuse Risks

- Do not speak tool-call arguments or partial JSON.
- Do not share a mutable global TTS sink between concurrent runs.
- Do not use CLI-agent latency on the conversational hot path without measured acceptance.
- Do not swallow cancellation and continue a provider/tool call after barge-in.
- Do not treat conversation history as durable machine state.
- Do not place audio/base64 in engine checkpoints or memory.

## Adopter Contract AC

1. Pinned engine wheel and provider client version are recorded.
2. Two concurrent calls route deltas to the correct session sinks with no cross-talk.
3. Tool-decision turns produce no spoken content.
4. Final text, usage, trace, and spoken text correspond to the same provider response.
5. Barge-in cancels provider streaming and suppresses late TTS output.
6. Read-only tool cancellation releases resources; side-effect tools prove idempotency.
7. Session history is bounded and product-owned.
8. Observation retention/privacy is explicit and audio bytes stay outside state.
9. One real latency canary meets the product's first-token and total-response budgets.

Use [the framework request lifecycle](extension-lifecycle.md) for a proven missing universal mechanic;
keep audio/session/TTS concerns in the voice product otherwise.
