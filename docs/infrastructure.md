# TGHandyUtils infrastructure

What runs where, which config layer wins, and every external service the bot depends on.
Verified 2026-07-02. If this drifts from reality, fix THIS file in the same change.

## Deploying

- **Your own instance (third parties):** the complete guide is [self-hosting.md](self-hosting.md).
- **The author's Pi deployment:** via the private `aws_deploy` Ansible repo — pointer and
  branch-pin gotcha in [deployment.md](deployment.md).

## Runtime topology

| Instance | Where | Bot identity | How it runs | Data |
|---|---|---|---|---|
| **Prod** | Raspberry Pi | production bot id (own token) | deployed via the `aws_deploy` repo (Ansible: `~/PycharmProjects/aws_deploy/ansible/`, inventory + playbooks there). Env is Ansible-managed. | Pi-local sqlite |
| **Dev/local** | this machine, Docker | `@delmebot_bot` (separate token in local `.env`) | `docker compose up -d bot` (root `docker-compose.yml`; repo is volume-mounted, so code changes need only a restart, not a rebuild) | `./data/` volume |

The two instances use DIFFERENT Telegram bot tokens, so they never fight over long-polling.
Watch the local bot: `docker compose logs -f bot`.

## Configuration layers (highest wins — proven, not assumed)

1. **Environment variables** — secrets + deployment knobs ONLY. Locally: `.env` (dotenv-loaded
   inside the container AND compose-interpolated; note the container only receives vars listed
   in `docker-compose.yml` `environment:`). On the Pi: Ansible-managed env. A guard test
   (`tests/unit/test_config_profile.py`) allow-lists every direct `os.getenv` in `config.py` —
   extending it is a reviewed decision. **A stale env override silently beats the yaml** (this
   bit us on 2026-07-02: `ANKI_IMAGE_PROVIDER=openai` in `.env` masked the yaml flip).
2. **`config/*.yaml`** — the LIVE non-secret config layer. `config/anki.profile.yaml` pins
   per-role models, providers, style refs, budgets. `config/workflow.yaml` = generic settings.
3. **Code defaults in `config.py`** — fallbacks only; they apply when neither layer above sets
   a value.

## External services

| Service | Used for | Config | Cost class |
|---|---|---|---|
| **ChatGPT-browser service** (Mac mini, Tailscale `http://100.107.180.35:8010`) | Anki IMAGE generation incl. PPLA style refs (FR-1); optional text via the `chatgpt-web` routable model | `CHATGPT_BROWSER_API_URL` env — REQUIRED when any role routes there; loud error if missing. Knobs: `anki_chatgpt_browser_timeout_seconds`, `anki_chatgpt_browser_force_fresh` | `subscription_notional` (rides ChatGPT Pro; typed pricing remains `unknown` when no counters/rate exist) |
| **OpenAI API** | general bot plumbing (classifier/task parsing), whisper/audio, uploaded-photo vision, optional anki backends/image provider | `OPENAI_API_KEY` | metered |
| **claude -p CLI** (in the bot image) | DEFAULT for all four anki text roles incl. quality (staged vision: the CLI reads generated card images). `codex-exec` is the sibling backend | `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`; no default, loud if missing). NOTE: shares the owner's Claude-subscription quota with coding sessions | `subscription_notional` |
| **Gemini** | optional image provider (`anki_image_provider: gemini`) | `GEMINI_API_KEY` | metered |
| **ElevenLabs** | Anki voice generation | `ELEVENLABS_API_KEY` | metered |
| **Telegram** | the bot itself | `TELEGRAM_BOT_TOKEN` (different per instance) | — |
| **Todoist / Trello / Google Calendar** | task platforms | respective tokens (optional) | — |

### ChatGPT-browser service — operational notes
- Docs + feature requests live in `~/PycharmProjects/ai-llm-infra/` (`CHATGPT_API.md`,
  `CHATGPT_API_FEATURE_REQUESTS.md`, `HANDOFF_FR1_REFERENCE_IMAGES.md`).
