# Anki Prompt Quality Review

Status: reviewed during generated-visual quality fix.

## Quality Bar

The Anki workflow should optimize for retrieval quality first:

- one clear study goal per input;
- one card family per input for the first production workflow;
- one generated image by default;
- exact written answer always present;
- generated image as mnemonic support, not the only answer;
- memorable visual hook when it helps recall;
- conservative card count must still preserve coverage: when a source is one coherent sibling
  list/set, one card should test the whole set or grouped categories, not a single sampled item;
- no generic decoration, term-only posters, or bland icon grids when a stronger grounded hook is
  feasible;
- source qualifiers preserved, especially aviation/legal conditions such as "if equipped" and
  "if applicable".

## Prompt Cache Layout

Text LLM calls use stable static/system prompts followed by dynamic user/source tails:

- `AnkiCardSetPlanner`: static role, rules, schema; dynamic capabilities, directives, source.
- `TextScenarioPlanner`: static basic-card rules and schema; dynamic source/directives/build plan.
- `ClozeScenarioPlanner`: static cloze rules and schema; dynamic source/directives/build plan.
- `VisualScenarioPlanner`: static visual-card rules, PPLA identity, schema; dynamic source,
  directives, build plan, image plan.
- `AnkiCardService`: static renderer rules and schema; dynamic rendering instructions and content.
- `AnkiRenderedCardEvaluator`: static quality rules and schema; dynamic source, plan, cards, media.

Current static prompt size estimate:

| Prompt | Approx tokens before schema expansion |
| --- | ---: |
| Card set gate | 578 |
| Text scenario | 172 |
| Cloze scenario | 181 |
| Visual scenario | 2279 |
| Renderer | 977 |
| Quality evaluator | 763 |
| Image style prefix | 1446 |

The visual prompt is long, but most of it is stable and cacheable. To reduce instruction-loss risk,
the visual scenario prompt and final image prompt now start with a short priority block before the
long style details.

Prompt templates live under `prompts/` and are loaded with `ai_workflow_engine.prompt_loader`.
Templates use LangChain f-string-style `{variable}` placeholders, not Jinja. The loader dedents and
trims outer newlines so files stay readable without leaking Python indentation into prompts.
This is intentional: current prompts only need flat variable substitution, while Jinja/Mustache
control flow would make templates harder to audit and easier to drift from the cacheable
static/dynamic split.

OpenAI chat calls can pass cache-affinity hints through `OPENAI_PROMPT_CACHE_KEY_PREFIX` and
`OPENAI_PROMPT_CACHE_RETENTION`. Images API calls currently receive one prompt string, so the
cache-friendly pattern is stable style prefix first, card-specific brief second. The installed
OpenAI client surface for `images.generate`/`images.edit` does not expose `prompt_cache_key`.

Usage summaries report cached input tokens and price with cached rates when provider metadata
contains cache fields such as `cache_read`/`cached_tokens`.

References checked for this policy:

- OpenAI Prompt Caching guide:
  https://developers.openai.com/api/docs/guides/prompt-caching
  Static/repeated prompt prefix first, dynamic user content last; cached token counts are reported in
  usage metadata.
- LangChain prompt/model interface docs:
  https://docs.langchain.com/oss/python/langchain/overview
  LangChain keeps the model interface provider-neutral; this project keeps provider-specific cache
  controls in adapters while repository prompts stay plain-template files.

## Prompt Risks

- Long visual prompts can cause the model to satisfy style while missing the actual studied
  relationship. Mitigation: priority block first, quality evaluator rejects bad image-answer
  mapping.
- Generated image text can be wrong. Mitigation: allow only sparse callouts, keep exact answer in
  Anki text, reject abbreviation-only visuals.
- "Clean infographic" can become dull and less memorable. Mitigation: default witty/catchy hook,
  and evaluator rejects sterile grids when a grounded hook was feasible.
- The reusable aircraft mascot can become repetitive. Mitigation: prompts now treat the mascot as
  optional; use it only when its role/reaction explains the tested fact, and evaluator may reject
  decorative mascot use.
- Humor can distort legal/procedural facts. Mitigation: source grounding and qualifier preservation
  outrank humor.
- Acronym/definition material can be routed to text too aggressively. Mitigation: card-set gate no
  longer rejects visuals merely because the source is an abbreviation/definition when an operational
  meaning, effect, contrast, or consequence can be pictured.
- Generated lists can invent counts or remove conditions. Mitigation: list/rule-set prompt rules
  prohibit misleading counts and unconditional treatment of conditional items.
- Conservative one-card planning can drop list coverage if the planner treats one sibling item as
  representative. Mitigation: card-set, scenario, renderer, and quality prompts now define count by
  independent study objective, require full sibling-set coverage for one grouped card, and prefer
  2-3 logical groups when one answer would be unreadable.

## PPL(A) Mind Tests

These are manual reasoning tests against the current prompt contracts, not paid live generations.

### 1. QNH Altimeter Setting

Input:
`QNH is the altimeter sub-scale setting that makes the altimeter indicate aerodrome elevation when
on the ground.`

Expected flow:

- Card-set gate may choose `visual_basic` because the operational effect can be pictured.
- Visual scenario front: "What does setting QNH make the altimeter indicate on the ground?"
- Back: "The aerodrome elevation above mean sea level."
- Image brief: altimeter knob/scale connected to runway/elevation marker, one short callout such as
  `QNH -> field elevation`.

