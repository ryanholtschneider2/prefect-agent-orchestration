# Plan: prefect-orchestration-8gc — `po attach` (kubectl-exec wrapping)

## Affected files

**New (core)**
- `prefect_orchestration/attach.py` — pure-logic module:
  - constants for the new bead-metadata keys (`po.k8s_pod`, `po.k8s_namespace`, `po.k8s_context`)
  - `session_name(issue, role)` — single source of truth for the tmux session name (mirrors `TmuxClaudeBackend._session_name`: dots → underscores, `po-{issue}-{role}` prefix)
  - `AttachTarget` dataclass `(mode: Literal["k8s","local"], pod, namespace, context, session)`
  - `resolve_attach_target(metadata, role) -> AttachTarget` — picks k8s vs local from bead metadata
  - `discover_roles(metadata) -> list[str]` — derive available roles from `session_<role>` keys (reuse `sessions.SESSION_PREFIX`)
  - `build_kubectl_argv(target, *, tty=True) -> list[str]` — returns the `kubectl [--context X] -n NS exec -it POD -- tmux attach -t SESSION` argv
  - `build_local_argv(target) -> list[str]` — `["tmux", "attach", "-t", session]`
  - `probe_pod(target) -> PodStatus` — runs `kubectl get pod -o json`, returns `present | not_found | terminating | unreachable`, with stderr captured for the error message
  - `stamp_runtime_location(store)` — reads `POD_NAME`/`POD_NAMESPACE`/`PO_KUBE_CONTEXT` env, calls `store.set("po.k8s_pod", …)` etc. when present (and clears any stale keys when not in k8s). Pack flows call this once at flow entry alongside the existing `po.rig_path`/`po.run_dir` stamping.

- `tests/test_attach.py` — unit tests covering: argv construction (k8s + local), session-name parity with `TmuxClaudeBackend`, role discovery, `resolve_attach_target` precedence, `probe_pod` parsing of `kubectl` output (mocked subprocess), `stamp_runtime_location` env-var matrix, ambiguous-role handling.

**Edits**
- `prefect_orchestration/cli.py`
  - Import `attach as _attach`.
  - New `@app.command()` `attach(issue_id, role: str|None, list_: bool, dry_run: bool, kube_context: str|None)`:
    - resolves run_dir via `_run_lookup.resolve_run_dir`
    - loads `metadata.json` via `_sessions.load_metadata`
    - reads bead metadata (via `BeadsStore.all()` for the k8s_* keys, since those are stamped on the bead, not the file metadata.json — keeps parity with `po.rig_path`/`po.run_dir` lookup)
    - if `--list`, prints role/pod/session table and exits 0
    - resolves role: explicit `--role` > exactly-one-session shortcut > interactive prompt when stdin is TTY > error with hint when not (`--list` recommended)
    - probes pod when `mode=="k8s"`; on stale pod prints `pod gone, run was on <pod-name>` and `try: po retry <issue-id>`, exits 4
    - on `--dry-run` prints the resolved argv and exits 0
    - otherwise `os.execvp(argv[0], argv)` so tmux gets a real TTY (matches the `os.execvp` pattern already used by `po logs -f`)
- `prefect_orchestration/sessions.py`
  - Extend `SessionRow` with optional `pod: str | None = None`.
  - `build_rows` now takes optional `bead_metadata: dict[str, str] | None`; when provided and contains `po.k8s_pod`, populates `pod` on each row (pods are per-run, not per-role today, so all rows share the value — leaving the field per-row keeps the door open for per-role pods later).
  - `render_table` adds a `POD` column when at least one row has a non-empty `pod`; otherwise unchanged (back-compat for tests).
- `prefect_orchestration/cli.py::sessions` — pass `BeadsStore(issue_id).all()` (best-effort; swallow `bd` errors, fall back to None) so the new column shows up automatically.
- `tests/test_sessions.py` — add a case asserting the `POD` column appears when k8s metadata is present and is omitted otherwise.
- `tests/test_cli_artifacts.py` (or a new `tests/test_cli_attach.py`) — invoke `po attach --dry-run` against a fixture bead/run_dir, asserting argv and exit codes for: k8s happy path, local fallback, ambiguous role (non-TTY → error), `--list` table, stale pod.
- `engdocs/attach.md` — new doc covering: bead metadata stamping (downward API env vars + `PO_KUBE_CONTEXT`), CLI usage, RBAC requirement (`pods/exec`), troubleshooting (stale pod, missing context).
- `engdocs/work-pools.md` — add a one-liner cross-reference + the env-var snippet for the k8s deployment manifest.
- `k8s/po-worker-deployment.yaml` — add `POD_NAME` / `POD_NAMESPACE` downward-API env vars and a placeholder `PO_KUBE_CONTEXT` env (commented; operator fills in) so the worker stamps these onto beads it processes.
- `CLAUDE.md` — short blurb under the "Debugging a run" section pointing at `po attach`.

**Out of scope (explicit)**
- `software-dev-full` / `epic` flows in `../software-dev/po-formulas/` need to call `attach.stamp_runtime_location(store)` next to the existing `po.rig_path`/`po.run_dir` stamping. That edit lives in the pack repo, not here. The new core helper is API-stable so the pack-side change is a one-liner; we'll note it in `lessons-learned.md` and open a follow-up bead in the pack repo if one isn't already filed.

## Approach

