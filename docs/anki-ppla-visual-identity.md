# PPLA Anki Visual Identity

Status: design note captured after the first successful generated visual card.

## Goal

Generated image cards should feel like one coherent PPLA study deck, not unrelated one-off images.
The style should make aviation law/procedure facts memorable while staying accurate enough for
exam review.

## Product Soul

The point is not to make pretty aviation pictures. The point is to turn dry PPL(A) facts into
retrieval-first cards that are easier to remember, import, and review on a phone.

- The front asks a real retrieval question.
- The back gives the exact useful answer.
- The visual creates a memorable hook for that answer: consequence, contrast, grouping, operational
  relationship, role conflict, or a grounded joke.
- Humor is a study tool, not decoration.
- A bland clean infographic can still fail if it does not improve recall.
- A funny image can still fail if it changes a legal/procedural fact, hides a qualifier, or makes
  the image the only answer.

## Direction

- Use a bright aviation training-poster look: semi-realistic cartoon, clean airport/cockpit context,
  soft shadows, high readability.
- Keep humor as a default memory aid: one funny visual metaphor, exaggerated prop, scale joke, or
  aircraft reaction that directly maps to the tested fact. Humor should make the answer easier to
  recall, not merely make the image cute.
- Reuse a generic high-wing trainer aircraft character, Cessna-172-like but not branded. Give it a
  consistent nose/eyes/blue-stripe silhouette across cards.
- Give the aircraft subtle Labrador-like personality only: loyal, warm, eager-to-help, gentle,
  slightly goofy. Do not add fur, ears, paws, tail, or animal body parts.
- Keep aircraft geometry stable. The safest visible propeller treatment is a single centered
  translucent spinning propeller disk. Use an exact centered two-blade propeller only for a close,
  focal, static trainer. For small, angled, background, or multi-aircraft scenes, prefer a spinning
  disk, cropped nose, distant silhouette, jet/airliner, or no propeller detail rather than asking
  the model to count blades.
- Treat the trainer as the main deck mascot, not a mandatory subject. Use it when the character
  reaction helps recall. Use airliners, cockpit-only views, ATC/radio scenes, airport/runway views,
  documents, maps, weather cues, typography/callout cards, icons, or simple diagrams when those fit
  the fact better.
- Use a stable palette: sky blue, white, navy, safety orange, warm yellow, and small green accents.
- Prefer clear scenes that map directly to the concept: documents strapped to an aircraft,
  overloaded paperwork, airflow/pressure metaphors, ATC/radio/checklist cues, cockpit instruments,
  route maps, and airport ground scenes.
- Avoid meme formats, sarcasm, cultural references, brand references, slapstick chaos, and jokes
  that could imply false aviation/legal facts.

## Card UX Rules

- One concept per visual card. A generated image should create one recall hook, not summarize the
  whole lesson.
- Prefer text-front, answer-plus-image-back. The front should ask the retrieval question; the image
  should usually reinforce the answer after recall, unless the image itself is the object being
  tested.
- Keep the image phone-readable: one focal point, clear foreground/background separation, simple
  silhouettes, strong contrast, and enough whitespace.
- Use signaling instead of explanation: arrows, grouping, scale, contrast, before/after placement,
  or spatial proximity.
- Avoid excess context. Do not turn full paragraphs, full legal clauses, or entire slides into
  visual cards.
- Avoid dense labels because generated text can be unreliable. Use exact regulatory wording in the
  card answer; allow only short labels when they materially improve recall.
- Default visual tone is catchy, witty, memorable, and lightly humorous when compatible with
  accuracy. Use one funny exaggeration, concrete consequence, clever metaphor, role conflict, or
  character reaction that maps directly to the tested fact.
- The memory hook is subordinate to correctness: exact source facts, qualifiers, labels, and
  phone-readable clarity beat humor when they conflict.
- Do not confuse "clean" with "flat". When a funny collage, overloaded scene, or character/story
  composition makes the fact more memorable and remains accurate, prefer that over generic tidy
  tile grids.
- Typography should be controlled and sparse. Generated images should usually be visual first; when
  labels help recall, use 1-3 short friendly labels/callouts, never legal wording or answer text.
