# Anki Cards Feature - Current Plan

Status: **active implementation**
Owner: Artem
Branch: `feature/anki-workflow-graph`
Last updated: 2026-06-05

## 1. Goal

The bot turns arbitrary Telegram content into Anki flashcards while keeping reminders/tasks as a
separate content mode.

Supported modes now:

- Persistent settings mode: `reminder` | `anki` | `auto`.
- One-shot command override: `/tasks ...` or `/anki ...`.
- Auto mode: classify the message, show a 5 second correction window, then commit.

Routing rule:

- `/anki` or saved `anki` mode goes directly to the Anki workflow.
- `/tasks` or saved `reminder` mode goes directly to the reminder workflow.
- Only saved `auto` mode uses classifier/supervisor behavior to choose the workflow before the
  5 second correction window.

Current target branch: replace the old single-prompt Anki generation with a typed, multi-step card
planning pipeline that can choose between text, cloze, user-image, generated-visual, and explicit
language-voice visual cards. The control graph, type gate, per-type scenario planners,
image-generation provider, voice-generation provider seam, renderer split, and quality evaluation
now exist; true live Telegram e2e remains open.

## 2. Project Rules

- The key architecture concept is a universal goal-oriented workflow engine: a user goal plus
  available instruments such as typed sub-workflows, LLM planning calls, primitive tools, file/image
  tools, and future web/search tools. Anki is the first application graph, not the engine itself.
- The reusable engine boundary is documented separately in
  `docs/workflow-engine-architecture.md`. Keep Anki-specific card schemas, prompts, rendering, and
  delivery out of the engine package.
- Per the 2026-06-07 scope decision the shared engine is built to full scope first
  (sequencing + per-phase acceptance in `docs/workflow-engine-implementation-plan.md`); this Anki
  feature is the validation workload that must stay green (`./test.sh`) at every framework phase.
- Prompt quality, cache layout, and PPL(A) mental test cases are tracked in
  `docs/anki-prompt-quality-review.md`.
- Acceptance criteria and validation evidence are consolidated in
  `docs/anki-acceptance-criteria.md`.
- The current implementation is a reusable package plus an Anki graph. Open-ended agency and
  streaming/durable lifetime stay second-product adoption risks until MageQA/GoPro bind real
  adapters.
- Prefer the clean target architecture over preserving obsolete shapes or compatibility layers.
- Preserve current user-visible reminder behavior unless a product change is explicitly chosen.
- Framework machinery follows the settled full-engine implementation plan, not ad hoc expansion.
  Add or keep a primitive only when it is covered by `docs/workflow-engine-implementation-plan.md`
  and has a proving toy/Anki/MageQA/GoPro acceptance path.
- Keep content modes explicit: a new mode should be one enum value, one processor, one router entry,
  and any required settings UI.
- Use structured models for all LLM stage outputs. Raw JSON strings should not cross stage
  boundaries.
- Treat `[i ...]` tags as user constraints and guidance, not as the complete card configuration. The workflow
  should fill unspecified parameters such as card type, image policy, count, layout, and fallback
  strategy through planning stages, and later prompts should receive relevant guidance such as
  tone/style requests.
- Default to one output modality per input message. Do not mix basic, cloze, and visual cards from
  one user input in the first production workflow.
- Prefer fewer cards. Unless the user explicitly requests more, visual cards should normally produce
  one card, and text/cloze/basic should normally produce one card with a practical cap of two or
  three only when the source clearly has separate high-value facts.
- Image generation is optional per card and must have a text fallback.
- Every workflow node that can branch, retry, call a model, create a file, or call a tool must emit
  structured logs and append trace events.

## 3. Current Implementation

Implemented baseline:

- `IContentProcessor` and `ProcessingContext` in `core/interfaces.py`.
- `ReminderProcessor` in `services/content/reminder_processor.py`.
- `AnkiProcessor` in `services/content/anki_processor.py`.
- Explicit `Intent` routing in `services/content/router.py`.
- `IntentResolver` in `services/content/intent_resolver.py`.
- Settings mode and deck name persistence.
- `/anki` and `/tasks` command overrides.
- Auto mode 5 second pre-commit window in `services/content/auto_flow.py`.
- `.apkg` packaging through `genanki` in `services/anki_card_service.py`.
- User-provided image embedding via Anki media.
- Per-user in-memory Anki buffer with export, clear, and undo actions.

Current Anki generation is now split at the workflow-control level:

```
assembled thread + directives
  -> ContentSource
  -> AnkiGenerationGraph
       -> DirectiveParser
       -> ImageAssetPlanner
       -> CardSetPlanner
       -> per-type branch
       -> TextScenarioPlanner | ClozeScenarioPlanner | VisualScenarioPlanner
       -> text/cloze render or image-generation + visual render
       -> optional voice generation for explicit langvoice cards
       -> quality evaluation / bounded repair / fallback
  -> AnkiCardService.build_package()
  -> Telegram document + buffer
```

The graph makes routing, image asset planning, AI card-set branching, per-type scenario planning,
validation, fallback, and trace logging explicit. Automatic card type choice is handled by
`AnkiCardSetPlanner`, which returns a validated `CardBuildPlan` and retries once on bad
JSON/schema. Forced `[i basic]`, `[i cloze]`, and `[i visual]` constraints are passed into planning
and then enforced by graph normalization; they do not prevent planning of unspecified parameters
such as count.

Per-type scenario planning is now split:

- `TextScenarioPlanner`: prepares grounded source content, facts to test, answer constraints, and
  rendering guidance for basic Q/A cards.
- `ClozeScenarioPlanner`: chooses meaningful cloze targets, max deletion count, rewritten sentence
  guidance, and renderer constraints.
- `VisualScenarioPlanner`: chooses front/back intent, layout, image count, generated-image prompt,
  reference policy, written answer, and fallback kind.

Every planner uses structured Pydantic output with one repair attempt on bad JSON/schema. Planner
nodes receive directive-cleaned source content plus structured directives and plans, so `[i ...]`
guidance is not treated as source fact text.

Renderer classes are now branch-specific. Text and cloze rendering still delegate final JSON
materialization and cloze repair to `AnkiCardService.extract_cards()` so packaging behavior stays
centralized.

This now works for simple Q/A, cloze, uploaded-image, generated-image, and explicit language-voice
graph paths. The current implementation includes rendered-card quality routing, bounded
repair/fallback, generated image/audio artifact cleanup, and per-run cost caps. Product rollout
still needs controlled live Telegram e2e and broader card-quality evaluation. The current PPL(A)
split character/design reference selection is documented in `docs/anki-ppla-visual-identity.md` and
configured in `config/anki.profile.yaml`.

The current shared layer now includes product-agnostic workflow goals, execution IDs/logging,
capability runtime, profiles/runtime plans, trace/usage/budget, evaluator retry/retrace, bounded
agent/process wrappers, fan-out, scheduler, checkpoints, human clarification, and fake-backed
MageQA/GoPro-shaped pilots. Telegram command and settings precedence still route directly before
auto-mode classification, as intended.

## 4. Target Workflow Architecture

The next architecture should be a reusable goal-workflow engine, with Anki as the first serious use
case.

This does not mean one generic prompt or one generic state shape for every future request. It means:

- a top-level orchestrator can hold the user goal and choose instruments;
- instruments can be typed product sub-workflows, LLM calls, deterministic helpers, or external
  tools;
- each product flow owns its typed graph and typed state;
- graph execution, branching, bounded loops, retry policy, tracing, and side-effect handling follow
  shared conventions;
- future workflows can add tools such as web search, image generation, file parsing, or human
  approval without turning processors into long prompt strings.

The required workflow capabilities:

- decision branching: route by model/user/tool decision;
- bounded loops: repair bad JSON, improve a scenario, regenerate an image prompt, or fall back;
- partial retrace: return to a previous stage when a later stage proves the earlier decision was
  bad;
