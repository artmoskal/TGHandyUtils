# Self-hosting TGHandyUtils (complete operator guide)

Everything needed to run your own instance from a fresh clone — no prior knowledge of this
repo assumed. (Author's own deployment lives elsewhere: [deployment.md](deployment.md) is
the pointer to that private Ansible setup; [infrastructure.md](infrastructure.md) is the
author-ops reference. You need neither to self-host.)

## 0. What you're deploying

A Telegram bot (long-polling — no public URL/webhook needed) that turns chat messages into
tasks (Todoist/Trello), reminders, and AI-generated Anki flashcards (text, cloze, visual
cards with generated images, optional voice). AI work runs through a pluggable backend
registry: OpenAI API by default, optionally `claude -p` / `codex exec` CLIs or self-hosted
services, selectable **per role** via config.

## 1. Prerequisites

- Docker with compose v2 (`docker compose version` works).
- A Telegram bot token: talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy the
  token. **If you'll also develop, make two bots** (dev + prod) — two processes polling one
  token fight each other (Telegram returns 409s and messages get split between them).
- An OpenAI API key with billing enabled.

## 2. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

| Key | Required? | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **yes** | from BotFather |
| `OPENAI_API_KEY` | **yes** | text roles (pure-API mode), quality/vision, whisper, openai images |
| **PURE-API MODE block** | **yes — uncomment it** | the committed `config/anki.profile.yaml` routes Anki roles to the author's private backends; these env overrides put everything on the OpenAI API. Env always beats yaml. |
| `CLAUDE_CODE_OAUTH_TOKEN` | optional | only for the `claude-p` backend (§5) |
| `GEMINI_API_KEY` | optional | only for `ANKI_IMAGE_PROVIDER=gemini` |
| `ELEVENLABS_API_KEY` | optional | voice on language cards; leave empty to skip voice |
| `TODOIST_API_TOKEN` / `TRELLO_*` / `GOOGLE_*` | optional | task-platform integrations |

Config precedence (highest wins): **env vars → `config/*.yaml` → code defaults.** Secrets
go ONLY in `.env` (never in yaml — a guard test enforces this). Non-secret behavior knobs
live in `config/anki.profile.yaml` (per-role models, image provider, budgets, style refs).

## 3. Run

```bash
docker compose up -d --build bot     # first build takes several minutes (conda env)
docker compose logs -f bot           # wait for: Run polling for bot @yourbot
```

Verify: message your bot `/start`, then send `/anki` plus a few sentences of any factual
text → you should get an `.apkg` file back with a usage footer like
`billed (API): $0.0021` (that's real money; `subscription … plan value` appears only if
you enable subscription backends).

Update after a `git pull`: code is volume-mounted → `docker compose restart bot` is
enough. Rebuild (`up -d --build`) only when `environment.yml` or `infra/Dockerfile` changed.

Data lives in `./data/` (sqlite DB, logs, observation bundles under `data/observations/`
— per-run traces you can inspect when something behaves oddly).

## 4. Tests

Always through the wrapper (correct env lives in Docker):

```bash
./test.sh unit                       # full mocked tier, no spend
ALLOW_PAID_TESTS=1 ./test.sh integration -- tests/integration/test_anki_eval.py -s
                                     # real-API card quality eval (writes .apkg files)
```

## 5. Optional backend: `claude -p` (Claude subscription, non-interactive)

Runs Anki text roles on a Claude subscription instead of metered API. The CLI is already
in the Docker image; you only provide auth:

1. On any machine where you're logged into Claude: `claude setup-token`.
2. **Complete the browser approval it opens** — the printed token is not active until you
   do (symptom otherwise: every call fails `401 Invalid authentication`).
3. Copy the token itself — the long `sk-ant-oat01-…` string, not the decorative box line
   around it (yes, that's a lesson from production).
4. Put it in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`, then `docker compose up -d bot`
   (recreate so the env lands).
5. Route any role to it — in `.env`: `ANKI_RENDER_MODEL=claude-p` (likewise decision/
   scenario/quality), or edit the yaml. The quality role may use it too: generated card
   images are staged as files the CLI actually reads (real vision).

Cost: reported as `subscription: ~$X plan value, no extra charge` — it consumes your
Claude plan quota, not money. Calls are slower than API (~5–15s CLI startup each).

## 6. Optional backend: `codex exec` (non-interactive)

Same idea with OpenAI's codex CLI (also pre-installed in the image). Route with
`ANKI_<ROLE>_MODEL=codex-exec`. Auth: either a ChatGPT-plan login (`codex login` state
made available to the container) or `OPENAI_API_KEY`. **Cost-ledger caveat:** the ledger
labels codex calls as subscription plan value; if you authenticate codex with an API key,
the spend is actually metered — keep plan auth or read the numbers accordingly.

## 7. Optional: images via a ChatGPT-browser service

`ANKI_IMAGE_PROVIDER=chatgpt` + `CHATGPT_BROWSER_API_URL` point image generation (with
reference-image support) at a self-hosted service that drives a logged-in ChatGPT browser
session. You must run your own instance — the author's is on a private network. Unless
you already have this, use `openai` (default in pure-API mode) or `gemini`.

## 7a. Style references — make visual cards look like YOURS, not the author's

`config/anki.profile.yaml` ships `anki_style_character_reference_image` /
`anki_style_design_reference_image` pointing at the author's `assets/anki/ppla-*.png`
(an aviation-training visual identity). With those set, your generated visual cards adopt
that look. To use your own style (or none): drop your reference PNGs under `assets/`,
repoint `anki_style_*` in the profile, or blank them to generate unstyled images.

## 8. Troubleshooting (failures are loud by design)

| Symptom | Meaning |
|---|---|
| `... is required to route a 'chatgpt-web' model` / `CHATGPT_BROWSER_API_URL is required` | a role/provider points at a backend you didn't configure — apply PURE-API MODE or set the env var |
| `401 Invalid authentication` from claude | setup-token browser approval not completed, or token truncated — redo §5 steps 1–3 |
| `console CLI binary not found` | you routed a role to `claude-p`/`codex-exec` outside the provided image — the CLIs live in the container |
| Telegram 409 conflicts in logs | two processes polling one bot token — stop the other one or use a second bot |
| First call after long idle fails once, retry works | upstream cold-start on subscription backends; the flow fails loudly rather than hanging |
| `billed (API): ?` | metered calls happened but the provider reported no price — genuinely unknown, never silently $0 |
