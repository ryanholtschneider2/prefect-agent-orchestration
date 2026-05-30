# cloud-rclaude

**What it provides:** `po env` driver that runs PO formulas on a remote
machine via the rclaude stack. Registers `--driver rclaude` with three backends:

- `ssh` (default) — a machine you ALREADY own and have registered with
  rclaude (`~/.config/rclaude/hosts.toml`) or can reach as `user@addr`.
  No provisioning; the flow runs as your connecting user in `$HOME`.
- `digitalocean` — provision a fresh DO droplet (`root`/`coder`/`/home/coder`).
- `daytona` — a Daytona sandbox (SDK-native, no SSH/cloudflared, no public IP).
  All remote ops run via `process.exec`; fast create-from-snapshot + cheap
  suspend/resume. Needs `DAYTONA_API_KEY` and `pip install rclaude[daytona]`.
  `build_image --backend daytona` bakes the reusable base snapshot once;
  `attach` uses Daytona's SSH gateway. Worker is provisioned with
  `auto_stop=0` (idle suspend off) so the central server can always dispatch.

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

# Daytona sandbox (bake the base image once, then provisions are seconds):
po env build-image --driver rclaude --backend daytona     # one-time base snapshot
po env up --driver rclaude --name sb --backend daytona
po env sync-packs sb          # deliver local-only packs (exec-tar) for a real po run
po attach <issue-id>          # tmux in over Daytona's ssh gateway
po env stop sb / po env start sb   # suspend / resume (keep disk, ~1s resume)
po env down sb                # deletes the sandbox
```

Daytona notes: local-only packs (not on PyPI) reach the sandbox via
`po env sync-packs` (tar over `process.exec`, not rsync — no public IP), same
`uv tool install --editable` as the ssh path. For a worker to reach a **private**
Prefect (Tailscale/LAN) `PREFECT_API_URL`, set a `TS_AUTHKEY` secret
(`rclaude secrets set TS_AUTHKEY=...`); `start_worker` joins the tailnet in
userspace mode and proxies the worker's HTTP through it.

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

**Secrets (env vars the run/agent needs):** owned by **rclaude** (the box
layer), so they work in `rclaude talk`/`exec` AND `po run --env`:
```bash
rclaude secrets set GITHUB_TOKEN=ghp_xxx --host laptop   # host-scoped
rclaude secrets set OPENAI_API_KEY=sk-xxx                # global (all hosts)
rclaude secrets import ./.env --host laptop              # bulk
rclaude secrets list                                     # keys only
```
Stored AES-256-GCM at `~/.config/rclaude/secrets.enc`. On launch (`rclaude
talk`/`exec` or `po env up`) the merged secrets (global + host) are written to
the box's tmpfs (`/dev/shm/rclaude/secrets.env`, 0600, RAM-only) and sourced,
so the agent + PO worker inherit them. Nothing on the remote disk; `po env
down` scrubs it. **Updating secrets → re-run `po env up <name>`** (or just
`rclaude talk` again) so the box re-sources.

The PO rclaude driver delegates to `rclaude.secrets`; PO holds no store of its
own. (Non-rclaude PO env drivers don't get these secrets.)

**Key paths:** `po_formulas_cloud_rclaude/driver.py` (delegates),
`rclaude/secrets.py` (the store + delivery).

**Skip if:** running locally, or using a non-rclaude driver (e.g. Modal).

**Read more:** `po show env-up`, `engdocs/cloud-envs.md`