- tool nodes: image generation now, web search or other tools later;
- side-effect isolation: image/file creation must be tracked so retry/fallback can clean up or avoid
  duplicate effects;
- traceability: keep node outputs, validation failures, chosen branches, retry counts, and generated
  artifact paths.

### 4.1 Universal Goal Engine

Target concept:

```text
UserGoal
  -> Supervisor/Orchestrator
       -> chooses instruments
       -> calls typed sub-workflows, LLM planners, deterministic helpers, or external tools
       -> evaluates results against the goal
       -> loops/retraces/falls back within bounded policy
  -> final product-specific delivery
```

Core objects should stay product-agnostic:

- `WorkflowGoal`: normalized user objective, constraints, and delivery target;
- `WorkflowInstrument`: a callable capability such as `anki_generation`, `image_generation`,
  `web_search`, `file_parse`, `calendar_task_create`, or a future review tool;
- `WorkflowInstrumentRegistry`: the product-agnostic inventory of callable instruments available
  to a future supervisor;
- `WorkflowRunner`: executes one chosen graph/tool with trace IDs and logging;
- `WorkflowTraceEvent`: records decisions, attempts, validation failures, and artifacts.

The supervisor should not directly mutate low-level Anki cards or files. It should select and
coordinate instruments. Product graphs such as Anki still own their schemas, prompts, validators,
and side effects.

Current status: the reusable package includes `WorkflowGoal`, `WorkflowRunner`, trace/usage/artifact
models, `CapabilityRuntime`, `RuntimePlanCompiler`, `WorkflowSupervisor`, `WorkflowDecisionPlanner`,
evaluator/retrace helpers, bounded agent/process wrappers, fan-out, scheduler, checkpoints, and
human clarification. The first product graph is Anki. The supervisor/runtime infrastructure is
available, while Telegram command/settings routing still uses explicit product precedence before
auto mode.

### 4.2 Universal Workflow Processor

Build a small local processor layer over the chosen graph framework.

Responsibilities:

- execute a typed product graph;
- attach generic correlation IDs (`workflow_id`, `user_id`, `workflow_type`) plus product-owned
  metadata such as Telegram message/chat IDs;
- record node start/end, latency, branch decisions, retry counts, validation errors, and artifacts;
- expose cleanup hooks for generated files and other side effects;
- provide one Telegram-safe failure result without leaking prompt or model internals;
- keep product-specific state and prompts inside the product graph.

This is the reusable part. The Anki graph should be the first implementation, but future request
types should be able to reuse the same runner without inheriting Anki-specific fields.

Useful shared state shape:

```python
class WorkflowTraceEvent(BaseModel):
    node: str
    decision: str | None = None
    error: str | None = None
    artifacts: list[str] = []

class AnkiPipelineState(BaseModel):
    workflow_goal: WorkflowGoal | None = None
    source: ContentSource
    directives: AnkiDirectiveConstraints | None = None
    image_asset_plan: ImageAssetPlan | None = None
    build_plan: CardBuildPlan | None = None
    text_scenario: TextCardScenario | None = None
    cloze_scenario: ClozeCardScenario | None = None
    visual_scenario: VisualCardScenario | None = None
    rendered: RenderedCardSet | None = None
    generated_media: list[GeneratedMedia] = []
    retry_counts: dict[str, int] = {}
    trace: list[WorkflowTraceEvent] = []
```

### 4.3 Anki Workflow Shape

The Anki processor should run a typed workflow graph:

```
ContentSource
  -> DirectiveParser
  -> ImageAssetPlanner
  -> CardSetPlanner
  -> branch by card kind
       -> TextScenarioPlanner   -> TextRenderer
       -> ClozeScenarioPlanner  -> ClozeRenderer
       -> VisualScenarioPlanner -> ImageGenerator -> VisualRenderer
  -> CardValidator
  -> AnkiPackageBuilder
  -> Delivery + buffer
```

Key branch/retrace examples:

- image asset planner decides uploaded image is only useful as OCR/source text, so type planner
  chooses text/cloze instead of direct image media;
- image asset planner decides uploaded image is the study object, so visual branch reuses it
  directly as Anki media;
- type planner chooses `visual`, image generation fails or validator rejects the result, graph
  routes to `fallback_to_text`;
- cloze scenario validator finds meaningless cloze targets, graph routes back to
  `prepare_cloze_scenario` with validation feedback;
- future search-enabled cards can branch to a `web_search` node before scenario preparation.

Simplification for the first production workflow:

- one input message produces one card family: basic, cloze, or visual;
- do not produce a mixed set such as one visual plus two cloze cards from the same input;
- visual branch normally produces exactly one card to avoid excessive generation cost;
- cloze/basic can produce two or three cards only when the planner can justify separate high-value
  facts or the user explicitly asks;
- if a future use case needs mixed card families, add it behind a separate `mixed_card_set` branch
  after the single-family workflow is stable.

### 4.4 ContentSource

Raw Telegram inputs normalized once before any card planning. This is not an AI stage; it is just
the graph input object.

- user text;
- OCR text already produced by the image ingestion path;
- image description/summary already produced by the image ingestion path;
- user-provided image bytes, Telegram file IDs, and filenames;
- owner/user metadata needed by prompts;
- parsed command/directive constraints.

Current code already does image understanding before content routing:

- `process_user_input_with_photo()` downloads the Telegram image;
- `ImageProcessingService.process_image_message()` calls `OpenAIService.analyze_image()`;
- the current prompt extracts visible text and a short image summary;
- the thread stores enriched content markers like `[SCREENSHOT TEXT]` and
  `[SCREENSHOT DESCRIPTION]`, plus raw `image_data`.

`ContentSource` should formalize that current implicit shape. It should not normally call OpenAI
again. If image analysis failed or is missing, a future graph node may choose to call a richer
`describe_image` tool, but that is a separate node, not hidden inside source normalization.

Image-only messages must be supported:

- `/anki` with an attached image should route directly to Anki.
- Saved `anki` mode with an attached image should route directly to Anki.
- The graph should preserve the original user image as card media.
- The graph should use OCR/description to decide whether the best card is text, cloze, user-image
  reuse, or generated visual.
- If the user image itself is the study object, the planner can choose `reuse_user_image` instead of
  generating a new image.

This is the only stage that knows Telegram-specific thread tuple shapes.

### 4.5 DirectiveParser

`[i ...]` should become structured constraints for planning:

- grammar: `[i <shortcodes>]instruction text[i]`, where the closing `[i]` is optional when the
  instruction text runs to the end of the message;
- the bracket body contains shortcodes only, not long prose;
- the text after the opening tag is user guidance/instructions when the tag follows source content,
  when a closing `[i]` marker is present, or when the source is an uploaded image;
- preserve legacy prefix usage for text-only content: `[i cloze] Paris is in France` still treats
  `Paris is in France` as source content, not guidance;
- requested card type: `auto` | `basic` | `cloze` | `visual`;
- image policy: `auto` | `none` | `reuse_user_image` | `generate`;
- placement preference: `front` | `back` | `split`;
- count strategy: `split` | `merge` | exact count;
- user guide/instruction;
- style/reference request.

The directive parser should not decide the final card. It only reports user constraints.

Examples:

```text
Bernoulli principle [i visual gen] make the image funny[i]
```

Source: `Bernoulli principle`; constraints: visual + generated image; guidance:
`make the image funny`.

```text
[attached image] [i visual] make the visual card funny
```

Source: uploaded image and existing OCR/description; guidance runs to the end of the message.

```text
[i cloze] Paris is in France
```

Text-only legacy prefix form. Source: `Paris is in France`; constraint: cloze.

### 4.6 ImageAssetPlanner

Purpose: decide how uploaded user images should participate before the card-set gate makes the
final card decision.

User-provided images are source assets. They do not automatically become card images.

Possible decisions:

