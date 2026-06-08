# Workflow Engine — Config & Secrets Architecture (codex hand-off)

Status: **implemented in TGHandyUtils + engine package, with one explicit transition decision open**
(Artem-directed 2026-06-08)
Author: Claude (hand-off; non-conflicting new doc)
Parents: `docs/workflow-engine-architecture.md`, `docs/workflow-engine-implementation-plan.md`
Scope: how the engine + products are configured. **Supersedes plan task P0.1** (env rename).

## 1. Problem (historical, fixed 2026-06-08)
- Before this change, `config.py` had **77 `os.getenv` calls**. Only ~7 were real **secrets**; ~70
  were **non-secret configuration** (model names, image/voice providers + sizes/formats/timeouts,
  feature flags, per-run caps, style refs, scheduler interval, thread timeout).
- There was **no config file** — configuration was 100% environment variables.
- Plan task **P0.1 (rename `ANKI_*`→`WORKFLOW_*`) would have entrenched this anti-pattern**: it
  renames env vars instead of moving non-secret config to a file. **Do not do the mass rename.**
- Current state: `config/workflow.yaml` + `config/anki.profile.yaml` are the non-secret source of
  truth; `packages/ai_workflow_engine/ai_workflow_engine/config_loader.py` loads them into
  `WorkflowProfile`/`ModelProfile` with env overrides; `config.py` now reads only secrets plus
  deployment/runtime knobs directly from env.

## 2. Decision
1. **Env = secrets + deployment/runtime overrides only.** Nothing else lives in env as a source of
   truth.
2. **Non-secret configuration is declarative config files (YAML)**, modeled as `WorkflowProfile` /
   `ModelProfile` / engine config — loaded by the engine.
3. **Precedence (3 layers):** code defaults  <  config file  <  **env override**. Env override is
   **kept** for explicit deployment/runtime overrides. The clean engine-facing prefix is
   `WORKFLOW_*`.
4. **Secrets never appear in a committed config file** (guarded by a test).

Open transition decision: TGHandyUtils currently accepts product-local `ANKI_*` env overrides during
deployment migration. That is a compatibility bridge, not part of the reusable engine target. Per
the repo architecture instruction, it must be either explicitly approved with a sunset/removal point
or removed before the engine is declared externally extractable.

## 3. What goes where

**Secrets → env / secret store (required by the app/feature path that uses them; fail loudly when an
active required secret is missing):**
`OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`TELEGRAM_BOT_TOKEN`, `TODOIST_API_TOKEN`.

Current TGHandyUtils `Config.validate()` globally requires `TELEGRAM_BOT_TOKEN` and `OPENAI_API_KEY`.
Provider-specific secrets such as `GEMINI_API_KEY`, `ELEVENLABS_API_KEY`, and Google/Todoist tokens
are capability/feature secrets: they must be present before that provider/tool path runs, but they
are not required for every unit-safe Anki/config load.

**Deployment / runtime selection → env (override layer):**
`DATABASE_PATH`, `LOG_LEVEL`/`LOG_FILE`/`LOG_FORMAT`, `AI_WORKFLOW_PROMPT_ROOT`, an `APP_ENV`/profile
selector (e.g. `WORKFLOW_PROFILE=anki.prod`), `OPENAI_PROMPT_CACHE_*` (ops cache hints), and any
single-value override of a config key.

**Non-secret workflow config → YAML profile (source of truth):**
all the `ANKI_*`/`WORKFLOW_*` model names (`*_DECISION_MODEL`, `*_RENDER_MODEL`, `*_QUALITY_MODEL`,
`*_SCENARIO_MODEL`, `*_CARD_MODEL`, `*_DEFAULT_MODEL`, supervisor/image/voice models), image config
(provider, sizes, quality, formats, timeouts, comparison providers), voice config (provider, voice id,
output format, timeout), feature flags (`*_IMAGE_GENERATION_ENABLED`, `*_AUTO_IMAGE_GENERATION_ENABLED`,
`*_QUALITY_EVALUATION_ENABLED`, `*_VOICE_GENERATION_ENABLED`, `*_QUALITY_INSPECT_IMAGES`,
`*_EVALUATE_FALLBACK_CARDS`, `*_GEMINI_RESPONSE_FORMAT_ENABLED`), per-run caps/budgets
(`*_MAX_IMAGE_GENERATIONS_PER_RUN`, `*_MAX_QUALITY_REPAIRS_PER_RUN`, `WORKFLOW_MAX_{TEXT,IMAGE,VOICE}_CALLS_PER_RUN`,
USD caps), style references + version, `SCHEDULER_INTERVAL`, `THREAD_TIMEOUT`, `DEFAULT_TASK_PLATFORM`.

## 4. Implemented shape
```
config/
  workflow.yaml          # engine defaults + model-profile registry + budgets
  anki.profile.yaml      # the Anki WorkflowProfile (models, providers, caps, flags, style refs)
