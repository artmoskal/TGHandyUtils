# Anki Workflow Acceptance Criteria

Status: validated locally
Last validated: 2026-06-08

This file consolidates acceptance criteria scattered across:

- `docs/anki-cards-plan.md`
- `docs/workflow-engine-architecture.md`
- `docs/anki-implementation-plan.md`
- `docs/anki-manual-qa.md`

## Validation Commands

No GPT Image 2 spend was used for this validation pass.

Latest results:

- Compile validation: passed.
- Dependency/docs validation: passed (`environment.yml` pins resolve together, `pip check` clean,
  framework imports clean, setup docs aligned).
- Full free unit suite: 543 passed, 142 deselected, 72.0% coverage; retained artifacts were scanned
  for live provider API URLs and no external API calls were recorded.
- Config/engine/Anki graph focused suite: 112 passed.
- Focused language-voice unit slice: 41 passed, 6 deselected.
- Focused processor UX/audio integration slice: 6 passed.
- Workflow engine/usage formatting unit slice: 17 passed.
- Dependency-upgrade focused unit suite: 118 passed.
- Previous API-backed graph integration slice remains covered separately; it was not rerun for this
  delivery-layer-only change.

```bash
python -m compileall packages/ai_workflow_engine/ai_workflow_engine services/content/anki_generation_graph.py services/content/anki_card_set_planner.py services/content/anki_scenario_planners.py services/content/anki_quality_evaluator.py models/anki_workflow.py
./test.sh unit -- --cov-fail-under=0
./test.sh unit -- tests/unit/test_anki_generation_graph.py tests/unit/test_anki_quality_evaluator.py tests/integration/test_anki_processor_flow.py --tb=short --cov-fail-under=0
./test.sh unit -- tests/unit/test_anki_generation_graph.py tests/unit/test_anki_quality_evaluator.py tests/unit/test_anki_scenario_planners.py tests/unit/test_anki_card_set_planner.py tests/unit/test_anki_card_service.py tests/unit/test_image_generation_provider.py tests/unit/test_workflow_engine.py tests/unit/test_anki_directives_buffer.py tests/unit/test_content_processors.py --tb=short --cov-fail-under=0 --log-cli-level=WARNING
ALLOW_PAID_TESTS=1 ./test.sh integration -- tests/integration/test_anki_processor_flow.py tests/integration/test_anki_workflow_graph_integration.py::test_anki_workflow_small_real_graph_integration tests/integration/test_anki_workflow_graph_integration.py::test_anki_workflow_real_quality_evaluator_graph_integration --tb=short --cov-fail-under=0 --log-cli-level=WARNING
```

## Criteria Matrix

