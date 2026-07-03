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
| **ChatGPT-browser service** (Mac mini, Tailscale `http://100.107.180.35:8010`) | Anki text roles decision/scenario/render (`chatgpt-web` routable model) + Anki image generation incl. PPLA style refs (FR-1) | `CHATGPT_BROWSER_API_URL` env — REQUIRED when any role routes there; loud error if missing. Knobs: `anki_chatgpt_browser_timeout_seconds`, `anki_chatgpt_browser_force_fresh` | `subscription_notional` (rides ChatGPT Pro; `cost_known=false`) |
| **OpenAI API** | Anki text roles (decision/scenario/render defaults), quality role (vision inspection), whisper/audio, general chat workflows, optional image provider | `OPENAI_API_KEY` | metered |
| **claude -p CLI** (in the bot image) | optional text backend — set any anki role model to `claude-p` | `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`; no default, loud if missing). NOTE: shares the owner's Claude-subscription quota with coding sessions | `subscription_notional` |
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
- The quality role must stay on an API vision model while
  `anki_quality_inspect_images: true` — text-only backends are rejected at construction.

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
3. Add `CHATGPT_BROWSER_API_URL=http://100.107.180.35:8010` to the Pi's env.
4. Verify the Pi reaches the mini over Tailscale (`curl <url>/ping` from the Pi) — without
   it the first Anki call fails loudly by design.

## In-repo packages

`packages/ai_workflow_engine` (the reusable workflow engine — consumers pin tags, currently
`engine-v0.6.5`), `packages/ai_workflow_tools` (CLI agents, ChatGPT-browser clients, media
providers), `packages/ai_workflow_viewer` (observation-bundle viewer). Observation bundles:
`data/observations/<run_id>/` per run (trace/details/usage/meta/definition/artifacts).