.env                     # secrets ONLY (+ optional overrides)
```
- Loader order: code defaults → load YAML into `WorkflowProfile`/`ModelProfile` → apply env overrides.
- The engine has `WorkflowProfile` + `RuntimePlanCompiler`; config loading now returns a
  `WorkflowConfigBundle` with `profile`, `models`, `settings`, and loader warnings.
- `[i ...]` directives remain a **per-message** profile overlay on top of the base Anki profile
  (they are runtime input, not deployment config) — keep that distinction explicit.

## 5. Deployment implication (coordinate with `aws_deploy`)
- Commit `config/*.yaml` (non-secret) in this repo. The Pi/Ansible then templates **only the
  secrets `.env`** — removing ~70 `ANKI_*` env lines from the Ansible template.
- **Transition safety:** because env-override is kept, the existing Pi `.env` can keep working during
  migration if Artem accepts the product-local `ANKI_*` bridge. That bridge must remain outside the
  engine package and have an explicit removal/approval decision; `aws_deploy` should eventually drop
  the non-secret env lines and ship `config/*.yaml`. Record this as a follow-up in the `aws_deploy`
  repo; do not silently rename env keys the Pi `.env` still sets.

## 6. Implemented P0.1 result
- T1 done: `config/workflow.yaml` + `config/anki.profile.yaml` define engine/workflow defaults,
  Anki model profiles, media settings, voice settings, usage caps, and style references.
- T2 done: `WorkflowConfigLoader` implements defaults → YAML → env override and returns
  `WorkflowConfigBundle`.
- T3 done: `config.py` keeps direct env reads for secrets and deployment/runtime knobs only:
  Telegram/OpenAI/Gemini/ElevenLabs/Google credentials, database path, log settings, and
  OpenAI prompt-cache hints.
- T4 done: `env.sample` is secrets-first and documents a small override pattern instead of listing
  dozens of non-secret defaults.
- T5 done: focused proof is
  `./test.sh unit -- tests/unit/test_config_profile.py tests/unit/test_workflow_config_loader.py tests/unit/test_workflow_engine.py tests/unit/test_container_wiring.py tests/unit/test_anki_generation_graph.py --cov-fail-under=0`
  -> `112 passed`.

## 7. Acceptance criteria (config work)
- **CFG-1 secrets/deployment env:** only secret/deployment keys are direct env reads; every non-secret key
  has a file/default. Test: app constructs config with **no non-secret env set**.
- **CFG-2 file is source of truth:** Anki/app config loads from
  `config/*.yaml` + defaults, no non-secret env.
- **CFG-3 env override works:** setting `WORKFLOW_<KEY>` overrides the file value. Product-specific
  aliases such as `ANKI_<KEY>` are transition bridges only, not engine contract. Test asserts
  override precedence while the bridge exists.
- **CFG-4 missing active secret fails loud:** absent globally required token
  (`TELEGRAM_BOT_TOKEN`/`OPENAI_API_KEY` today) or absent provider token for a selected
  provider/tool path → explicit error, never a default.
- **CFG-5 no secret in files:** guard/test rejects committed config data containing a secret-like
  key/value pattern.
- **CFG-6 profile compiles:** `config/anki.profile.yaml` loads into `WorkflowProfile` and compiles to
  a `RuntimePlan`; an unknown key emits a warning, not a crash.
- **CFG-7 Anki regression:** `./test.sh unit -- --cov-fail-under=0` green; deployment follow-up for
  `aws_deploy` recorded.
- **CFG-8 transition bridge explicitness:** any product alias bridge (`ANKI_*` today) is documented
  as temporary or explicitly user-approved; no alias may leak into `packages/ai_workflow_engine`.

## 8. Implementation assumptions
- Keep **env-override-on-top-of-file**. Strict file-only for non-secrets is not the default.
- Drop the `ANKI_*`→`WORKFLOW_*` mass rename. Move non-secret values to files; keep `WORKFLOW_*` as
  the generic override-env prefix. `ANKI_*` aliases are a product migration bridge with an open
  approval/sunset decision, not a settled library feature.