- `ignore_media`: image is irrelevant or too weak; use text/OCR only.
- `source_content_only`: image contributes OCR/description/source facts, but the card should not
  embed the uploaded image or pass it as a generated-image reference.
- `reuse_user_image`: the original image itself should appear on the card.
- `use_as_reference`: the user image should guide generated art through image edit/reference input.
- `generate_new_visual`: the uploaded image is not suitable as-is, but a generated visual may help.

Inputs:

- user image bytes and filenames;
- OCR text and image summary;
- `[i ...]` image constraints;
- card-quality policy.

Output fields can be part of `CardBuildPlan` or a separate `ImageAssetPlan`:

```python
class ImageAssetPlan(BaseModel):
    image_role: Literal[
        "ignore_media",
        "source_content_only",
        "reuse_user_image",
        "use_as_reference",
        "generate_new_visual",
    ]
    candidate_front_images: list[str] = []
    candidate_back_images: list[str] = []
    reference_images: list[str] = []
    rationale: str
```

This gate handles the two image cases:

- user uploads images and the flow decides they should go directly into cards;
- user uploads images and the flow decides to transform their meaning into text/cloze/generated
  visual cards instead.

### 4.7 CardSetPlanner

Purpose: decide the single card family, image policy, conservative count, model profile needs, and
fallback plan for this input.

Inputs:

- `ContentSource`;
- parsed directives;
- `ImageAssetPlan`;
- user guidance/preferences from `[i ...]`;
- cost/quality policy.

Output: `CardBuildPlan`, a Pydantic model.

If the user explicitly forced a type (`[i basic]`, `[i cloze]`, `[i visual]`), respect it unless it
is impossible. If not specified, this node auto-decides the best card type:

- prefer a visual card when a catchy image is feasible and likely improves recall;
- choose cloze when the material is best remembered as a sentence/context with hidden key facts;
- choose basic text Q/A when the fact is exact/abstract and image generation would likely add noise
  or hallucinated meaning. Do not reject a visual merely because the source is an abbreviation or
  definition if its operational meaning, effect, contrast, or consequence can be pictured clearly.
- choose target card count when the user did not request an exact count. Prefer fewer cards by
  default: visual normally one card; cloze/basic usually one card; two or three only for clearly
  separate high-value facts. Do not plan more than three cards unless the user explicitly requested
  more.

The planner should not choose mixed card families from one input in the first production workflow.
It chooses one of:

- `basic`;
- `cloze`;
- `visual_basic`.

Useful fields:

```python
class CardBuildPlan(BaseModel):
    card_kind: Literal["basic", "cloze", "visual_basic"]
    image_policy: Literal["none", "reuse_user_image", "reference", "generate"]
    count: int
    user_guidance: str | None
    model_profiles: dict[str, str] = {}
    source_facts: list[str]
    study_goal: str
    visual_rationale: str | None
    fallback_kind: Literal["basic", "cloze"]
    user_constraints_applied: list[str]
```

Planning rule: prefer a visual card only when the visual can make recall easier or more memorable.
Do not generate images for abstract definitions, legal wording, acronyms, or facts where a
generated picture would likely hallucinate meaning. Acronym/definition cards may still be visual
when the operational meaning, effect, contrast, or consequence can be pictured clearly.

Model-profile rule: each node can request a profile, but the default production text profile is the
current mini model. Nano is an explicit later optimization, not the baseline, because our early
real-world checks should optimize for card quality and stable behavior before shaving marginal cost.

Model profile defaults, checked against official OpenAI docs on 2026-06-04:

- Do not use `gpt-4o-mini` in new Anki/workflow code or graph integration smoke tests. It is legacy for this
  architecture.
- `decision_gate`: use `gpt-5.4-mini` by default. The pricing page lists `gpt-5.4-nano` as cheaper,
  but gates decide the whole downstream path, so start with latest mini and only downgrade after
  project evals prove the routing quality holds.
- `scenario_planner` and `card_renderer`: use `gpt-5.4-mini` by default.
- `quality_evaluator`: use `gpt-5.4-mini` by default; consider `gpt-5.4-nano` only for explicit
  high-volume preflight optimization after evals.
- `complex_supervisor`: reserve `gpt-5.5` for difficult orchestration or ambiguous high-value
  decisions, not routine card routing.
- Official sources:
  - <https://developers.openai.com/api/docs/models>
  - <https://developers.openai.com/api/docs/pricing>

Prompt caching and provider portability:

- All Anki LLM prompts must be written as a stable static prefix plus a dynamic request tail.
- Static prefix: role, branch purpose, quality rules, examples, visual identity/style policy, and
  Pydantic/JSON schema.
- Dynamic tail: source text, OCR/image analysis, uploaded-image summaries, `[i ...]` guidance,
  card build plan, scenario, rendered card JSON, media summaries, and retry-specific feedback.
- Never place `format_instructions` or schema text after source material. It should be in the
  static prefix so OpenAI prompt caching and Claude-style caching can reuse it.
- Keep this architecture LLM-agnostic. Use LangChain message ordering and product-neutral prompt
  templates in graph nodes; provider-specific cache hints belong in the model/provider adapter, not
  in Anki planners/renderers.
- When provider usage reports cached input, the workflow usage meter must estimate cost with cached
  input rates and show a per-node live reply grid with operation, input tokens, cached input tokens,
  output tokens, and estimated cost.

### 4.8 Per-Type Scenario Planners

Purpose: prepare card content after the card-set decision, with separate prompts and schemas per
card family.

A single generic scenario prompt is too broad. It would hide text-card, cloze-card, and visual-card
rules inside one prompt and recreate the same long-prompt problem the pipeline is supposed to fix.

Use one scenario planner per branch:

- `TextScenarioPlanner`: prepares front/back intent, exact facts to test, answer constraints, and
  self-contained wording rules.
- `ClozeScenarioPlanner`: prepares the sentence shape and explicit meaningful cloze targets. This
  is where "do not create meaningless clozes" belongs.
- `VisualScenarioPlanner`: prepares the visual concept, image prompt, reference-image policy,
  front/back/both-side layout, and text fallback. First production version should normally use one
  generated image only.

Each scenario planner receives relevant user guidance from `[i ...]`. For example, "make image
funny" should be available to the visual scenario planner and image prompt, while "make it formal"
should influence text/cloze wording. Guidance is a preference unless the directive parser marks it
as a hard constraint.

Outputs should be separate Pydantic models when fields diverge enough:

Useful fields:

```python
class TextCardScenario(BaseModel):
    front_intent: str
    back_intent: str
    facts_to_test: list[str]
    answer_text: str
    tags: list[str] = []

class ClozeCardScenario(BaseModel):
    source_sentence: str
    cloze_targets: list[str] = []
    rewritten_sentence: str
    tags: list[str] = []

class VisualCardScenario(BaseModel):
    source_content: str
    question_text: str
    front_intent: str
    back_intent: str
    facts_to_test: list[str]
    visual_prompt: str
    visual_style_notes: str | None = None
    reference_image_policy: Literal["none", "style_reference", "source_reference"] = "none"
    layout: Literal[
        "image_front_text_back",
        "text_front_image_back",
        "image_both_sides",
        "image_front_image_back",
    ]
    image_count: Literal[1, 2]
    answer_text: str
    tags: list[str] = []
```

Initial count policy:

- visual: one card and one generated image by default;
- cloze: one card by default, two or three only for clearly separate contextual facts or explicit
  user request;
- basic: one card by default, two or three only when separate facts would make a broad card worse;
- mixed card families are out of scope for the first production workflow.

For cloze cards, this stage is responsible for preventing meaningless clozes. The renderer should
not need to rediscover which words matter.

For visual cards, this branch writes the image-generation brief: what should be pictured, what must
not be shown, what text should be avoided, and how the generated image should support the written
answer. The scenario owns the exact `question_text` and `answer_text`; the visual renderer must not
ask a generic card extractor to rediscover the final Q/A.