Failure to reject:

- image only says `QNH`;
- fake acronym expansion;
- happy altimeter with no operational relationship.

Current prompts cover this after the abbreviation/effect update.

### 2. Article 29 Documents Carried In Aircraft

Input:
`International flights must carry certificate of registration, certificate of airworthiness, crew
licences, radio station licence if equipped with a radio, journey log book, passenger list, and, if
applicable, cargo manifest and cargo declaration.`

Expected flow:

- With `[i gen]` or visual preference, choose `visual_basic`.
- Front asks which original documents must be carried under Article 29.
- Back preserves exact item names and qualifiers.
- Image brief: funny overloaded aircraft/paperwork collage with grouped folders; conditional
  documents visually separated, not included in a false "7 must" headline.

Failure to reject:

- "7 documents must be on board" when some are conditional;
- `C of R` or broad labels that hide exact names;
- dull tile grid when user asked funny collage.

Current prompts cover qualifiers, exact names, and requested composition.

### 2b. ICAO Annex List

Input:
`ICAO Annexes: Annex 1 Personnel Licensing; Annex 2 Rules of the Air; Annex 3 Meteorological
Service; ...`

Expected flow:

- Without explicit count, choose one `basic` or `visual_basic` card if the study objective is the
  annex set itself.
- Front asks for the ICAO annexes in the supplied set/range.
- Back includes every high-value annex item supplied, or grouped ranges if the list is too long.
- If the answer becomes unreadable, split into 2-3 logical/range groups, not one card per annex by
  default.

Failure to reject:

- one card whose answer includes only Annex 1 or one representative annex;
- one card per annex when the source is clearly one list and the user did not ask for item-by-item
  drilling;
- omitted annexes without a deliberate grouping decision.

Current prompts cover this through the sibling list/set coverage rules.

### 3. Sovereign State Air Law Precedence

Input:
`Where a sovereign state passes laws with force in its country, that individual state's Air Law
prevails over International (ICAO) Air Law.`

Expected flow:

- Gate may choose `basic` because this is legal/abstract, unless visual is explicitly requested or a
  clear precedence metaphor is possible.
- Front: "Which Air Law prevails inside a sovereign state's own territory?"
- Back: "That individual state's Air Law."
- Optional visual: local law book/sign clearly outranks an ICAO book inside a border.

Failure to reject:

- image implying ICAO never matters;
- question wording that reveals "individual state";
- joke that changes legal meaning.

Current prompts mostly cover this; visual choice should stay conservative in auto mode.

### 4. Converging Aircraft Right Of Way

Input:
`When two aircraft are converging at approximately the same level, the aircraft that has the other
on its right shall give way.`

Expected flow:

- Gate should prefer `visual_basic` because spatial relationship is visual.
- Front asks which aircraft gives way in a converging same-level situation.
- Back: "The aircraft that has the other aircraft on its right gives way."
- Image brief: top-down runway/sky diagram with two aircraft, clear right-side relationship,
  arrow/yield cue, no ambiguous camera angle.

Failure to reject:

- perspective makes right/left ambiguous;
- image only shows two aircraft smiling;
- back answer missing "on its right".

Current prompts cover spatial relationship and clear arrows/positions.

### 5. Transponder Code 7600

Input:
`Transponder code 7600 indicates radio communication failure.`

Expected flow:

- Gate should usually choose `basic` unless visual is explicitly requested, because exact digits in
  generated image text are risky.
- Front: "Which transponder code indicates radio communication failure?"
- Back: "7600."
- Optional visual if forced: cockpit transponder display with `7600` plus broken radio icon; written
  answer still carries the exact code.

Failure to reject:

- generated image shows wrong digits;
- image text becomes the only answer;
- visual joke obscures the exact code.

Current prompts should route this to basic by default and keep exact answer in text if visual is
forced.

## Current Prompt Locations

- Card-set gate: `prompts/anki/card_set.static.prompt`,
  `prompts/anki/card_set.dynamic.prompt`, `prompts/anki/card_set.repair.prompt`.
- Text scenario: `prompts/anki/text_scenario.static.prompt`,
  `prompts/anki/text_scenario.dynamic.prompt`.
- Cloze scenario: `prompts/anki/cloze_scenario.static.prompt`,
  `prompts/anki/cloze_scenario.dynamic.prompt`.
- Visual scenario: `prompts/anki/visual_scenario.static.prompt`,
  `prompts/anki/visual_scenario.dynamic.prompt`.
- Final text/cloze renderer: `prompts/anki/renderer.static.prompt`,
  `prompts/anki/renderer.dynamic.prompt`.
- Final image style prefix: `prompts/anki/image_style_prefix.prompt`.
- Rendered-card evaluator: `prompts/anki/quality.static.prompt`,
  `prompts/anki/quality.dynamic.prompt`, `prompts/anki/quality.repair.prompt`.
- Shared workflow supervisor: `prompts/workflow/supervisor.static.prompt`,
  `prompts/workflow/supervisor.dynamic.prompt`, `prompts/workflow/supervisor.repair.prompt`.
- Shared structured-output repair fallback: `prompts/workflow/structured_llm.repair.prompt`.
- Task parser prompts: `prompts/tasks/task_create.full.prompt`,
  `prompts/tasks/task_create.static.prompt`, `prompts/tasks/task_create.dynamic.prompt`.
