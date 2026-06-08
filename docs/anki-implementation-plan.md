# Anki Workflow Implementation Plan

Status: active implementation plan
Owner: Artem
Last updated: 2026-06-04

Acceptance criteria and validation evidence: `docs/anki-acceptance-criteria.md`

## Target Outcome

The bot should turn arbitrary Telegram content into one professional Anki card family per input:

- basic Q/A;
- cloze;
- visual using uploaded or generated media.
- language visual cards with generated target-language pronunciation audio for explicit
  `[i langvoice ...]` requests.

If the user supplies `[i ...]`, it adds constraints and guidance. If not, the system decides the
best card family, image policy, count, wording strategy, fallback, and quality repairs.

Default policy:

- one card family per input;
- visual normally one card and one generated image;
- basic/cloze usually one card, rarely two or three when separate high-value facts justify it;
- card count is based on independent study objectives, not source line/bullet count. A coherent
  sibling list/set should usually become one grouped recall card that covers the whole set, unless
  the answer would be unreadable, in which case use 2-3 logical groups;
- no mixed basic/cloze/visual sets in the first production workflow;
- OpenAI `gpt-image-2` as the default generated-image backend, with Gemini/Nano Banana selectable
  through the same image-provider seam;
- ElevenLabs voice generation as an optional tool node for language cards, capped to one call per
  run by default;
- different model profiles per node, with current mini as the default quality/cost baseline;
- current text-model policy checked on 2026-06-04:
  - `gpt-5.4-mini` for decision gates, API-backed graph integration tests, scenario planning, card
    rendering, and quality evaluation by default;
  - `gpt-5.4-nano` only as an explicit later optimization after project evals prove quality holds;
  - `gpt-5.5` only for complex supervisor/orchestration decisions where quality justifies cost;
  - do not use `gpt-4o-mini` in new Anki/workflow code.

## Phase 0 - Contract Baseline

### Task 0.1 - Directive Grammar

Estimate: 1.5h
Status: done

Deliverables:

- Parse `[i <shortcodes>]instruction text[i]`.
- Treat closing `[i]` as optional when guidance runs to the end.
- Preserve `[i cloze] text source` for text-only prefix usage.
- Treat after-tag text as guidance when source exists before the tag or when attached image media is
  the source.
- Add tests for closed block, optional-to-end block, image-source instruction block, and legacy
  prefix content.

Files:

- `services/content/anki_directives.py`
- `tests/unit/test_anki_directives_buffer.py`
- `tests/unit/test_anki_generation_graph.py`

### Task 0.2 - Acceptance Scenarios

Estimate: 2h
Status: done

Deliverables:

- Add executable acceptance cases for:
  - text without `[i]`: API-backed graph integration;
  - text with `[i cloze]`: API-backed graph integration;
  - text with `[i visual] make image funny[i]`: API-backed graph integration fallback when generation is
    disabled;
  - uploaded image with `[i visual] make this memorable`: unit graph coverage;
  - image generation failure fallback: unit graph coverage;
  - bad JSON repair: unit card-set planner coverage.
- Mark which cases are unit, graph integration, processor-boundary integration, or manual OpenAI
  image checks. Live OpenAI image graph checks now exist in
  `tests/integration/test_anki_workflow_graph_integration.py`; true live Telegram e2e is still
  separate.

Files:

- `docs/anki-cards-plan.md`
- `tests/unit/test_anki_generation_graph.py`
- `tests/unit/test_anki_card_set_planner.py`
- `tests/integration/test_anki_workflow_graph_integration.py`

## Phase 1 - Internal Workflow Engine Boundary

### Task 1.1 - Package Shape

Estimate: 2h
Status: done

Deliverables:

- Move shared workflow classes into `packages/ai_workflow_engine`.
- Do not keep legacy module shims for the old direct `services/workflow/*` engine paths.
- Keep Anki models out of the engine package.

Files:

- `packages/ai_workflow_engine/pyproject.toml`
- `packages/ai_workflow_engine/ai_workflow_engine/models.py`
- `packages/ai_workflow_engine/ai_workflow_engine/engine/*`
- `packages/ai_workflow_engine/ai_workflow_engine/usage.py`
- `packages/ai_workflow_engine/ai_workflow_engine/prompt_loader.py`

### Task 1.2 - Model Profiles

Estimate: 2.5h
Status: done via direct per-node config

Deliverables:

- Add direct per-node config profiles now; add `ModelProfile` / `ModelProfileRegistry` only when
  more workflows need shared policy logic.
- Support profiles:
  - `decision_gate`;
  - `scenario_planner`;
  - `card_renderer`;
  - `quality_evaluator`;
  - `complex_supervisor`;
  - `image_generator`.