Visual cards must not be "term posters". A generated image that only repeats the question term,
shows a happy generic object, or decorates the card without encoding the answer relationship is a
failed visual scenario. For definitions, rules, procedures, and relationships, the image should
show the effect, consequence, contrast, grouping, decision cue, or story beat that makes the written
answer easier to recall.

Default visual layout policy:

- Prefer `text_front_image_back` or `image_both_sides` when the image supports recognition or
  explanation.
- Allow `image_front_text_back` for cases where the image itself is the cue to identify, label, or
  explain.
- Allow `image_front_image_back` only when two distinct images genuinely improve the card; do not
  create two generated images by default.

### 4.9 CardRenderer

The renderer converts a validated scenario into concrete `AnkiCard` objects and media files.

- `TextRenderer`: creates basic Q/A cards.
- `ClozeRenderer`: creates cloze cards from explicit cloze targets.
- `VisualRenderer`: consumes generated or user-provided image media, then creates front/back card
  HTML from `VisualCardScenario.question_text` and `VisualCardScenario.answer_text`.

The renderer branch should be deterministic except for the optional image-generation call. Visual
rendering is not an LLM extraction step; if the visual scenario does not provide an exact question
and answer, the graph should retry the visual scenario or fall back.

### 4.10 ImageGenerator

Add this behind a small provider interface, not inside `AnkiCardService`.

```python
class ImageGenerator(Protocol):
    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage: ...
```

The default implementation is `OpenAIImageGenerator`; the provider seam also supports a Gemini
adapter and a bounded comparison wrapper. The Anki graph must still depend only on the
provider-neutral `ImageGenerator` protocol.

Use GPT Image 2, not DALL-E. OpenAI's April 21, 2026 product release names this capability
"ChatGPT Images 2.0":

- <https://openai.com/index/introducing-chatgpt-images-2-0/>

The API model page names `gpt-image-2` as the state-of-the-art image generation model. It also lists
the dated snapshot `gpt-image-2-2026-04-21`, which matches the Images 2.0 release date. Make the
model configurable so we can use the current alias by default while pinning when reproducibility
matters:

- `ANKI_IMAGE_PROVIDER=openai` by default;
- `ANKI_IMAGE_MODEL=gpt-image-2` by default for OpenAI;
- `ANKI_GEMINI_IMAGE_MODEL=gemini-3.1-flash-image` by default when
  `ANKI_IMAGE_PROVIDER=gemini`;
- `ANKI_GEMINI_IMAGE_MODEL=gemini-2.5-flash-image` is the cheaper Gemini/Nano Banana option for
  comparison runs;
- `ANKI_GEMINI_RESPONSE_FORMAT_ENABLED=false` by default; Gemini still receives prompt and inline
  reference images, but aspect/size response-format controls are opt-in because live REST validation
  can reject documented values during rollout;
- `ANKI_IMAGE_PROVIDER=comparison` with `ANKI_IMAGE_COMPARE_PROVIDERS=openai,gemini` runs multiple
  paid providers for one request and returns only the configured primary provider image to Anki;
- `ANKI_IMAGE_STABLE_MODEL=gpt-image-2-2026-04-21` as the stable fallback/pin;
- `ANKI_IMAGE_SIZE=1536x1024` by default for generated Anki visuals;
- `ANKI_IMAGE_QUALITY=low` by default as the cheapest GPT Image 2 production setting that still
  exercises the v2 model;
- `ANKI_IMAGE_OUTPUT_FORMAT=png` by default.

Do not automatically fall back to `gpt-image-1.5` in the first implementation. If GPT Image 2 is
unavailable or fails, the graph should fall back to a text/cloze card unless the user explicitly
chooses a different image model.

Gemini/Nano Banana note: official Gemini image-generation docs show the same `generateContent`
shape supports prompt text plus inline reference images. Keep provider-specific payload details
inside the Gemini adapter; do not fork visual planning prompts unless a measured provider-specific
quality issue requires it. The default Gemini adapter asks for text+image response modalities and
does not force response-format sizing unless `ANKI_GEMINI_RESPONSE_FORMAT_ENABLED=true`.

Cost/quality rule:

- `gpt-image-2` supports `low`, `medium`, `high`, and `auto` quality.
- The guide lists popular sizes including `1024x1024`, `1024x1536`, `1536x1024`, larger 2K/4K
  sizes, and `auto`.
- Current pricing lists GPT Image 2 low quality at about `$0.006` for `1024x1024` and about
  `$0.005` for `1024x1536` or `1536x1024`. For Anki, use `1536x1024` low by default because it is
  a cheap v2 setting and landscape cards work well in review UIs.
- Reusable style/reference assets are different from disposable per-card outputs: generate deck
  references with `quality=high` and version them separately, because their one-time cost is
  amortized and a weak reference degrades every downstream generated card.
- `gpt-image-1-mini` is cheaper in some table rows, but it is not the requested v2/best-quality
  target. Do not use it for production Anki visuals unless the user explicitly asks for a cheaper
  non-v2 mode.
- The graph has hard cost guards:
  - `ANKI_MAX_IMAGE_GENERATIONS_PER_RUN=1` by default;
  - `ANKI_MAX_QUALITY_REPAIRS_PER_RUN=1` by default;
  - `ANKI_EVALUATE_FALLBACK_CARDS=false` by default, so fallback text cards do not trigger another
    LLM quality pass after deterministic validation;
  - `WORKFLOW_USAGE_TRACKING_ENABLED=true` by default records local usage events for text, image,
    and tool calls;
  - `WORKFLOW_MAX_TEXT_CALLS_PER_RUN=16` and `WORKFLOW_MAX_IMAGE_CALLS_PER_RUN=1` cap per-run paid
    call counts before another call starts;
  - `WORKFLOW_MAX_ESTIMATED_USD_PER_RUN=0` disables USD budget enforcement by default; set a
    positive value once model prices are configured/validated;
  - the default local price table covers current Anki defaults including `gpt-5.4-mini`,
    `gpt-5.4-mini-2026-03-17`, `gpt-image-2`, `gemini-2.5-flash-image`,
    `gemini-3.1-flash-image`, and `gemini-3-pro-image`; subscription CLI notional pricing uses the
    engine's strict versioned `pricing:` catalog or an injected `NotionalPricingPolicy`, never an
    environment-JSON override;
  - `WORKFLOW_SHOW_USAGE_IN_REPLY=true` adds a compact usage footer to generated-card replies;
  - image generation failure or budget exhaustion routes to text fallback, not another image try.

Current OpenAI docs support generation and edits with image references, and the Responses API can
also expose image generation as a tool in multi-step flows. Current Gemini image-generation docs
show prompt text plus inline reference images through `generateContent`.

- <https://developers.openai.com/api/docs/models/gpt-image-2>
- <https://developers.openai.com/api/docs/guides/image-generation>
- <https://platform.openai.com/docs/guides/tools-image-generation>
- <https://ai.google.dev/gemini-api/docs/image-generation>

Use stable project style reference images if visual consistency matters. The PPLA deck uses two
runtime references: one character reference for the recurring trainer aircraft and one general deck
design reference for palette, props, layout rhythm, and visual vocabulary. Store a style version in
the generated media metadata so later style changes do not silently alter old deck behavior.

API choice:

- Use `client.images.generate()` for OpenAI one-shot card art when no reference image is needed.
- Use `client.images.edit()` for OpenAI when style references or source reference images should
  guide the output. OpenAI's image guide shows `gpt-image-2` edit calls with multiple input images,
  which is enough for character + design + optional user-source reference support.
- Use Gemini `generateContent` with inline image parts for Gemini/Nano Banana references.
- Consider the Responses API image-generation tool only if the scenario planner and image tool need
  to operate in one conversation or multi-turn edit loop.