| ID | Criterion | Evidence | Status |
| --- | --- | --- | --- |
| AC-01 | `/anki` and saved `anki` mode route directly to Anki; auto mode is the only classifier/supervisor choice path. | `services/content/intent_resolver.py`, `services/content/router.py`, `docs/anki-cards-plan.md`. | Covered by existing routing design; not reworked in this change. |
| AC-02 | Source content, OCR/description text, uploaded image bytes, and Telegram metadata are normalized before graph planning. | `services/content/anki_source.py`, `tests/unit/test_anki_generation_graph.py::test_content_source_preserves_image_analysis_and_bytes`. | Validated. |
| AC-03 | `[i ...]` supports shortcodes-only brackets, optional closing `[i]`, bare `[i]` guidance, legacy prefix use, and image-source guidance. | `services/content/anki_directives.py`, `tests/unit/test_anki_directives_buffer.py`. | Validated. |
| AC-04 | The reusable workflow engine is separate from Anki-specific schemas/prompts and is extraction-ready as an internal package boundary. | `packages/ai_workflow_engine`, Anki models under `models/anki_workflow.py`, non-Anki toy workflow and prompt-root tests. | Validated. |
| AC-05 | Structured LLM stages use Pydantic output, model profiles, one repair retry, and structured logs. | `packages/ai_workflow_engine/ai_workflow_engine/engine/llm_node.py`, `AnkiCardSetPlanner`, scenario planners, quality evaluator, supervisor tests. | Validated. |
| AC-06 | A supervisor/instrument layer exists for future master-agent style orchestration without replacing command/settings routing. Anki graph nodes are registered `CapabilitySpec`s and invoked through `CapabilityRuntime` with a compiled `RuntimePlan` and side-effect classes for image/voice/package nodes. | `packages/ai_workflow_engine/ai_workflow_engine/engine/supervisor.py`, `services/content/anki_generation_graph.py`, `tests/unit/test_workflow_engine.py`, `tests/unit/test_anki_generation_graph.py::test_graph_nodes_run_through_generic_capability_runtime`. | Validated. |
| AC-07 | The card-set gate chooses one family per input: basic, cloze, or visual. Forced directives constrain but do not replace planning. | `services/content/anki_card_set_planner.py`, graph normalization tests. | Validated. |
| AC-08 | AI-planned card count is conservative: max 3 unless the user explicitly requests more; visual defaults to one generated image/card; one-card list/set decisions must preserve full planned coverage instead of sampling one item. | `tests/unit/test_anki_generation_graph.py::test_graph_clamps_ai_planned_count_without_user_override`, trace coverage preview test, prompt coverage tests, visual tests. | Validated. |
| AC-09 | Guidance from `[i ...]` reaches card-set and per-type scenario planning instead of leaking into source facts. | Directive-cleaned source and visual-guidance tests. | Validated. |
| AC-10 | Per-type scenario planners exist for text, cloze, and visual cards with branch-specific prompts and schemas. | `services/content/anki_scenario_planners.py`, `tests/unit/test_anki_scenario_planners.py`. | Validated. |
| AC-11 | Cloze planning prevents meaningless clozes through explicit target selection and validation. | `ClozeScenarioPlanner`, cloze-target tests, `AnkiCardService` cloze repair tests. | Validated. |
| AC-12 | Branch-specific renderers exist for text, cloze, visual, and fallback. Visual rendering is deterministic from planned exact question/answer text and does not re-run generic card extraction. | `services/content/anki_renderers.py`, graph renderer tests. | Validated. |
| AC-13 | Uploaded images can be reused, ignored, or used as generation references depending on directives/planning. | Image asset plan tests, uploaded-reference tests. | Validated. |
| AC-14 | Generated image cards use a provider-neutral image request. OpenAI `gpt-image-2` remains the default; Gemini/Nano Banana is switchable through config; comparison mode can run both providers but is bounded by the image-call budget. Explicit `[i gen]` is enabled, while automatic AI-chosen generation is disabled by default. | `config.py`, `packages/ai_workflow_engine/ai_workflow_engine/image_generation.py`, image-provider tests, model scans. | Validated without paid image rerun. |
| AC-15 | Character and design reference image paths plus style version are configurable and passed to image-generation requests. | `config.py`, graph style-reference test. | Validated. |
| AC-16 | Quality evaluation can accept, retry the card-set plan, repair render, retry scenario, or fall back. Visual quality rejects term-only/decorative images that do not encode the written answer relationship, including abbreviation-only visuals that omit the supported meaning/effect. | `services/content/anki_quality_evaluator.py`, graph quality tests. | Validated. |
| AC-17 | Retry loops and provider calls are bounded to avoid runaway cost. | `ANKI_MAX_IMAGE_GENERATIONS_PER_RUN`, `ANKI_MAX_QUALITY_REPAIRS_PER_RUN`, `WORKFLOW_MAX_TEXT_CALLS_PER_RUN`, `WORKFLOW_MAX_IMAGE_CALLS_PER_RUN`, `WORKFLOW_MAX_VOICE_CALLS_PER_RUN`, usage/budget tests. | Validated. |
| AC-18 | LLM prompts are prompt-cache-aware and provider-neutral: stable instructions/schema/style first, dynamic source/user/tool state second. Cached input tokens are included in usage summaries and cost estimates when provider metadata reports them. | `prompts/`, `packages/ai_workflow_engine/ai_workflow_engine/prompt_loader.py`, `StructuredLLMNode`, Anki planners/evaluator/renderer, usage tests. | Validated. |
| AC-19 | Failed/abandoned generated media is cleaned before fallback/retrace. | `WorkflowArtifact`, `cleanup_artifacts`, graph cleanup path, workflow artifact cleanup test. | Validated for local files. |
| AC-20 | `.apkg` packaging and Anki buffer delivery path still work through the processor boundary. | `tests/integration/test_anki_processor_flow.py`, `AnkiCardService` package tests. | Validated. |
| AC-21 | Text/cloze/API-backed graph integration works with current mini text model and image generation disabled. | Focused graph integration tests. | Validated. |
| AC-22 | Manual QA has a checklist for import, media, visual quality, fallback, and cost guard review. | `docs/anki-manual-qa.md`. | Documented. |
| AC-23 | Telegram result messages expose image placement, generated-image preview, fallback use, and media count on deck export. | `services/content/anki_processor.py`, `handlers_modular/callbacks/anki_buffer_cb.py`, processor UX tests. | Validated. |
| AC-24 | Explicit `[i langvoice source->pt gen]` requests create one visual language card with target-language answer text and generated pronunciation audio attached to the Anki back side. | `services/content/anki_directives.py`, `services/content/anki_generation_graph.py`, `packages/ai_workflow_engine/ai_workflow_engine/voice_generation.py`, language voice graph and processor delivery tests. | Validated with mocked providers; paid live provider run deferred. |

## Explicitly Deferred Or Rollout-Only

These are not hidden implementation blockers:

- Live Telegram e2e with a controlled real bot/chat environment.
- Broad golden-set card-quality benchmark.
- Final product style reference image selection beyond the current PPLA character/design v1 refs.
- Multiple `[i ...]` blocks in one message.
- Product-level graph restart/recovery wiring for generated artifact workflows.