- Short explanatory callouts are allowed when they clarify the memory hook. For aviation
  abbreviations/acronyms, prefer a brief supported meaning/effect callout over an abbreviation-only
  poster. Do not invent unofficial acronym expansions.
- For list/procedure/rule-set cards, preserve qualifiers. Conditional items such as "if equipped" and
  "if applicable" must be shown as conditional extras or handled in the written answer, not turned
  into a flat unconditional count.
- For lists, show grouped objects/folders rather than a written checklist. For priority/precedence,
  show one object clearly winning. For cause/effect, show the two sides with arrows or contrast.
- Image occlusion remains a separate future card family. Use it for stable diagrams/tables/maps
  where hiding a part is the task; do not use it for generated illustrative scenes by default.

## Prompt Rules

- Keep this visual identity block in the static prompt prefix whenever possible. Put per-card facts
  and user guidance after it so provider prompt caching can reuse the style instructions.
- The runtime image prompt must describe the design in text even when reference images are passed:
  text rules are cacheable and more controllable, while image references mainly stabilize style.
- Uploaded screenshots/documents are content sources by default, not visual references. Use them as
  generated-image references only when the user explicitly asks for reference/style/composition reuse
  or the image appearance itself is the thing being tested.
- The final Images API prompt also receives a deterministic style prefix. This prevents a short
  AI-produced `visual_prompt` from dropping critical rules such as no broken propellers, character
  optionality, sparse typography, and grounded humor.
- Short labels are allowed only when they improve recall.
- Sparse explanatory text is allowed when it helps recall; abbreviation cards should show the
  supported meaning/effect or a clear visual relationship, not just the abbreviation.
- Do not rewrite source item names into generic labels or unexplained abbreviations. If exact text
  is too long or unreliable for generated typography, use visual props/icons and keep precise wording
  in the card answer.
- Avoid dense text, watermarks, UI chrome, brand names, and unsupported extra details.
- Avoid fake institutional marks: no invented seals, shields, crests, flags, official-looking
  badges, globe marks, or pseudo-ICAO/UN-style emblems. Use plain folders, color blocks, tabs,
  labels, or simple non-institutional icons instead.
- Do not invent regulatory obligations, aircraft equipment, dates, or document names.
- The image should support the written answer, not replace it.
- The image must answer "why this answer?" visually. Reject term posters: images that only repeat
  the tested term, show a happy generic object, or decorate the card without showing the answer
  relationship, effect, consequence, contrast, grouping, or decision cue.
- For list-based cards, group items visually into separate objects or folders.
- For cause/effect or physics cards, show the relationship with arrows, contrast, or visual metaphor.
- For law/procedure cards, use memorable but factual scenes with the aircraft character reacting to
  the rule.
- For aviation/PPLA source material, treat the recurring trainer aircraft as one strong available
  memory hook, not the default obligation. Choose documents, maps, cockpit/radio views, airliners,
  icons, diagrams, or typography/callout compositions when those explain the fact better.
- Keep visual prompts grounded: if the source does not specify a document, aircraft equipment,
  number, date, legal term, or exact label, do not invent it.

## Reference Image Plan

- Use two high-quality runtime reference images:
  - character reference:
    `assets/anki/ppla-character-reference-v3.png`;
  - general deck design/typography reference:
    `assets/anki/ppla-design-reference-v2.png`.
- Keep the separate human-readable UX/style guide sheet for review under:
  `assets/anki/ppla-visual-guide-sheet.png`.
- Set:
  - `ANKI_STYLE_CHARACTER_REFERENCE_IMAGE=assets/anki/ppla-character-reference-v3.png`
  - `ANKI_STYLE_DESIGN_REFERENCE_IMAGE=assets/anki/ppla-design-reference-v2.png`
  - `ANKI_STYLE_REFERENCE_VERSION=ppla-split-v3`
- The character reference locks the recurring trainer aircraft identity.
- The design reference locks the palette, props, page rhythm, aviation visual vocabulary, and
  answer-side illustration/typography language.
- Generate reusable deck reference assets with `quality=high`. Per-card images may stay on
  `quality=low` or `quality=medium` for cost control, because the one-time reference cost is
  amortized and a weak reference degrades style consistency across downstream generated cards.
