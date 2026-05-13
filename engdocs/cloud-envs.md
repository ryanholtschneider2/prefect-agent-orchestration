# Cloud envs — `po env up` + `--env <name>` for remote execution

Daily-driver verbs for running PO formulas against a remote compute env
without leaving the CLI. Builds on the existing k8s + cloud-smoke
substrate (`charts/po`, `k8s/po-worker-deployment.yaml`,
`scripts/cloud-smoke/`) but exposes it as a single ergonomic surface:

```bash
po env up --driver daytona --name big       # provision once
po run software-dev-fast --env big ...      # dispatch many runs to it
po env down big                             # clean up
```

Plus an ephemeral mode for one-shot sandboxing:

```bash
po run software-dev-fast --env up --driver daytona ...   # provision → run → teardown
```

> Lurking the remote agent: `po attach <issue-id>` already routes to
> k8s pods via `kubectl exec`. Daytona driver extends the same logic
> to `daytona ssh <sandbox> -- tmux attach -t po-<issue>-<role>` so
> `--env` envs are first-class citizens of `po attach`.

## Why this exists

We have all the cloud infra pieces — Helm chart, worker image,
job-template — but no daily verb that ties them together. Today an
operator who wants to run a formula on a real cloud box has to
`scripts/cloud-smoke/provision-hetzner.sh` then `helm install` then
`prefect deployment run` with the right pool name. Three commands, all
of which embed copy-pastable secrets. `po env` collapses that to one
verb plus a `--env` flag on every existing `po run`.

This is also the missing piece for using PO from a laptop without a
big CPU/RAM budget — long `software-dev-full` epics are happier on a
CCX33 than on a thinkpad.

## Principles applied

- **§1 (thin CLI over Prefect).** `--env <name>` is sugar over
  `work_pool_name=po-env-<name>` on the existing `<formula>-manual`
  deployment — not a new substrate. The PO-specific value is the
  per-env metadata (`~/.config/po/envs/<name>.toml`), the snapshot
  registry, and the identity-bundle sync. Plain `prefect deployment run
  --pool ...` still works.
- **§3 (packs, not core).** Driver implementations ship as separate
  packs (`po-cloud-daytona`, future `po-cloud-modal`, …) registered
  via a new `po.env_drivers` entry-point group. Core ships only the
  `po env` verbs, the env-metadata store, and the `--env` flag plumbing.
- **§4 (utility ops are direct callables).** `po env list / image list
  / image gc` are `po.commands`-style utility ops; only `po run --env`
  goes through Prefect.

## Three trees, three sync mechanisms

A cloud env needs three different things from the operator's machine.
Decision matrix:

| Tree | What it is | Today's local source | Sync mechanism |
|---|---|---|---|
| **Rig** | Repo PO operates on (code + `.beads/` + `.planning/`) | `<rig_path>/` | `git push` to a Daytona-side bare remote at every `po run --env <name>`. Bare remote is created at `po env up` time. Fallback `--rig-transport=tar` for rigs without a git remote (this rig). |
| **User-scoped agent files** | Curated `~/.claude/` slice | `~/.claude/{settings.json,CLAUDE.md,.mcp.json,commands/,prompts/,skills/,memory/}` | Tar + `sandbox.fs.upload()` to `/home/coder/.claude/`. Re-upload only when local hash diverges from `envs/<name>.toml`. Excludes `projects/`, `todos/`, `statsig/`, caches, and `.credentials.json` unless `--with-auth`. |
| **Project-scoped agent files** | `<rig>/.claude/` + `<rig>/CLAUDE.md` + `<rig>/.claude/packs/` overlays | covered by rig tree | comes free with rig sync |
| **PO core + packs** | `prefect-orchestration` + every installed `po-formulas-*` / `po.commands` / `po.doctor_checks` / `po.deployments` pack | `~/.local/share/uv/tools/` (or `pip show` paths) | **Baked into a Daytona snapshot at `po env image build` time.** Snapshot tag is content-addressed by installed-pack hash; rebuilds are no-op when local pack set hasn't changed. |
| **Credentials** | `ANTHROPIC_API_KEY` *or* OAuth `~/.claude/.credentials.json`; `GITHUB_TOKEN` for git push | shell env + `~/.claude/.credentials.json` | Per-sandbox env via Daytona's secret API. Never baked into the snapshot. OAuth creds are file-mode only — uploaded as a separate `sandbox.fs.upload(/home/coder/.claude/.credentials.json, mode=0600)` when `--with-auth`. |

## Snapshot baking