- Keep image prompts out of graph helpers. `AnkiImagePromptPolicy` owns the shared deck style/base
  prompt plus provider-specific OpenAI/Gemini/comparison templates, while the provider adapters only
  translate `ImageGenerationRequest` into API calls.

Default policy for first implementation:

- at most one generated image per user message;
- support multiple project style reference images from the start;
- support user/source image references when the visual scenario requests them;
- no generated front/back pair until single-image quality is proven;
- no mask/occlusion generation in the first generated-image stage;
- fall back to text/cloze when generation fails, times out, or the plan says visual value is low.

Rollout switches:

- `ANKI_IMAGE_GENERATION_ENABLED=true` by default so explicit `[i gen]` requests can use the
  configured image provider.
- `ANKI_AUTO_IMAGE_GENERATION_ENABLED=false` by default. This prevents the AI card-type planner from
  silently spending on generated images when the user did not explicitly request generation.
- `ANKI_IMAGE_PROVIDER=openai`, `ANKI_OPENAI_IMAGE_MODEL=gpt-image-2`,
  `ANKI_GEMINI_IMAGE_MODEL=gemini-3.1-flash-image`, `ANKI_IMAGE_SIZE=1536x1024`, and
  `ANKI_IMAGE_QUALITY=low` are the default generated-visual settings.
- `ANKI_IMAGE_PROVIDER=comparison` plus `ANKI_IMAGE_COMPARE_PROVIDERS=openai,gemini` is for
  explicit A/B testing only and requires a higher image-call budget.
- `ANKI_STYLE_CHARACTER_REFERENCE_IMAGE`, `ANKI_STYLE_DESIGN_REFERENCE_IMAGE`, and
  `ANKI_STYLE_REFERENCE_VERSION` should be set together when using a stable visual style. The
  current PPLA deck references are `assets/anki/ppla-character-reference-v3.png` and
  `assets/anki/ppla-design-reference-v2.png` with version `ppla-split-v3`.
- `ANKI_QUALITY_EVALUATION_ENABLED=true` by default for rendered-card quality routing.
- `ANKI_MAX_IMAGE_GENERATIONS_PER_RUN=1` and `ANKI_MAX_QUALITY_REPAIRS_PER_RUN=1` are production
  safety caps; raise them only for explicit experiments.

### 4.11 Validation, Retry, and Fallback

Each LLM stage validates its own Pydantic output.

Recommended retry policy:

- planner parse failure: retry planner once with validation error context;
- scenario parse failure: retry scenario once with validation error context;
- card validation failure: retry only the renderer/scenario branch that failed;
- image generation failure: do not retry more than once by default; fall back to text/cloze;
- package failure: fail the user-visible request and log stage artifacts.

Validation should check more than schema:

- no empty front/back;
- no answer leaked in basic-card question;
- cloze has meaningful `{{cN::...}}` deletions;
- generated visual cards include text answer fallback;
- image media files exist before packaging;
- card count respects user constraints unless the planner explicitly rejects them.

### 4.12 Logging and Observability

Workflow logging must be structured enough to debug a failed branch without replaying Telegram by
hand.

Required log fields:

- `workflow_id`;
- `workflow_type` such as `anki_generation`;
- `user_id` plus product-owned correlation metadata such as Telegram chat/message IDs when available;
- `node`;
- `attempt`;
- `branch_decision`;
- `model` and provider for model/tool nodes;
- elapsed milliseconds;
- validation error summary;
- generated artifact paths and cleanup status;
- final outcome: success, fallback, user-visible failure.

Logging rules:

- Never log full user content, full prompts, raw OCR text, or generated image bytes by default.
- Log short content hashes or truncated summaries for correlation.
- Log validation errors and schema paths because they are needed for JSON repair.
- Log image generation parameters (`model`, `size`, `quality`, `style_reference_version`) but not
  the full image payload.
- Every side-effect node must log artifact creation before the next node runs.

## 5. Framework Decision

The goal is not only persistence. The Anki pipeline should be easy to evolve like a small node
workflow: planner node, per-type scenario nodes, validation nodes, renderer branches, image tool
node, fallback node, retry loops, and later optional review/approval.

### 5.1 Decisions Locked

- Use a shared `WorkflowRunner`/processor layer for multi-step AI request execution.
- Use typed product-specific graphs on top of that runner; Anki is the first graph.
- Use LangGraph for graph flow control: conditional edges, cycles, retrace/fallback, node retry
  policy, and engine checkpoint-store/replay seams where product restart recovery needs them.
- Use Pydantic models for graph state and node outputs.
- Use LangChain structured output when the pinned dependency supports the needed API; otherwise use
  local Pydantic parse/retry wrappers for LLM JSON repair.
- Do not adopt Pydantic AI for the first implementation.
- Do not claim product-level restart recovery until checkpoint resumes are wired and validated for a
  concrete product workflow.
- Explicit commands and saved non-auto modes bypass any supervisor and go directly to the chosen
  workflow.
- A future supervisor/master agent may route high-level goals in `auto` or mixed-goal modes, but it
  should invoke typed graphs rather than directly managing low-level side effects.
- Use OpenAI Images API with `gpt-image-2` by default, while keeping Gemini/Nano Banana selectable
  behind the same provider-neutral image request. Use OpenAI `images.generate` for no-reference
  visual cards, OpenAI `images.edit` when OpenAI style/source reference images are used, and Gemini
  `generateContent` inline image parts for Gemini references.
- Logging/tracing is required from the first workflow implementation.

Framework coverage:

- Wrong JSON / invalid structured output:
  - LangChain `create_agent(..., response_format=ToolStrategy(...))` supports Pydantic/schema
    structured output and validation-error retries through `handle_errors`.
  - Pydantic AI supports typed output validation and `ModelRetry` with output retry budgets.
- Flow control:
  - LangGraph covers explicit state graphs, nodes, edges, conditional branches, loops, node retry
    policy, and optional checkpointing.
  - Persistence/checkpointing is one LangGraph capability, not the reason to use it by itself.
- Built-in OpenAI image tool:
  - The OpenAI Responses API image-generation tool can be part of a multi-step model run, but it
    does not replace application-level validation, fallback, media cleanup, or Anki packaging.

LangGraph vs pure LangChain:

- Pure LangChain is enough for prompt calls, structured outputs, tool calls, and mostly linear
  chains.
- LangGraph gives the bonus we care about here: explicit graph state, conditional edges, cycles,
  node-level retry policy, optional checkpointing, and a visual/node mental model close to n8n.
- For the Anki use case, the first meaningful graph bonus is not persistence. It is making
  `type_gate -> per_type_scenario -> validate -> render/generate -> validate -> retrace/fallback`
  explicit and testable.
- If the implementation ends up as a simple one-pass text/cloze generator, LangGraph is unnecessary.
  If image generation, prompt repair, web search, and fallback branches are first-class goals,
  LangGraph is the better control layer.

Supervisor/master agent:

- A supervisor agent can sit above product graphs and choose the user goal, workflow, tools, and
  high-level strategy when the user is in `auto` mode or a future mixed-goal mode.
- Explicit commands and saved non-auto settings bypass the supervisor and invoke the selected
  workflow directly.
- The supervisor should invoke typed graphs; it should not directly manage low-level file writes,
  image retries, package building, or cleanup.
- This keeps the "agent knows the ultimate goal" behavior while preserving bounded, testable
  execution inside each graph.

Current recommendation: use LangGraph as the workflow-control layer for multi-step AI request
flows, starting with Anki. Keep each graph small and typed; do not enable durable persistence until
restart recovery is a real requirement.

Near-term implementation:

- Pydantic models for graph state: `ContentSource`, `ImageAssetPlan`, `CardBuildPlan`,
  `TextCardScenario`, `ClozeCardScenario`, `VisualCardScenario`, `ImageGenerationRequest`,
  `RenderedCardSet`, and `AnkiPipelineState`.