- Do not use a text-heavy guide sheet as a runtime reference unless deliberately testing it; it can
  leak fake labels or typography into generated card images.

## Current Implementation Fit

- The graph supports separate character/design reference images and passes both to the OpenAI image
  edit request when they exist; the Gemini/Nano Banana adapter sends reference images as inline
  image parts.
- Image generation is provider-switchable through `ANKI_IMAGE_PROVIDER=openai|gemini|comparison`.
  The graph keeps one provider-neutral request shape, so visual identity guidance remains shared
  unless provider-specific evals show a real quality gap.
- Final image prompts are built by `AnkiImagePromptPolicy`, which combines the shared style prefix,
  shared educational image rules, provider-specific OpenAI/Gemini/comparison guidance, and the
  card-specific scenario brief.
- `AnkiImagePromptPolicy` adds a dynamic numeric/value guard: if the source/scenario provides no
  numeric values, generated image prompts explicitly forbid invented numbers, dial readouts,
  altitudes, pressures, frequencies, and units.
- Gemini aspect/size response-format controls are opt-in; the default Gemini adapter preserves
  prompt/reference behavior while avoiding schema-specific failures during live model rollout.
- Generated media metadata already records `style_reference_version`.
- The runtime visual scenario prompt includes PPLA style/UX rules for aviation material.
- Generated assets:
  - `assets/anki/ppla-character-reference-v3.png`: current high-quality recurring aircraft
    character reference; uses one full aircraft and a spinning propeller disk to avoid propeller
    ambiguity.
  - `assets/anki/ppla-design-reference-v2.png`: current high-quality deck design/typography
    reference for short labels, callouts, highlights, mixed visual/text layouts, and grounded humor.
  - `assets/anki/ppla-character-reference-v1.png`: earlier character reference; rejected for
    inconsistent repeated propeller variants.
  - `assets/anki/ppla-character-reference-v2.png`: safer layout but rejected because the hero
    propeller still read as a single visible blade.
  - `assets/anki/ppla-design-reference-v1.png`: previous mostly visual vocabulary reference; useful
    historical source but weak on typography.
  - `assets/anki/ppla-style-reference-v3.png`: previous combined reference; no longer runtime
    default because character and design are now separate references.
  - `assets/anki/ppla-style-reference.png`: earlier approved low-quality combined reference; useful
    only as historical source/reference.
  - `assets/anki/ppla-style-reference-v2.png`: rejected high-quality attempt; technically higher
    effort but stylistically too bland for the target deck.
  - `assets/anki/ppla-visual-guide-sheet.png`: human-readable UX/style guide sheet.

## Research Notes

- SuperMemo's formulation rules emphasize understanding first, simple questions, pictures as memory
  aids, personalization/examples, and atomic memories. This maps to one card family per input and
  one visual hook per generated image:
  <https://supermemo.guru/wiki/20_rules_of_knowledge_formulation>
- Multimedia-learning guidance supports removing irrelevant details, signaling important structure,
  and placing related visual/textual information close enough to reduce extraneous processing:
  <https://www.cambridge.org/core/books/cambridge-handbook-of-multimedia-learning/principles-for-reducing-extraneous-processing-in-multimedia-learning-coherence-signaling-redundancy-spatial-contiguity-and-temporal-contiguity-principles/CD5B7AE1279A9AB81F8EEBB53DBEC86E>
- Anki's native Image Occlusion docs frame occlusion as hiding parts of an image to test that hidden
  content; generated illustrative scenes should stay as visual basic cards unless the task is
  actually spatial/label hiding:
  <https://docs.ankiweb.net/editing.html#image-occlusion>
- Anki community discussion around image occlusion warns against excess context and large
  slide-like cards; the same risk applies to generated images:
  <https://forums.ankiweb.net/t/image-occlusion-and-the-problem-of-excess-context-in-flashcards/55947>
- Image-occlusion guidance is useful for stable diagrams and spatial material, but generated PPLA
  illustration cards should default to basic visual cards unless occlusion is explicitly needed:
  <https://help.remnote.com/en/articles/6511625-image-occlusion-cards>
- Flashcard design guidance reinforces minimum information and visuals as dual-coding support:
  <https://examtex.com/blog/flashcard-study-tips>
