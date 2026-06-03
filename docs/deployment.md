# Deployment (pointer)

This repo contains **no infrastructure code**. All deploy/IaC for TGHandyUtils lives in the
separate **`aws_deploy`** repository (private). This page only tells a developer of *this* repo
how their code reaches a running bot — it intentionally duplicates no host facts or secrets.

## How it gets deployed

- Deployment is an **Ansible role** named `tghandyutils` in the `aws_deploy` repo.
- It clones this repo, templates a `.env` (tokens come from `aws_deploy` secrets — never stored
  here), and runs the container via this repo's `infra/docker-compose.yml`.
- Trigger it from the `aws_deploy` repo:

  ```bash
  make deploy-tgbot-pi          # background deploy
  make deploy-tgbot-pi DEPLOY_MODE=blocking   # foreground
  ```

  (A direct `ansible-playbook` is intentionally blocked by a `DEPLOY_MODE` guard — use the make
  target.)

## ⚠️ Gotcha: the deployed branch is pinned in `aws_deploy`

The branch that actually runs is pinned in `aws_deploy` group vars (`tghandyutils_git_version`),
**not** necessarily `main`. If you merge work here, it does **not** reach the bot until that pin
points at your branch (or you merge into the pinned branch). Check `aws_deploy` before assuming a
deploy shipped your change.

## How the container is built (this repo)

- `infra/Dockerfile` — conda (`condaforge/miniforge3`) image; the env is created from
  **`environment.yml`**. **Add new dependencies to `environment.yml`** (there is no
  `requirements.txt`). After changing it, the image must be rebuilt.
- `infra/docker-compose.yml` — single `bot` service, `restart: unless-stopped`, persists
  `data/db` and `data/logs`, no exposed ports (Telegram long-polling, outbound only).
- Entry point: `python main.py`.

## Operating notes

- Logs / restart / DB-verification commands and the host facts live in the `aws_deploy` runbook
  (kept there so secrets and IPs stay out of this repo).
- The full operational runbook should live in `aws_deploy/docs/` — see that repo.