- A thin local wrapper around graph execution for shared concerns: trace collection, node timing,
  retry counters, artifact cleanup hooks, and Telegram-safe error reporting.
- LangGraph nodes:
  - `parse_directives`;
  - `plan_image_assets`;
  - `plan_card_type`;
  - `prepare_text_scenario`;
  - `prepare_cloze_scenario`;
  - `prepare_visual_scenario`;
  - `validate_text_scenario`;
  - `validate_cloze_scenario`;
  - `validate_visual_scenario`;
  - `render_text_or_cloze`;
  - `render_visual`;
  - `generate_image`;
  - `validate_rendered_cards`;
  - `fallback_to_text`;
  - `package_cards`.
- Conditional edges:
  - uploaded image role influences card-set planning and visual scenario planning;
  - card kind routes to text/cloze/visual scenario planner;
  - each scenario planner routes to its matching validator and renderer;
  - validation failure routes to bounded retry or fallback;
  - image failure routes to fallback;
  - successful render routes to packaging.
- Use structured-output retry inside planner/per-type scenario nodes, either through LangChain
  structured output if the installed version supports the current API, or through a local retry
  wrapper.
- Split every LLM prompt into cache-friendly messages: static instructions/schema first, dynamic
  user/source state second. This is mandatory for new graph nodes and keeps caching provider-neutral.
- Stage artifact logging for debugging.

Reason:

- the product direction explicitly needs AI branching/looping, not only a fixed linear chain;
- a graph gives a readable place to add future visual variants, image prompt repair, occlusion,
  or human review nodes;
- the same graph conventions can support future request types without forcing every request through
  one generic agent prompt;
- LangGraph fits the existing LangChain dependency family better than introducing a separate
  agent framework only for orchestration.

Do not use LangGraph as a license to make every helper a node. Keep pure deterministic helpers as
plain functions. Nodes should represent stage boundaries where retry, branching, observability, or
side effects matter.

Durability decision:

- Start without durable checkpointing if simple in-process execution is enough.
- Add checkpointing/job persistence when stage outputs must survive restart, when image generation
  becomes slow enough that losing work hurts, or when human review/approval enters the normal flow.

LangGraph docs:

- <https://docs.langchain.com/oss/python/langgraph/durable-execution>
- <https://docs.langchain.com/oss/javascript/langgraph/persistence>

Pydantic AI remains a reasonable candidate if the system becomes agent/tool-heavy rather than
workflow-graph-heavy. Its official docs cover typed outputs, output validation retries, and tool
retries:

- <https://pydantic.dev/docs/ai/core-concepts/output/>
- <https://pydantic.dev/docs/ai/core-concepts/agent/>

Structured-output dependency decision: the current implementation uses local Pydantic parse/retry
wrappers through `StructuredLLMNode`. Revisit LangChain native structured-output retry only if it
reduces code while preserving the same bounded repair behavior.

### 5.2 Prompt Planning

Current implementation prompt usage:

- `DirectiveParser`: no prompt. Parses `[i ...]` tags deterministically into
  `AnkiDirectiveConstraints`.
- `ContentSource`: no prompt. Normalizes already-assembled Telegram text, OCR/description markers,
  and raw image bytes. Existing upstream image understanding still happens before this graph.
- `ImageAssetPlanner`: no prompt yet. Deterministically chooses `ignore_media`,
  `source_content_only`, `reuse_user_image`, `use_as_reference`, or `generate_new_visual` from
  directives and uploaded-image presence. Uploaded screenshots/documents are content sources by
  default; they become generated-image references only when `[i ref]` is used or a later AI gate
  explicitly chooses reference use.
- `CardSetPlanner`: uses `AnkiCardSetPlanner` when available. It asks the model to
  choose `basic`, `cloze`, or `visual_basic`, decide whether uploaded images should be ignored,
  reused, used as references, or replaced by generated visuals, and return a Pydantic
  `CardBuildPlan`. Forced card type/count directives are included as constraints and then enforced
  by graph normalization. Bad JSON/schema retries once with validation-error context. If the planner
  fails or chooses an impossible visual branch, the graph normalizes to text fallback.
- `TextScenarioPlanner` / `ClozeScenarioPlanner` / `VisualScenarioPlanner`: separate structured
  LLM prompts now prepare per-family scenarios before rendering. Each uses Pydantic parsing,
  validates required scenario fields, retries once with validation-error context, logs model-stage
  failures with a content hash, and receives directive-cleaned source content plus structured
  directives/build plans. User guidance from `[i ...]` is included as constraints and preserved
  for downstream rendering.
- `TextRenderer` / `ClozeRenderer` / current visual text renderer: uses the existing
  `AnkiCardService.extract_cards()` prompt. That prompt asks the model to create self-contained
  Anki cards, preserve original language, avoid answer leakage, create meaningful clozes, and
  return a Pydantic `AnkiCardSet`.
- `ImageGenerator`: uses `VisualCardScenario.visual_prompt`. With the production-wired visual
  planner this prompt is AI-prepared per visual scenario; deterministic prompt construction remains
  only as a local fallback when no visual scenario planner is configured.

Target prompt architecture:

1. `CardSetPlannerPrompt`

Purpose: choose the best single card family, image policy, conservative count, model profile needs,
and fallback plan. The current implementation lives in
`services/content/anki_card_set_planner.py`.

Input:

- normalized source content;
- OCR/description markers;
- image asset plan;
- user directives;
- user guidance/preferences;
- cost/quality policy.

Output: `CardBuildPlan`.

Prompt skeleton:

```text
You are planning an Anki card workflow, not writing the final card.

Choose exactly one card kind for this input: basic, cloze, or visual_basic.
Do not mix card families from one input.

Prefer visual_basic only when a generated or uploaded image can make recall easier, more memorable,
or more concrete without inventing facts. Choose cloze when the material is best remembered as a
sentence/context with meaningful hidden facts. Choose basic when the fact is exact, abstract,
acronym-like, definition-like, legal/procedural, or when a visual would add noise. Do not reject
visual_basic merely because the source is an abbreviation or definition if the operational meaning,
effect, contrast, or consequence can be pictured clearly.

If the user did not request an exact count, choose a target count. Prefer fewer cards:
- visual: usually 1;
- cloze/basic: usually 1, sometimes 2-3 for separate important facts;
- never more than 3 unless the user explicitly requested more.

Respect explicit user constraints unless impossible. If visual is impossible or low value, choose a
text fallback and explain why.

Preserve user guidance for downstream scenario prompts, e.g. humor, tone, style, or "focus on X".

Return only valid JSON matching CardBuildPlan.
```

2. `TextScenarioPlannerPrompt`

Purpose: prepare basic Q/A content rules before rendering.

Output: future `TextCardScenario` with fields such as `front_intent`, `back_intent`,
`facts_to_test`, `answer_constraints`, `count`, and `tags`.

Prompt skeleton:

```text
Prepare a basic Anki Q/A scenario from the source.

Rules:
- One card tests one fact unless the user requested merge.
- The front must be self-contained and must not leak the answer.
- The back must be concise, correct, and grounded only in the source.
- Preserve the source language.
- Do not mention screenshots, messages, OCR, sender names, or source formatting.

Return only valid JSON matching TextCardScenario.
```

3. `ClozeScenarioPlannerPrompt`

Purpose: choose meaningful cloze targets before rendering, instead of letting generic generation
hide random words.

Output: future `ClozeCardScenario` with fields such as `source_sentence`, `cloze_targets`,
`rewritten_sentence`, `why_targets_matter`, and `tags`.

Prompt skeleton:

```text
Prepare a cloze Anki scenario.

Rules:
- Use cloze only when the surrounding sentence makes the blank answerable.
- Hide key facts, terms, relationships, numbers, or contrasts.
- Never hide filler words, obvious grammar, or meaningless connective text.
- If several important facts are in one sentence, use separate deletions.
- Keep enough context around every deletion.
- Preserve the source language.

Return only valid JSON matching ClozeCardScenario.
```