`po env image build` is the load-bearing piece. Steps:

1. Walk `importlib.metadata.entry_points()` for `po.formulas`,
   `po.commands`, `po.doctor_checks`, `po.deployments`,
   `po.env_drivers`. Record `(distribution_name, version)` per pack.
2. Compute snapshot tag = `sha256(core_version + sorted pack tuples)[:12]`.
   Same local state ⇒ same tag ⇒ cache hit.
3. Build via `Dockerfile.pack`:
   - For each pack: if it's a registry-published version, pin via
     `--build-arg PACK_SPEC=<name>==<ver>`.
   - For each editable-installed pack: `COPY` the source tree into the
     image and `RUN po packs install --editable /pack/<name>`. Editable
     local packs still bake reproducibly; the image just embeds the
     copied source rather than installing from PyPI.
4. Push to Daytona's snapshot registry (or to whatever container
   registry Daytona is configured to pull from in self-hosted mode).
5. Persist tag → pack-set fingerprint mapping in
   `~/.config/po/snapshots.toml` so `po env doctor` can detect drift
   when `po packs install <new-pack>` runs locally without a rebuild.

`po env image build` is its own verb so:

- CI can pre-warm snapshots out of band on every pack version bump.
- `po env up` invokes it as a no-op-when-fresh dependency.
- `po env up --rebuild` and `po run --env <name> --rebuild` force a
  refresh without provisioning new envs.
- `po env image gc [--keep N]` prunes old snapshots once you've moved
  on (Daytona snapshot storage isn't free).

## `po env up --driver daytona` — flow

1. `po env image build` (no-op if snapshot fresh; emits the snapshot
   tag for step 2).
2. `Daytona().create(snapshot=<tag>, name=<workspace-name>)` via
   the Python SDK. Persistent workspace if `--name` given; ephemeral
   sandbox per-run if `--name` omitted (the `--env up` path).
3. Bootstrap a bare git remote inside the sandbox at `/srv/rig.git`
   (one-shot `process.exec("git init --bare /srv/rig.git")`).
   Skipped when `--rig-transport=tar`.
4. `git remote add po-env-<name> ssh://<sandbox-ssh>/srv/rig.git`
   locally + initial `git push po-env-<name> HEAD`.
5. `sandbox.fs.upload()` the curated `~/.claude/` tarball.
   `--with-auth` opts in to `.credentials.json`.
6. `sandbox.env.set()` the credential bundle (`ANTHROPIC_API_KEY` or
   `CLAUDE_CREDENTIALS`-mode OAuth, `GITHUB_TOKEN`, `PO_BACKEND=tmux`).
7. Start a Prefect worker on the sandbox under supervisord:
   `prefect worker start --pool po-env-<name>`. Pool is created
   client-side via `prefect work-pool create po-env-<name> --type
   process` first.
8. Persist `~/.config/po/envs/<name>.toml`:
   ```toml
   driver = "daytona"
   snapshot_tag = "<hash>"
   pool = "po-env-<name>"
   sandbox_id = "<daytona-id>"
   sandbox_ssh = "<host:port>"
   rig_remote = "ssh://.../srv/rig.git"
   identity_hash = "<sha256-of-claude-bundle>"
   created_at = "<utc>"
   ```

## `po run --env <name>` — flow

1. Read `envs/<name>.toml`. Error if not registered.
2. `git push po-env-<name> HEAD` (incremental — no rig re-upload).
3. Re-upload identity bundle iff local `~/.claude/` slice hash differs
   from `envs/<name>.toml`.
4. Schedule the existing `<formula>-manual` deployment with
   `work_pool_name=po-env-<name>` override and `--start-in 0`. The
   worker on the sandbox picks it up.
5. Stream flow state (Prefect events) to local stdout — same as
   `po watch` does today.
6. **At flow exit**, mirror `<rig>/.planning/<formula>/<issue>/` back
   to local via `sandbox.fs.download()`. This makes
   `po logs / artifacts / sessions / trace` work against the local
   filesystem without keeping the sandbox alive.

## Lurking the remote agent — `po attach` over Daytona

PO's tmux backend names sessions `po-<issue-id>-<role>` (with `.` →
`_` sanitization). On the daytona driver, `po attach` extends today's
k8s logic with a third resolution branch:

1. `bd show` → `po.env_name` metadata is set (stamped at flow entry
   when `--env <name>` was passed).
2. `envs/<name>.toml` → `sandbox_id` + `sandbox_ssh`.
3. `os.execvp("daytona", ["ssh", "<sandbox-id>", "-t",
   f"tmux attach -t po-{issue_safe}-{role}"])` — full PTY handoff,
   colors, keyboard, scrollback all work.