1. **Surface area first**: keep the CLI thin (typer command in `cli.py`), put all decision logic + subprocess wrappers in `attach.py` so it's pure-test-friendly. Mirror the structure of `run_lookup.py` / `sessions.py`.
2. **Session-name parity**: `TmuxClaudeBackend._session_name` already sanitises dots → underscores. The new helper `attach.session_name(issue, role)` is the single shared definition; refactor `TmuxClaudeBackend` to call it (small, low-risk).
3. **Bead-metadata stamping**: pack-side flows call a new core helper `stamp_runtime_location(store)` that reads `POD_NAME`/`POD_NAMESPACE`/`PO_KUBE_CONTEXT` env vars and writes the three `po.k8s_*` keys (or skips when unset → host run). Free-form metadata, no schema migration. Triage's option (a) — operator-supplied `PO_KUBE_CONTEXT` — is what we ship; (b)/(c) are noted as future work in `engdocs/attach.md`.
4. **Process replacement**: `os.execvp` for the actual attach so signals/TTY flow naturally; `--dry-run` returns the argv as a string for tests/scripting.
5. **Stale-pod detection**: `kubectl get pod -o json --context X -n NS POD` before the exec; parse `.status.phase` and the not-found stderr. Map to a clean error with a `po retry` hint.
6. **Role disambiguation**: derive roles from `metadata.json` `session_<role>` keys. If `--role` omitted: exactly-one ⇒ pick; >1 + TTY ⇒ prompt with numbered list; >1 + non-TTY ⇒ error suggesting `--list` and `--role`.
7. **`po sessions` POD column**: opt-in based on metadata presence. Existing tests stay green because the column is suppressed when no `po.k8s_pod` is set.

## Acceptance criteria (verbatim from issue)

- `po attach <issue>` attaches to a running k8s-pod tmux session given a kubeconfig context.
- Falls back to local tmux when bead has no k8s metadata.
- `po attach <issue> --role builder` picks a specific role.
- Tests: unit (mock kubectl), e2e against a kind cluster (the cloud-smoke harness can host the e2e).
- `engdocs/work-pools.md` or new `engdocs/attach.md` documents it.

## Verification strategy

| AC | Concrete check |
|---|---|
| k8s attach works | Unit: fixture bead with `po.k8s_*` set + `metadata.json` containing one `session_builder`; `po attach <id> --dry-run` emits exactly `kubectl --context <ctx> -n <ns> exec -it <pod> -- tmux attach -t po-<safe-issue>-builder`. Manual smoke documented in `engdocs/attach.md`: against the kind cluster spun up by `scripts/smoke-compose.sh`-equivalent, real attach reaches the tmux pane (Ctrl-b d detaches cleanly). |
| Local fallback | Unit: bead has `po.rig_path`/`po.run_dir` but no `po.k8s_*`; `po attach <id> --dry-run` emits `tmux attach -t po-<safe-issue>-<role>`. |
| `--role` selection | Unit: `metadata.json` has multiple `session_<role>` keys; `po attach <id> --role builder --dry-run` picks the builder session; ambiguous + non-TTY exits 5 with a message naming the available roles. |
| Stale pod | Unit: mock `kubectl get pod` returning `NotFound`; `po attach <id>` exits 4 with `pod gone, run was on <pod-name>` and `try: po retry <id>` strings. |
| Tests (unit + e2e) | `tests/test_attach.py` (subprocess mocked), `tests/test_cli_attach.py` (Typer runner + monkeypatched `bd`/`kubectl`). e2e against kind: extend `tests/test_cloud_smoke_scripts.py` (or a new `tests/e2e/test_attach_kind.py`) gated on `KIND_CLUSTER` env; CI keeps it skipped by default since kind boot is heavy — runs locally via `scripts/cloud-smoke-*.sh`. |
| Docs | `engdocs/attach.md` exists and is linked from `engdocs/work-pools.md` and the project `CLAUDE.md`. Validate via `grep` in a doc-link sanity test. |

## Test plan

- **Unit (`tests/test_attach.py`)** — argv builders, session-name parity with `TmuxClaudeBackend`, role discovery, `resolve_attach_target`, `probe_pod` parsing, `stamp_runtime_location` env matrix.
- **CLI (`tests/test_cli_attach.py`)** — Typer `CliRunner` with monkeypatched `_run_lookup`, `BeadsStore`, and `subprocess.run` for `kubectl`. Covers happy paths, `--list`, `--dry-run`, ambiguous role, stale pod, missing metadata.
- **Sessions table (`tests/test_sessions.py`)** — POD column appears/disappears based on metadata.
- **e2e (`tests/e2e/test_attach_kind.py`, gated)** — opt-in test that creates a kind cluster, deploys the worker manifest, runs a stub formula, asserts `po attach --dry-run` resolves the pod and that the kubectl argv is consumable. Skipped unless `PO_E2E_KIND=1`.
- No playwright (CLI-only feature).

## Risks

- **Kubeconfig context discovery is operator-supplied** (`PO_KUBE_CONTEXT`). If the env var is missing, `po attach` falls back to the user's *current* kubectl context — silent footgun for multi-cluster users. Mitigation: when `po.k8s_pod` is set but `po.k8s_context` is not, print a one-line warning before exec.
- **`kubectl exec -it` TTY**: confirmed `os.execvp` (process replacement) is required; subprocess pipes break tmux's PTY handling. Tests cover argv only; live attach is documented manual smoke.
- **Pack-side dependency**: this lands the helper but doesn't actually start stamping `po.k8s_*` until the pack flows call `stamp_runtime_location`. We'll log a follow-up bead targeting `../software-dev/po-formulas/` and note the integration step in `lessons-learned.md`. The local-fallback path keeps `po attach` useful in the meantime.
- **`po sessions` table change** is back-compat (POD column suppressed when absent), but downstream parsers that split on whitespace could miscount columns. No known consumers in core; flagged in changelog.
- **RBAC**: callers without `pods/exec` see a raw 403 from kubectl. We catch the kubectl exit and surface a "your kubeconfig context lacks pods/exec on namespace <ns>" hint.
- **No API contract changes**, no migrations, no breaking consumers.