4. `VisualScenarioPlannerPrompt`

Purpose: design the visual card and image-generation brief after the card-set decision.

Output: `VisualCardScenario`.

Prompt skeleton:

```text
Prepare a visual Anki card scenario.

The image is a memory aid, not an authoritative diagram. It must not introduce unsupported facts.

Priority order:
1. exact grounded question/answer;
2. image explains the answer relationship, not only the topic term;
3. one memorable hook that helps recall;
4. sparse useful text/callouts only when they clarify the hook;
5. deck/style consistency.

Decide:
- whether to reuse uploaded image, use it as a source/style reference, or generate a new visual;
- whether layout should be text_front_image_back, image_front_text_back, image_both_sides, or
  image_front_image_back;
- whether one image is enough;
- the exact `question_text` shown on the Anki front;
- what exact written answer must remain available if the image is bad.

Write an image prompt that avoids dense or decorative text. Sparse labels/callouts are allowed when
they improve recall, especially for abbreviations that need a supported meaning or operational
effect. Prefer a memorable, high-signal illustration over decorative art. Default visual tone is
catchy, witty, and lightly
humorous when compatible with accuracy. The memory hook must map directly to the tested fact and
stay subordinate to source grounding, exact labels, qualifiers, and phone-readable clarity. If the
user asks for humor, collage, story, or catchiness, the prompt must keep that composition goal and
map the memory hook directly to the tested fact instead of flattening it into a generic icon grid.
The image must support the exact written answer; reject prompts that merely repeat the question
term, draw a happy object, or make a generic illustration without showing the relationship,
consequence, contrast, grouping, or decision cue being tested.
Do not rewrite source item names into generic labels or unexplained abbreviations; use exact short
source labels or visual props/icons with precise wording kept in the written answer. Do not invent
unofficial acronym expansions.
For flows, exchanges, forces, contrasts, or cause/effect, require distinct arrows, positions,
colors, or before/after states so the relationship is visually unambiguous.

Return only valid JSON matching VisualCardScenario.
```

5. `ImagePromptRepairPrompt`

Purpose: repair only the visual prompt when generated-image validation rejects the output or the
prompt is too vague.

Output: corrected `VisualCardScenario` or a decision to fall back.

Prompt skeleton:

```text
The previous visual prompt failed validation:
{validation_error}

Revise only the image prompt and visual layout fields. Do not change the studied fact unless the
visual approach is unsuitable. If visual is unsuitable, set fallback_kind and explain why.

Return only valid JSON matching VisualCardScenario.
```

6. `RenderedCardRepairPrompt`

Purpose: bounded JSON/card repair for final card outputs.

Output: corrected `RenderedCardSet` or fallback decision.

Prompt skeleton:

```text
The rendered card failed validation:
{validation_error}

Repair the card while preserving the scenario intent and source-grounded facts.

Rules:
- no empty fronts/backs;
- no answer leakage in basic questions;
- cloze cards must contain meaningful {{cN::...}} deletions;
- do not invent facts;
- preserve source language.

Return only valid JSON matching the requested schema.
```

Prompt execution rules:

- Every AI planner/renderer prompt must use Pydantic schema validation.
- A stage may retry once with validation-error context.
- After retry failure, route to a smaller branch or `fallback_to_text`.
- Do not log full prompt/source text by default; log model, node, schema, validation summary, and
  short content hash.
- Keep prompt text inside the product graph or planner classes, not in `AnkiProcessor`.

## 6. Persistence and Cleanup

Current in-memory state is acceptable for the first generated-image experiment if cleanup is strict:

- auto-mode pending windows may be lost on restart;
- Anki buffer may be lost on restart;
- local temporary media files may be orphaned.

Required cleanup improvements before generated images:

- generated image files live under the existing Anki media temp tree;
- buffer clear/export/undo removes generated media it owns;
- failed image-generation runs remove partial files;
- logs include enough stage detail to reproduce a failed run.

Introduce an `anki_jobs` table only when restart recovery matters. Do not add it only to preserve
current ephemeral behavior.

## 7. Implementation Status and Gap Review

Ready today:

- content intent routing and explicit command/settings precedence;
- Anki processor entry point and Telegram delivery;
- upstream image ingestion with OCR/summary and raw image bytes;
- `.apkg` packaging through `genanki`;
- in-memory Anki buffer with export, clear, and undo.

Implemented in this branch:

- `environment.yml` is the dependency source of truth. The LangChain/LangGraph/OpenAI stack is
  pinned together to avoid cross-framework resolver drift:
  `langchain-core==1.4.0`, `langchain-openai==1.2.2`, `langgraph==1.2.4`,
  `openai==2.41.0`, and `pydantic==2.13.4`.
- `WorkflowRunner` adds workflow IDs and start/end/error logs.
- `AnkiGenerationGraph` is the first typed product graph over the shared runner.
- Pydantic graph models exist for `ContentSource`, image asset plans, card build plans,
  per-type scenarios, generated media, rendered cards, and pipeline state.
- `ContentSource` formalizes the existing thread tuple/enriched-image shape.
- `[i ...]` now supports visual, generated-image, reference-image, and reuse-image directives.
- `ImageAssetPlanner` decides uploaded image roles before card-set routing.
- `AnkiCardSetPlanner` runs as the AI card planning gate when available. It returns
  `CardBuildPlan`, retries once on invalid JSON/schema, and can choose basic, cloze, generated
  visual, uploaded-image reuse, reference-image generation, no image, and target card count.
- `CardSetPlanner` owns one card family per input, conservative count, image policy, downstream
  user guidance, model-profile needs, and fallback plan.
- Direct per-node model config now exists: `ANKI_DECISION_MODEL`, `ANKI_SCENARIO_MODEL`,
  `ANKI_RENDER_MODEL`, `ANKI_QUALITY_MODEL`, and `ANKI_COMPLEX_SUPERVISOR_MODEL`, with text-node
  profiles defaulting back to `ANKI_CARD_MODEL`.
- Forced basic/cloze/visual directives constrain the AI planning gate and are normalized against
  feasibility; they do not skip planning of unspecified parameters.
- User-requested count constraints override AI-planned count. If count is not explicit, the planner
  can choose a conservative target count and the renderer receives it. Current implementation clamps
  AI-planned count at three unless the user explicitly requested more.
- The graph treats the card-set plan as authoritative: the final text/cloze renderer is called
  with the planned card type instead of letting the old card prompt secretly re-decide.
- `TextScenarioPlanner`, `ClozeScenarioPlanner`, and `VisualScenarioPlanner` are production-wired
  into the container. They run separate structured prompts, validate typed Pydantic output, retry
  once on bad JSON/schema, and pass rendering guidance into the final renderer.
- AI planners receive directive-cleaned source content while directives remain structured
  constraints. This prevents `[i ...]` control text from leaking into scenario/card prompts.
- Scenario validation, rendered-card validation, bounded retry/fallback edges, and fallback loop
  guards are explicit.
- `TextRenderer`, `ClozeRenderer`, `VisualRenderer`, and `FallbackTextRenderer` are split into
  branch-specific renderer classes. The graph orchestrates them instead of directly owning card
  materialization.
- `AnkiRenderedCardEvaluator` exists and is production-wired. It performs deterministic hard checks,
  can inspect generated images with the quality model, returns structured accept/repair/retry/fallback
  decisions, and retries malformed evaluator JSON once.
- Quality rejection can route to card-set replan, render repair, per-type scenario retry, or
  fallback. Generated image/audio artifacts are cleaned up when the graph abandons a branch.
- Run-level cost guards cap image generation and quality repair loops before another paid call can
  occur.