- Wire config names and defaults:
  - `decision_gate`: `gpt-5.4-mini`;
  - `scenario_planner`: `gpt-5.4-mini`;
  - `card_renderer`: `gpt-5.4-mini`;
  - `quality_evaluator`: `gpt-5.4-mini`;
  - `complex_supervisor`: `gpt-5.5`;
  - `image_generator`: OpenAI `gpt-image-2` by default, Gemini/Nano Banana selectable through
    `ANKI_IMAGE_PROVIDER`;
  - `image_prompt_policy`: product-level prompt builder with shared style/base templates and
    provider-specific OpenAI/Gemini/comparison templates;
  - generated image size: `1536x1024`;
  - generated image quality: `low`.
- Inventory stale `gpt-4o-mini` usage and remove active defaults from new Anki/workflow code.
- Tests verify different nodes can request different profiles.

Files:

- `config.py`
- `services/content/anki_card_set_planner.py`
- `services/content/anki_scenario_planners.py`
- `services/anki_card_service.py`
- `core/container.py`
- `tests/unit/test_workflow_engine.py`

### Task 1.3 - LLM Node Runner

Estimate: 3h
Status: done - reusable helper exists and active planning/evaluation nodes use it

Deliverables:

- Add reusable typed LLM node helper:
  - prompt render;
  - model profile selection;
  - Pydantic parse;
  - bounded JSON/schema retry;
  - structured logs;
  - caller-owned graph trace remains outside the LLM helper.
- Keep it product-agnostic.
- Scenario planners and the quality evaluator keep branch-specific prompts/validators, but use the
  shared helper for model selection, Pydantic parsing, bounded retry, and structured logs.

Files:

- `packages/ai_workflow_engine/ai_workflow_engine/engine/llm_node.py`
- `tests/unit/test_workflow_engine.py`

### Task 1.4 - Artifact Ownership

Estimate: 2.5h
Status: done for local side-effect cleanup; product-level restart recovery deferred

Deliverables:

- Add `WorkflowArtifact` model.
- Track generated media/temp references before side effects are exposed to delivery/buffer.
- Provide cleanup hook for failed visual workflows.
- Durable artifact recovery after process restart is deferred until it is a measured need.

Files:

- `packages/ai_workflow_engine/ai_workflow_engine/engine/artifacts.py`
- `services/content/anki_generation_graph.py`
- `tests/unit/test_anki_generation_graph.py`

## Phase 2 - Anki Planning Layer

### Task 2.1 - Rename Role to CardSetPlanner

Estimate: 2h
Status: done

Deliverables:

- Introduce `AnkiCardSetPlanner`.
- The class owns family, image policy, count, guidance, model-profile needs, and fallback.
- Update docs/tests to use target naming.

Files:

- `services/content/anki_card_set_planner.py`
- `tests/unit/test_anki_card_set_planner.py`

### Task 2.2 - Enforce Conservative Count Policy

Estimate: 1.5h
Status: done

Deliverables:

- Clamp AI-planned count to max 3 unless user explicitly requests more.
- Visual branch defaults to one generated image/card.
- Tests for user override vs AI planned count.

Files:

- `services/content/anki_generation_graph.py`
- `tests/unit/test_anki_generation_graph.py`

### Task 2.3 - Guidance Propagation

Estimate: 2h
Status: done

Deliverables:

- Add explicit guidance fields to Anki workflow models.
- Ensure directive guidance reaches card set planner and all scenario planners.
- Tests verify "make image funny" reaches visual scenario/image prompt.

Files:

- `models/anki_workflow.py`
- `services/content/anki_generation_graph.py`
- `tests/unit/test_anki_generation_graph.py`

## Phase 3 - Per-Type Scenario Planners

### Task 3.1 - Text Scenario Planner

Estimate: 3h
Status: done

Deliverables:

- Add structured `TextCardScenario`.
- Prompt prepares front/back intent, facts, answer constraints, tags, and guidance.
- Bad JSON/schema retry through engine LLM node helper.
- Renderer no longer asks old prompt to decide card strategy.

Files:

- `models/anki_workflow.py`
- `services/content/anki_scenario_planners.py`
- `tests/unit/test_anki_generation_graph.py`

### Task 3.2 - Cloze Scenario Planner

Estimate: 3h
Status: done

Deliverables:

- Add structured `ClozeCardScenario` with sentence and meaningful cloze targets.
- Prompt explicitly prevents filler-word clozes and visible-answer leaks.
- Validator checks targets exist and are meaningful enough.

Files:

- `models/anki_workflow.py`
- `services/content/anki_scenario_planners.py`
- `tests/unit/test_anki_generation_graph.py`

### Task 3.3 - Visual Scenario Planner

Estimate: 3h
Status: done

Deliverables:

- Add structured `VisualCardScenario` with visual concept, prompt, layout, media policy, answer
  text, exact question text, and fallback.
- Guidance such as "funny", "minimal", or "focus on X" reaches image prompt.
- One generated image by default.
- Visual prompts must encode the tested answer relationship; term-only posters and decorative
  object images are retry/fallback candidates.

Files:

- `models/anki_workflow.py`
- `services/content/anki_scenario_planners.py`
- `tests/unit/test_anki_generation_graph.py`

## Phase 4 - Renderers

### Task 4.1 - Basic Renderer

Estimate: 2h
Status: done - renderer class split; final JSON still produced by AnkiCardService

Deliverables:

- Convert `TextCardScenario` to `AnkiCard` without strategy decisions.
- Preserve Anki package compatibility.

Files:

- `services/content/anki_renderers.py`
- `tests/unit/test_anki_generation_graph.py`

### Task 4.2 - Cloze Renderer

Estimate: 2h
Status: done - renderer class split; AnkiCardService performs final cloze JSON/repair

Deliverables:

- Convert cloze targets to `{{cN::...}}` deletions.
- Reject cards with no real cloze.

Files:

- `services/content/anki_renderers.py`
- `tests/unit/test_anki_generation_graph.py`

### Task 4.3 - Visual Renderer

Estimate: 2.5h
Status: done

Deliverables:

- Attach uploaded/generated media according to visual scenario layout.
- Build visual cards deterministically from `VisualCardScenario.question_text` and
  `VisualCardScenario.answer_text`; do not call the generic extractor after visual planning.
- Keep generated image count to one by default.
- Bundle media through existing package path.

Files:

- `services/content/anki_renderers.py`
- `services/content/anki_processor.py`
- `tests/unit/test_anki_generation_graph.py`

## Phase 5 - Quality Loop

### Task 5.1 - Rendered Card Evaluator

Estimate: 3h
Status: done

Deliverables:

- Add evaluator output:
  - accept;
  - retry card-set plan;
  - repair renderer;
  - retry scenario;
  - fallback.
- Use quality model profile by default.
- Tests use fake evaluator branches.

Files:

- `services/content/anki_quality_evaluator.py`
- `services/content/anki_generation_graph.py`
- `tests/unit/test_anki_generation_graph.py`

### Task 5.2 - Retrace Edges

Estimate: 3h
Status: done

Deliverables:

- Add bounded graph edges from evaluator to:
  - card-set planner;
  - scenario planner;
  - renderer repair;
  - fallback.
- Track retry counts per reason.
- Pass evaluator criticism into the retried planner/scenario/renderer step.
- Clean abandoned generated image/audio artifacts before retrace or fallback.

Files:

- `services/content/anki_generation_graph.py`
- `tests/unit/test_anki_generation_graph.py`

## Phase 6 - Generated Image Production Hardening

### Task 6.1 - Real API Graph Integration Smoke Test

Estimate: 2h
Status: done via focused graph integration

Deliverables:

- Add opt-in script/test that calls the configured image provider with a tiny prompt when API key
  is present. OpenAI live smoke is validated; Gemini live smoke requires `GEMINI_API_KEY` in
  `.env`.
- Never run in normal unit suite.
- Verify output is packaged into an `.apkg`.

Files:

- `tests/integration/test_anki_workflow_graph_integration.py`

### Task 6.2 - Style Reference Policy

Estimate: 2h
Status: done

Deliverables:

- Choose config behavior for project style reference images:
  - character reference image for the recurring trainer aircraft;
  - design reference image for the general PPLA deck visual language.
- Record style version in generated media.
- Tests verify both reference paths are passed to the image provider.

Files:

- `config.py`
- `packages/ai_workflow_engine/ai_workflow_engine/image_generation.py`
- `tests/unit/test_image_generation_provider.py`

## Phase 7 - Manual Review and Release

### Task 7.1 - Manual Card QA Checklist

Estimate: 1.5h
Status: done

Deliverables:

- Checklist for importing generated `.apkg`, reviewing text/cloze/visual card quality, and
  verifying media.

Files:

- `docs/anki-manual-qa.md`

### Task 7.2 - Rollout Switches

Estimate: 2h
Status: done

Deliverables:

- Ensure generated visuals remain off by default.
- Add config notes for enabling in dev/prod.
- Verify text/cloze path remains stable.

Files:

- `config.py`
- `docs/deployment.md`
- `docs/anki-cards-plan.md`
