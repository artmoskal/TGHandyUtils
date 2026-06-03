# Anki Cards Feature — Project Plan

Status: **Planning** · Owner: Artem · Branch: `feature/anki-cards` (to be created off `feature/timezone-agnostic-llm`)

## 1. Goal

Let the bot turn arbitrary content the user sends into **Anki flashcards** (question/answer),
as an alternative to the existing **reminder/task** flow. The active behaviour is selectable:

- **Settings mode** — persistent default: `reminder` | `anki` | `auto`.
- **Per-message command override** — `/tasks …` or `/anki …` forces one flow for that message.
- **`auto` mode** — an LLM classifier decides reminder-vs-card per message.

## 2. Guardrails (non-negotiable)

1. **DO NOT break current functionality.** The reminder/task path must behave identically
   after the refactor. Proven by characterization tests written *before* the refactor (Stage 1).
2. **DO NOT degrade architecture.** New behaviour is added behind a thin seam; no duplicated
   pipelines, no copy-paste of the reminder flow.
3. **DO NOT over-engineer.** No plugin registry, no provider framework beyond what these two
   modes need. The seam must stay obvious enough to add a *third* mode later in < 1 day.
4. **Deployed-branch safety.** `feature/timezone-agnostic-llm` is what the Pi deploys
   (`aws_deploy` Ansible pins it + force-pulls). **No feature code lands on it** until the
   Anki work is merged and deliberately released. See `docs/deployment.md`.
5. **Single-token rule.** Only one bot may long-poll a token. Local bot testing uses a
   **separate BotFather test token** in the gitignored local `.env`; never the prod token.

## 3. Current architecture (baseline)

Message → reminder pipeline today:

```
threading_handler.process_user_input        (groups rapid messages, ~1s window)
  └─ text_handler.process_thread_with_photos (THE chokepoint — reminder logic lives here)
       ├─ parsing_service.parse_content_to_task()   → {title, due_time, description}  (gpt-4o-mini)
       ├─ build TaskCreate
       ├─ recipient_task_service.create_task_for_recipients()  → Todoist/Trello/etc + tasks table
       └─ handle_task_creation_response()           → Telegram reply + post-task buttons
scheduler.py → later sends the reminder over Telegram
```

Key files:
- `handlers_modular/message/threading_handler.py` — threading entry.
- `handlers_modular/message/text_handler.py:16-168` — `process_thread_with_photos` (refactor target).
- `services/parsing_service.py` — LLM parsing (OpenAI gpt-4o-mini, temp 0).
- `services/recipient_task_service.py` — task creation + platform distribution.
- `core/container.py` — DI (`providers.Factory`).
- `database/migrations.py` — numbered migrations + `_verify_schema`.
- `database/user_preferences_repository.py` + `models/unified_recipient.py` — settings.
- `handlers_modular/callbacks/settings/notifications.py` — settings-toggle pattern.
- `telegram_handlers.py` — handler registration.

## 4. Target architecture

Two small additions, both behind interfaces, wired at the existing chokepoint.

### 4.1 `IContentProcessor` (the seam)

```python
class IContentProcessor(ABC):
    async def process(self, ctx: ProcessingContext) -> ServiceResult: ...
```

`ProcessingContext` carries what the chokepoint already has: assembled text, owner_name,
location, user_id, chat_id, message_id, screenshot_data, and the aiogram message (for replies).

Implementations:
- **`ReminderProcessor`** — the *current* `process_thread_with_photos` body moved verbatim.
  Behaviour-identical. This is the whole "don't break it" strategy.
- **`AnkiProcessor`** — gpt-4o-mini Q/A extraction → `genanki` `.apkg` → send as Telegram document.
  (A later `delivery` sub-strategy can add AnkiConnect; out of scope for v1.)

### 4.2 `IntentResolver`

```python
class IntentResolver:
    def resolve(self, user_id: int, command_override: Optional[Intent]) -> Intent
```

Precedence (first match wins):
1. `command_override` from `/anki` / `/tasks` (one-shot, this message only)
2. user setting `content_mode` (`reminder` | `anki` | `auto`)
3. if `auto` → gpt-4o-mini classifier → `reminder` | `anki`

`Intent = REMINDER | ANKI`.

### 4.3 Rewired chokepoint

`process_thread_with_photos` becomes thin:

```python
intent     = intent_resolver.resolve(user_id, override)
processor  = processor_for(intent)          # from DI container
result     = await processor.process(ctx)
```

Everything else — threading, parsing service, recipient service, scheduler — is unchanged.

### 4.4 Why this does NOT degrade the architecture

- The reminder flow is *moved*, not *changed* — no second copy.
- `IParsingService` stays as-is (the `ReminderProcessor` keeps using it). We are **not**
  forcing reminders and cards through one awkward shared interface.
- Adding a future mode = one new `IContentProcessor` + one enum value + one wiring entry.
- The DI container already uses `providers.Factory`; new providers follow the existing shape.

### 4.5 Discipline — flexible where it moves, silent where it doesn't

The governing rule: **a seam is justified by a second concrete case *now*; an interface is
justified by a second *implementation*.** Applied:

- **`IContentProcessor` — build it.** Two concrete intents today + stated growth. Justified.
- **NO registry / plugin discovery / dynamic loading.** Wire intents *explicitly* with a plain
  `{Intent.REMINDER: ..., Intent.ANKI: ...}` map in the container. This is the exact thing that
  causes "stuck with pluggable things"; explicit wiring stays readable and trivially extended.