- `OpenAIImageGenerator`, `GeminiImageGenerator`, and comparison-mode provider selection exist
  behind a reusable `ImageGenerator` provider seam. OpenAI uses `gpt-image-2` by default; Gemini
  uses `gemini-3.1-flash-image` by default and can be changed to `gemini-2.5-flash-image` for
  cheaper comparisons.
- Generated visual cards are wired behind `ANKI_IMAGE_GENERATION_ENABLED` for explicit `[i gen]`
  requests and `ANKI_AUTO_IMAGE_GENERATION_ENABLED` for AI-chosen generated visuals; generated media
  is attached to cards, packaged, buffered, and cleaned up if the request fails before buffer
  ownership.
- Source-image references and configured character/design style reference paths are supported for
  image-generation requests.
- Node-level structured logs include workflow ID, user ID, node, attempt, branch decision,
  validation error summary, elapsed time, and artifact paths.
- `WorkflowSupervisor` and `WorkflowDecisionPlanner` exist over the product-agnostic instrument
  registry. Without a planner, the supervisor routes by `goal.workflow_type`; with a planner, it
  can choose among registered instruments while keeping product-specific internals inside the
  selected graph/tool.
- Unit tests cover graph routing, visual generation, source-image references, quality repair caps,
  image-generation caps, fallback on image failure, reusable supervisor routing, and existing Anki
  processor behavior.

Remaining rollout and product-validation items:

- The current PPLA runtime style references are selected as split character/design assets with
  version `ppla-split-v3`; broader deck-quality evaluation remains open.
- The evaluator is implemented, but no large golden-set eval has been built yet. Current coverage is
  focused unit/integration smoke, not a broad benchmark of card quality.
- Directive parsing still handles a single `[i ...]` block.
- Automatic generated image generation is disabled by default to avoid surprise cost. Explicit
  `[i gen]` requests are enabled by default and still bounded by per-run image-call caps.
- Supervisor/instrument routing exists, but Telegram content-mode routing still correctly uses
  explicit command/settings precedence before auto mode. The supervisor does not replace that
  product routing path.
- True live Telegram e2e has not been run. Current coverage is unit tests, API-backed graph
  integration tests, and a processor-level delivery-boundary test with Telegram I/O mocked.

Highest-risk implementation areas:

- Over-generalizing the workflow layer until product graphs lose useful type safety.
- Under-instrumenting logs, making branch/retry failures hard to diagnose.
- Letting image-generation side effects happen before artifact ownership is recorded.
- Creating unbounded repair loops around bad JSON, bad clozes, or rejected visuals.
- Letting visual cards overrule study quality just because they are catchy.

## 8. Stages

### Stage A - Update Current Docs

Status: done.

- Mark implemented baseline honestly.
- Remove obsolete `gpt-4o-mini`/`requirements.txt` assumptions.
- Document the multi-step card planning target.
- Record the framework recommendation and escalation point.

Acceptance: this document matches the current code and the intended generated-image direction.

### Stage B - Reusable Graph Planning Without Image Generation

Status: done. The graph, models, runner, routing, validation, fallback, card-set structured-output
retry, per-type scenario AI prompts, and tests exist.

- Add Pydantic models for image asset plan, build plan, and per-type scenarios.
- Move `[i ...]` parsing into structured planning constraints.
- Add a small shared workflow runner/wrapper for graph invocation, trace capture, retry counters,
  and artifact cleanup hooks.
- Add a small LangGraph-based `AnkiGenerationGraph`.
- Add graph nodes for directive parsing, image asset planning, card-set planning, per-type
  scenario preparation, per-type scenario validation, text/cloze rendering, rendered-card
  validation, fallback, and packaging.
- Add the AI card-set planner prompt with Pydantic validation, one JSON/schema repair attempt, and
  graph fallback when the plan is invalid or impossible.
- Keep final rendered output equivalent to current text/cloze cards.
- Add tests for directive parsing, image asset decisions, planner constraints, conditional routing,
  bounded retry, fallback, and cloze quality rules.

Acceptance: existing Anki tests pass; graph execution can produce current cards without image calls.
The shared runner can execute another future graph without knowing Anki-specific fields.

### Stage C - Renderer Split

Status: done. Branch-specific renderer classes exist; text/cloze still delegate final JSON
materialization and cloze repair to `AnkiCardService.extract_cards()`.

- Add `TextRenderer`, `ClozeRenderer`, and `VisualRenderer` classes.
- Keep renderers as plain classes/functions called by graph nodes.
- Keep `AnkiCardService` focused on packaging and final card extraction only where still needed.
- Validate rendered cards before packaging.

Acceptance: card rendering is branch-specific and testable without Telegram or OpenAI.

### Stage D - Generated Visual Cards

Status: implemented behind a disabled-by-default rollout switch. The provider seam, OpenAI
`gpt-image-2` implementation, config, graph node, reference-image request path, packaging,
buffering, quality evaluator, unit tests, API-backed generated-image graph integration tests, and
manual artifact preservation exist. True live Telegram e2e and broader quality evals remain rollout
validation tasks.

- Add `ImageGenerator` provider and config.
- Add `OpenAIImageGenerator` using `ANKI_IMAGE_MODEL=gpt-image-2` by default, with
  `ANKI_IMAGE_STABLE_MODEL=gpt-image-2-2026-04-21` available as a stable fallback/pin.
- Generate one optional image from `VisualCardScenario.visual_prompt`.
- Use `images.generate` for no-reference requests and `images.edit` for style/source reference
  images.
- Add config for default character/design style reference images and one style version.
- Attach generated media to Anki cards.
- Fall back to text/cloze on failure or low visual value.

Acceptance: generated-image cards can be created and packaged with media by automated graph
integration tests. Manual import/review and visual-quality evaluation are still required before
calling generated visuals production-ready. Text fallback remains available.

### Stage E - Durability and Job Recovery

Do this only if Stage D exposes restart/retry/human-review needs that are painful without persisted
graph state.

Options:

- add an `anki_jobs` table for user-visible job metadata;
- enable LangGraph checkpointing for stage state;
- persist generated media ownership so cleanup can recover after restart;
- add human review/approval checkpoints if visual cards need manual approval.

Acceptance: the persistence layer removes a measured failure mode, not just architectural
discomfort.

### Stage F - Image Occlusion

Future stage only.

Occlusion becomes useful when:

- the source/generation reliably produces diagrams worth testing visually;
- masks can be represented in Anki cleanly;
- generated media lifecycle and imports are stable;
- there is enough manual evidence that visual cards improve recall for this use case.

## 9. Main Risks

- Over-generalization: the shared workflow layer should manage execution mechanics, not erase
  product-specific typed graphs.
- Unbounded loops: every repair/regeneration branch needs explicit attempt caps and fallback exits.
- Side-effect retries: image/file/tool nodes must record artifacts before later nodes can retry,
  retrace, or fall back.
- Visual overuse: catchy images can produce worse study cards than text. The planner must explain
  why visual recall helps.
- Factual hallucination: generated images should usually be mnemonic, not authoritative diagrams.
- Cost and latency: image generation needs a strict per-message cap and fast fallback.
- Style drift: reference image/style changes should be versioned.
- Cloze quality: cloze targets must be chosen during scenario planning, not left to generic
  renderer prompting.
- Hidden state: current in-memory buffer is fine for experiments, but not for durable job recovery.
- Graph over-modeling: LangGraph nodes should represent meaningful retry/branch/side-effect
  boundaries, not every pure helper call.

## 10. Open Decisions

- Should generated visual cards be enabled by default in `auto`, or only when `/anki`/`[i visual]`
  requests them?
- Should PPLA split references remain the long-term production refs after broader card-quality eval?
- Should the first visual card put the generated image on the front with text answer on the back,
  or include a small generated front image plus normal text Q/A?
- What is the maximum acceptable latency for one Anki request?
- At what point does restart recovery matter enough to add `anki_jobs` or LangGraph checkpointing?
