# cloud-rclaude

**What it provides:** `po env` driver that runs PO formulas on a remote
machine via the rclaude stack. Registers `--driver rclaude` with two backends:

- `ssh` (default) — a machine you ALREADY own and have registered with
  rclaude (`~/.config/rclaude/hosts.toml`) or can reach as `user@addr`.
  No provisioning; the flow runs as your connecting user in `$HOME`.
- `digitalocean` — provision a fresh DO droplet (`root`/`coder`/`/home/coder`).

**When to use:**
- Run `software-dev-full` / `epic` on your laptop / home server / colo box
  from another machine, keeping the central Prefect UI as source of truth.
- Need OAuth-authenticated Claude Code on a fresh droplet without an API key.

**Key verbs:**
```bash
# Own host (the rclaude alias doubles as the env name):
po env up --driver rclaude --name laptop --backend ssh
po run software-dev-full --env laptop --issue-id <id> --rig <r> --rig-path <remote-path>
po attach <issue-id>          # tmux into the remote agent
po env down laptop            # stops the worker; does NOT destroy your host

# Fresh DO droplet:
po env up --driver rclaude --name big --backend digitalocean
```

**How the worker reaches Prefect (ssh backend):** the remote worker's
`PREFECT_API_URL` resolves, first match wins:
1. `api_url` in the env's opaque (reserved for a future `--api-url` flag),
2. `PO_REMOTE_API_URL` in the dispatcher's env,
3. derived from this machine's Tailscale IP → `http://<ts-ip>:4200/api`.

**Prerequisites for a real run on an ssh host (NOT auto-handled):**
- The central Prefect server must bind to the tailnet
  (`PREFECT_SERVER_API_HOST=0.0.0.0 prefect server start`) and be reachable
  from the remote at the resolved URL.
- The remote must have `po` + the SAME formula packs importable. `start_worker`
  best-effort `uv tool install`s `prefect-orchestration` + `po-formulas-software-dev`
  from PyPI; **editable/local-only packs must be synced to the remote
  separately** (rsync the pack source + `po packs install --editable`).
- `--rig-path` must point at a path that already EXISTS on the remote (your
  other dev machine has the repo checked out). The ssh backend does no
  git-push / checkout — `ensure_rig_remote` returns `""` (tar/no-transport).

**Secrets (env vars the run/agent needs):** store them encrypted on the
dispatcher, never on the remote disk or in git:
```bash
po secrets set GITHUB_TOKEN=ghp_xxx --env laptop   # env-scoped
po secrets set OPENAI_API_KEY=sk-xxx               # global (all envs)
po secrets import ./.env --env laptop              # bulk
po secrets list                                    # keys only
```
At `po env up`, the merged secrets (global + env-scoped) are injected into the
remote's tmpfs (`/dev/shm/po/secrets.env`, 0600, RAM-only) and sourced by the
worker, so the flow + agent inherit them. Nothing is written to the remote
disk; `po env down` scrubs the file. **Updating secrets requires re-running
`po env up <name>`** (the worker re-sources at start). Stored AES-256-GCM at
`~/.config/po/secrets.enc`.

**Key paths:** `po_formulas_cloud_rclaude/driver.py`,
`prefect_orchestration/secrets_store.py`

**Skip if:** running locally, or using a Daytona/Modal driver.

**Read more:** `po show env-up`, `engdocs/cloud-envs.md`
