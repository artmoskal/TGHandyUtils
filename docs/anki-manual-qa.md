# Anki Manual QA Checklist

Use this checklist before enabling generated visual cards broadly.

## Text And Cloze Cards

- Import the generated `.apkg` into Anki.
- Confirm basic cards have a self-contained front and concise back.
- Confirm basic questions do not reveal the answer.
- Confirm cloze cards contain valid `{{cN::...}}` deletions.
- Confirm cloze deletions hide meaningful facts, terms, values, or relationships.
- Confirm `[i ...]` instructions do not appear in card text.
- Confirm the card preserves the source language.
- Confirm generated card count follows the request or the conservative default.

## Visual Cards

- Confirm the Telegram result caption says where images landed in Anki, such as front/back/both.
- Confirm generated visual cards send a separate image preview before or with the `.apkg` delivery.
- Confirm fallback cards visibly say fallback was used instead of silently looking like a requested
  visual succeeded.
- Confirm generated image media appears in Anki after import.
- Confirm the image placement matches the scenario layout.
- Confirm the written answer remains enough if the image is imperfect.
- Confirm the image answers "why this answer?" visually instead of only restating the topic term.
- Confirm the image does not introduce unsupported factual detail.
- Confirm the recurring aircraft character matches the current character reference.
- Confirm the aircraft propeller is plausible and consistent: no missing propeller, single visible
  blade, random three-blade variant, broken blade, or off-center propeller.
- Confirm the image follows the current deck design reference: bright aviation training-poster
  finish, stable palette, clean silhouettes, and phone-readable whitespace.
- Confirm short labels/callouts, when present, follow the typography reference and do not contain
  legal wording, full answers, paragraphs, or dense checklist text.
- For abbreviation/acronym cards, confirm the card answer includes the supported meaning or
  operational effect, and the image is not abbreviation-only decoration.
- Confirm humor is grounded in the tested fact and does not add false aviation/legal meaning.
- For flows, exchanges, forces, contrasts, or cause/effect, confirm the visual relationship is clear.
- Confirm the image contains no unwanted labels, text artifacts, UI, or decorative clutter.
- Confirm the package contains only the intended media files.
- Confirm generated media metadata records the current style version and reference image count.

## Language Voice Cards

- Send a short prompt such as `[i langvoice ukr->pt gen] привіт` and confirm exactly one visual card
  is produced.
- Confirm the front asks for the Portuguese translation/meaning and includes the source phrase.
- Confirm the back starts with natural Portuguese answer text.
- Confirm the `.apkg` imports with playable `[sound:...]` audio on the back side.
- Confirm Telegram sends an audio preview when the generated file exists.
- Confirm the generated image is a mnemonic for the phrase meaning, not a dense bilingual table.
- Confirm voice usage appears as a `tool` row with character count and that only one voice call is
  made unless `WORKFLOW_MAX_VOICE_CALLS_PER_RUN` is explicitly raised.

## Fallback And Cost Guards

- Force image generation failure with a fake provider and confirm text fallback.
- Set `ANKI_MAX_IMAGE_GENERATIONS_PER_RUN=0` and confirm no image provider call occurs.
- Set `WORKFLOW_MAX_VOICE_CALLS_PER_RUN=0` and confirm `langvoice` does not call ElevenLabs and
  reports voice fallback while still delivering the text/image card.
- Force a plan-level quality rejection, such as a card that samples one item from a larger sibling
  set, and confirm the graph retries card-set planning with evaluator criticism.
- Set `ANKI_MAX_QUALITY_REPAIRS_PER_RUN=0` and confirm quality rejection routes to fallback.
- Confirm fallback cards do not trigger a second LLM quality pass unless `ANKI_EVALUATE_FALLBACK_CARDS=true`.
- Confirm full-deck export captions mention included media file count.

## Manual Artifact Review

- Review preserved artifacts under `infra/test-results/anki-manual-review/`.
- Inspect `card_summary.json` for source, card text, media metadata, fallback state, and package path.
- Open generated images directly and check they match the study goal.
- Import the preserved `.apkg` and verify the same media/card behavior inside Anki.