- **`ProcessingContext` = a small frozen dataclass of raw inputs only** (content, user_id,
  chat_id, message_id, screenshot, message ref). No methods, no services on it — processors
  pull collaborators from DI. Prevents the context becoming a god-object.
- **Anki delivery: stay concrete.** `AnkiProcessor` depends on one `AnkiCardService` with a single
  public method. Only when AnkiConnect actually arrives do we extract a `CardDelivery` interface
  (a ~1-file change). No interface for an implementation that doesn't exist.
- **LLM: centralize construction, don't abstract behavior.** One `llm` provider in the container
  returns the configured client; injected into parsing, Q/A, and the classifier. Swapping
  model/provider later = one place. No `ILLMClient` interface yet (no second provider).

**Litmus tests this design must pass:**
- Add a 3rd intent `/note`: new enum value + `NoteProcessor` + one map line + one settings entry. Zero other files.
- Add AnkiConnect: extract `CardDelivery` from the one method, add `AnkiConnectDelivery`, switch by config. `AnkiProcessor` untouched.
- Swap to Claude: change the one `llm` provider.

## 5. Stages, task split & acceptance criteria

Each stage is independently shippable and testable via `./test.sh`. Stop after any stage.

### Stage 0 — genanki spike (no bot, no Pi, no token)
**Purpose:** validate card *quality* (gpt-4o-mini Q/A) and `.apkg` import friction before committing.
- [ ] Add `genanki` to `environment.yml` (the `pip:` list — this repo builds via conda
      `environment.yml` + `infra/Dockerfile`; there is **no** `requirements.txt`); rebuild image.
- [ ] `services/anki_card_service.py` — `genanki` deck/note builder (pure, no Anki running).
- [ ] Deterministic test (existing suite): fixed Q/A fixture → assert valid `.apkg` (zip loads, N notes). Permanent regression net.
- [ ] One parametrized eval tool (reads samples from a yaml/txt) → real gpt-4o-mini Q/A → writes `.apkg`(s) for manual import. Run via `./test.sh`/docker.
- **Acceptance:** user imports a generated `.apkg` on desktop + phone; Q/A quality judged acceptable. Decision gate: genanki-only vs add AnkiConnect later.

### Stage 1 — `IContentProcessor` seam (behaviour-preserving refactor)
- [ ] Characterization tests capturing current reminder behaviour **first**.
- [ ] Add `IContentProcessor` + `ProcessingContext` to `core/interfaces.py`.
- [ ] `ReminderProcessor` = current `process_thread_with_photos` body, verbatim.
- [ ] `AnkiProcessor` wrapping Stage 0's service.
- [ ] Register both in `core/container.py`; thin out the chokepoint.
- **Acceptance:** all pre-existing tests + new characterization tests green; reminder flow byte-for-byte same outputs; `AnkiProcessor` reachable via a temporary hardcode for smoke test.

### Stage 2 — settings mode
- [ ] Migration `011_add_content_mode` (`content_mode TEXT DEFAULT 'reminder'`); update `_verify_schema`.
- [ ] Add field to `UnifiedUserPreferences` / `UnifiedUserPreferencesUpdate` + repository read/write.
- [ ] Settings UI selector (mirror `notifications.py`): Reminder / Anki / Auto.
- [ ] `IntentResolver` reads the setting (no `auto` logic yet — treat `auto` as `reminder` until Stage 4).
- **Acceptance:** toggling mode in settings persists and routes messages to the right processor; default stays `reminder` for existing users (migration default).

### Stage 3 — command overrides
- [ ] `/anki <text>` and `/tasks <text>` handlers in `handlers_modular/commands/`; register in `telegram_handlers.py`.
- [ ] One-shot override passed into `IntentResolver` (wins over setting). Command **with content** = process that content now; no hidden sticky state.
- **Acceptance:** `/anki <text>` always makes a card, `/tasks <text>` always makes a reminder, regardless of the saved mode; normal (commandless) messages still follow the setting.

### Stage 4 — `auto` mode + 5s pre-commit window (DECIDED)
- [ ] gpt-4o-mini classifier → `reminder` | `anki`.
- [ ] On auto-resolution: reply with the guess + override buttons `[📝 Reminder] [🃏 Card]`,
      **create nothing for 5s**, then commit the guess if untouched. A button tap within the
      window cancels the pending commit and commits the chosen intent instead. Because nothing
      is created until commit, there are **no inconsistent side effects** (addresses the earlier
      race concern); the 5s window is long enough for a human to actually override.
- **Acceptance:** auto mode routes correctly on clear cases; the guess message shows; tapping
  a button within 5s produces the chosen type and no duplicate; ignoring it commits the guess once.

### Parallel — deployment doc
- [ ] `docs/deployment.md` — thin, secret-free pointer to `aws_deploy`. (Done alongside this plan.)

## 6. Decisions locked
- Q/A model: **OpenAI gpt-4o-mini** (reuse existing key/stack).
- v1 Anki delivery: **genanki `.apkg`** (Pi-friendly, no running Anki). AnkiConnect-on-Mac-mini is a *possible* later delivery strategy behind the same seam, not v1.
- Race handling in auto mode: **ignored**, 1s delay only.
- Todoist test writes: acceptable; cleaned up manually.

## 7. Open questions
- Stage 4 delayed-transaction UX (see above).
- Anki deck/model identity: single fixed deck ("Telegram") vs per-user/per-topic decks; note model/GUID stability for re-imports.
- Should cards persist server-side (an `anki_cards` table) or be fire-and-forget `.apkg`? v1 leans fire-and-forget.