4. Falls back to `os.execvp("ssh", [...])` against the persisted
   `sandbox_ssh` if `daytona` CLI isn't on PATH.

This is exactly the rclaude `talk` pattern (rclaude does
`ssh -t … 'su - coder -c "claude"'`); we're just running `tmux attach`
instead of starting a fresh Claude session.

## Doctor checks

`po doctor --check=envs` walks every registered env in
`~/.config/po/envs/*.toml`:

| Check | Status |
|---|---|
| Snapshot tag matches local pack-set hash | red on mismatch — hint `po env image build` then `po run --env <name> --rebuild` |
| Identity bundle hash matches local `~/.claude/` | yellow on mismatch — hint runs will resync at next dispatch |
| Sandbox reachable (Daytona API + ssh probe) | red on failure |
| Worker process healthy (Prefect API: pool has live worker) | red on failure |
| `git push --dry-run po-env-<name>` succeeds | red on failure |

Red row exits 1 — drives CI gates and pre-flight checks.

## Cost guard

VMs forgotten = bills accrued. Two safeguards:

- `po env list --idle [--threshold 1h]` shows envs with no recent
  flow runs.
- `po env reap [--idle-since 24h]` tears down anything matching, with
  a confirmation prompt unless `-y`.
- `--env up --auto-down 30m` on the ephemeral path schedules teardown
  at flow exit + 30m grace.

## Writing a driver

Cloud-env packs implement the `EnvDriver` Protocol defined in
`prefect_orchestration/env_drivers.py`. Quick start:

1. New pack: `po-cloud-<provider>`.
2. Implement the 8 Protocol methods (`provision`, `teardown`,
   `attach_argv`, `push_identity`, `push_credentials`,
   `ensure_rig_remote`, `start_worker`, `health`) on a class.
3. Register the class via:

   ```toml
   [project.entry-points."po.env_drivers"]
   <provider> = "po_cloud_<provider>:<ClassName>"
   ```

4. `po packs install --editable <path>` then `po packs update`.
5. Confirm with `po doctor` (`env drivers registered` row lists the
   new driver) and `po packs list` (`env_drivers=<provider>` column).

### `EnvHandle` rules

- Frozen dataclass. Drivers MUST NOT mutate it in place — return a
  new handle when state changes. (`Mapping[str, Any]` is a typing hint,
  not a runtime guarantee; the underlying dict remains mutable
  post-construction — discipline, not enforcement.)
- `opaque` is a JSON-serializable mapping. No `Path`, no `bytes`, no
  custom classes. Stash an SDK client by reconstructing it from the
  ids in `opaque` each call. `__post_init__` validates the shape at
  construction time and raises `TypeError` on a non-JSON value.
- 9ws.4 (`envs.toml` store) round-trips `(driver_name, opaque)` via
  TOML; design `opaque` so that round-trip preserves what the driver
  needs to look up the sandbox.

### `push_credentials` — bytes vs path

The OAuth creds parameter is `bytes | None`, not `Path`. Caller reads
`~/.claude/.credentials.json` into bytes once and passes them; driver
controls the sandbox-side write path (typically
`/home/coder/.claude/.credentials.json`, mode 0600). This keeps future
drivers (Vault / 1Password fetch, mTLS bundle) clean.

### Disjoint from `secrets.py`

`prefect_orchestration/secrets.py` provides per-role secret injection
at AgentSession launch (e.g. `SLACK_TOKEN_BUILDER`). That's orthogonal
to `push_credentials`, which is per-sandbox provisioning. A pack will
typically use both: `push_credentials` bakes `ANTHROPIC_API_KEY` into
the sandbox env; `secrets.py` re-keys role-scoped secrets at turn time
inside that sandbox.

### Worked example — `NoopDriver`

See `prefect_orchestration/env_drivers.py::NoopDriver` for a minimal
in-tree implementation. It records every call in memory and returns
canned values — **copy-paste this as your driver skeleton**.
`NoopDriver` is deliberately NOT registered as a `po.env_drivers` entry
point: it's a test fixture, not a supported driver. Don't add it to
your pack's `pyproject.toml`.

## Sequencing — see beads epic `prefect-orchestration-cloud-envs`

Each chunk independently testable. Final child is the E2E gate (real
`software-dev-fast` against a real Daytona sandbox using OAuth creds
+ TmuxClaudeBackend, with `po attach` validation that the remote
tmux is lurkable from the laptop).