- Single logged-in browser ⇒ strictly sequential calls (text ~5–30 s, image ~30–90 s).
- Known issue FR-2: the FIRST interaction after idle can exceed its own timeout (504) —
  retry once; warm calls are fast. Our clients fail loudly, never auto-retry.
- Auth is coming (their review BUG 2): expect a Bearer token → new env
  `CHATGPT_BROWSER_API_TOKEN` on our side (no default; loud if required and missing).
- Backend selection is per-role via the model name: set any `anki_*` role model to
  `chatgpt-web` (browser) or an API model name (metered). Registry:
  `services/llm_factory.py` (`LLMBackend`, `register_llm_backend`).
- The quality role needs a VISION-capable backend while `anki_quality_inspect_images: true`:
  API vision models qualify, `claude-p` qualifies via staged files; genuinely text-only
  backends (`chatgpt-web` /ask) are rejected loudly at construction.

## Known deliberate deviation — front-door paths run off-engine (owner decision 2026-07-04)

The message classifier (`services/content/classifier.py:36`), task parsing
(`services/parsing_service.py:40`), whisper transcription, uploaded-photo vision, and the
Todoist/Trello/Calendar platform writes (`platforms/*.py` — no `external_write` gating)
predate the workflow engine and bypass it: no trace/bundles, no budget gates, no backend
registry routing, no side-effect policy on platform writes. This contradicts the north
star ("one engine across the gradient") and is **deliberately deferred, not endorsed**:
the engine ships to external consumers first (MageQA/GoPro) and must prove itself there.

**Revisit trigger:** engine polished + shipped + validated by other applications — or the
next front-door bug that trace-lessness makes painful, whichever comes first. Migration
shape when triggered (sliced, parity-gated): classifier → one-step engine flow with
routing-parity fixtures; task parsing → structured node + `external_write`-gated platform
capabilities with idempotency keys; whisper/vision → engine capabilities. Old paths get
DELETED on cutover (no duplicate implementations).

## Planned work (design docs, approved 2026-07-04)

- [run-durability-plan.md](run-durability-plan.md) — no silent run loss on restart (loud
  sweep first, re-dispatch + dedup second).
- [cost-ledger-plan.md](cost-ledger-plan.md) — durable per-pool usage ledger, optional
  daily caps with loud refusal, `/usage` command.

## Testing

- `./test.sh unit` — full mocked tier in Docker (NEVER bare pytest). Batch variants:
  `./test_batch.sh`, `./test_all_batches.sh`.
- `ALLOW_PAID_TESTS=1 ./test.sh integration -- <files>` — real-spend/live tier.
  Live ChatGPT-browser suite: `tests/integration/test_chatgpt_browser_live.py`
  (module-skips without `CHATGPT_BROWSER_API_URL`; service-down = failure, not skip).
  Anki eval harness (real cards + .apkg): `tests/integration/test_anki_eval.py`.
- Results land in `infra/test-results/` (junit.xml, coverage).

## Deploying the current branch to the Pi (checklist)

1. Push/merge `polish/workflow-engine` (pushing is the owner's action, never automated).
2. Deploy via `aws_deploy` Ansible as usual.
3. Add `CHATGPT_BROWSER_API_URL=http://100.107.180.35:8010` AND `CLAUDE_CODE_OAUTH_TOKEN`
   to the Pi's env (template via aws_deploy secrets; infra compose uses env_file so no
   compose change is needed).
4. Verify the Pi reaches the mini over Tailscale (`curl <url>/ping` from the Pi) — without
   it the first Anki call fails loudly by design.

## In-repo packages

`packages/ai_workflow_engine` (the reusable workflow engine — consumers pin tags, currently
`engine-v0.6.6`), `packages/ai_workflow_tools` (CLI agents, ChatGPT-browser clients, media
providers), `packages/ai_workflow_viewer` (observation-bundle viewer). Observation bundles:
`data/observations/<run_id>/` per run (trace/details/usage/meta/definition/artifacts).
