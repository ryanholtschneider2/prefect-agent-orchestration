# Plan: prefect-orchestration-8gc — `po attach` (kubectl-exec wrapping for remote tmux lurking)

## Goal
Add `po attach <issue-id> [--role <role>]` that auto-discovers the worker pod that hosts an issue's tmux agent session(s), then `os.execvp`s into either:

- `kubectl --context <ctx> -n <ns> exec -it <pod> -- tmux attach -t <session>` when the bead carries `po.k8s_pod` metadata, or
- `tmux attach -t <session>` directly when the run was on the host.

Plus: stamp the metadata at flow entry from the downward API; teach `po sessions` to show a `POD` column when k8s rows exist; surface stale-pod / RBAC errors clearly; document.

## Affected files (under `/home/ryan-24/Desktop/Code/personal/nanocorps/prefect-orchestration`)

New:
- `prefect_orchestration/attach.py` — pure module: `session_name(issue, role)`, `AttachTarget` dataclass, `resolve_attach_target(...)`, `discover_roles(metadata) -> list[str]`, `build_kubectl_argv(...)`, `build_local_argv(...)`, `probe_pod(...) -> Literal["running","gone","forbidden","unknown"]`, `stamp_runtime_location(store)` (reads `POD_NAME`/`POD_NAMESPACE`/`PO_KUBE_CONTEXT` env, no-op when unset).
- `tests/test_attach.py` — unit (argv builders, target resolution, role disambiguation, stamping no-op vs. set, session-name parity).
- `tests/test_cli_attach.py` — unit (Typer CliRunner; mocks `os.execvp` and `attach.probe_pod`).
- `tests/e2e/test_attach_kind.py` — opt-in via `PO_E2E_KIND=1` (skipped otherwise); spins/uses a kind cluster, runs the attach roundtrip non-interactively (no real TTY — assert on built argv via a `--print-argv` debug flag).
- `engdocs/attach.md` — user-facing guide, RBAC requirements, `PO_KUBE_CONTEXT` operator note.

Edited:
- `prefect_orchestration/cli.py` — register `attach` Typer command; wire `--role`, `--list`, `--print-argv` flags; `os.execvp` for handoff.
- `prefect_orchestration/sessions.py` — extend `SessionRow` with optional `pod: str | None`; `build_rows` reads `po.k8s_pod` from metadata; `render_table` only adds the POD column when ≥1 row populates it (back-compat narrow output).
- `prefect_orchestration/agent_session.py` — refactor `TmuxClaudeBackend._session_name` to delegate to `attach.session_name(issue, role)` so name-parity is enforced by a single source of truth.
- `engdocs/work-pools.md` — short xref to `attach.md` + downward-API env wiring requirement.
- `k8s/po-worker-deployment.yaml` — add `POD_NAME` / `POD_NAMESPACE` downward-API env vars; document `PO_KUBE_CONTEXT` (operator-supplied) under env.
- `CLAUDE.md` — one-line entry under "Common workflows" pointing at `po attach`.

Out of scope (per issue):
- Web-terminal (ttyd) lurking — separate bead.
- Cross-cluster federation — single kubeconfig context per call.
- Pack-side caller of `stamp_runtime_location` — landed in core; the `software-dev` pack flow-entry path will adopt it in a follow-up bead. Until then, runs without the stamp degrade gracefully to the local-tmux fallback (acceptance #2 still passes).

## Approach

1. **Metadata stamping (core helper, formula-agnostic).**
   `attach.stamp_runtime_location(store: MetadataStore) -> None` reads
   `POD_NAME`, `POD_NAMESPACE`, and `PO_KUBE_CONTEXT` from `os.environ`.
   When `POD_NAME` is set it writes `po.k8s_pod`, `po.k8s_namespace`,
   and (if present) `po.k8s_context` via `store.set(...)` alongside the
   existing `po.rig_path` / `po.run_dir` stamps in
   `prompt_formula.py` (lines ~200–205). When `POD_NAME` is unset the
   helper is a no-op — host runs leave the metadata clean and
   `po attach` falls through to local tmux.

2. **Resolution logic (`resolve_attach_target`).**
   - Look up the run via `run_lookup.resolve_run_dir(issue_id)` and the
     bead via `BeadsStore.all(issue_id)`.
   - If `po.k8s_pod` set → build a `K8sTarget(context, namespace, pod, session)`.
   - If unset → build a `LocalTarget(session)`.
   - Session name: `attach.session_name(issue, role)` =
     `f"po-{issue.replace('.', '_')}-{role.replace('.', '_')}"` (the
     same rule as `TmuxClaudeBackend._session_name`).

3. **Role disambiguation.**
   Roles come from the run_dir's `metadata.json` (per-role Claude
   session UUIDs are already stored there by the existing
   `AgentSession` flow). `discover_roles(run_dir)` returns sorted role
   names. Behavior:
   - `--role X` and X is a known role → use it (no listing).
   - `--role` omitted, exactly one role → use it.
   - `--role` omitted, multiple roles, **TTY**: print numbered list + prompt.
   - `--role` omitted, multiple roles, **non-TTY**: list to stderr, exit 2 with "specify --role". (No surprise blocking in CI.)
   - `--list` flag → print rows and exit 0 without attaching.

4. **Stale-pod / RBAC handling (`probe_pod`).**
   Before `execvp`, run `kubectl --context <ctx> -n <ns> get pod <pod>
   -o json` (subprocess.run, capture). Map exit codes / JSON status:
   - exit 0 + `.status.phase == "Running"` → proceed.
   - stderr contains `NotFound` or pod not in Running phase → exit 1
     with `pod gone, run was on <pod-name> — try 'po retry <issue-id>'`.
   - stderr contains `forbidden` (HTTP 403) → exit 1 with `RBAC: caller
     needs pods/exec in <ns>`.
   - any other error → exit 1, surface stderr verbatim.

5. **TTY handoff.**
   Use `os.execvp("kubectl", argv)` (or `os.execvp("tmux", argv)` for
   local) to replace the `po` process — both `kubectl exec -it` and
   `tmux attach` need a real TTY, and pipe-mode `subprocess.run` would
   garble tmux. This mirrors the existing `os.execvp("tail", …)` path
   in `logs --follow`.

6. **`po sessions` POD column.**
   `sessions.build_rows` reads `po.k8s_pod` from bead metadata for the
   issue (same `BeadsStore` it already uses) and stores `pod` on each
   row. `render_table` only emits the POD column header when at least
   one row has `pod is not None` (preserves current narrow output for
   pure-host users).

7. **Operator note.**
   The pod doesn't know the user's local kubeconfig context label, so
   the operator must set `PO_KUBE_CONTEXT` on the Deployment when k8s
   workers are involved. Documented in `engdocs/attach.md` and
   `engdocs/work-pools.md`. When `po.k8s_pod` is set but `po.k8s_context`
   is missing, `po attach` warns and lets `kubectl` pick the user's
   current-context (matches the issue's "single kubeconfig context per
   attach call" scope).

## Acceptance criteria (verbatim from the issue)

- `po attach <issue>` attaches to a running k8s-pod tmux session given a kubeconfig context.
- Falls back to local tmux when bead has no k8s metadata.
- `po attach <issue> --role builder` picks a specific role.
- Tests: unit (mock kubectl), e2e against a kind cluster (the cloud-smoke harness can host the e2e).
- `engdocs/work-pools.md` or new `engdocs/attach.md` documents it.

## Verification strategy

| AC | Concrete check |
|---|---|
| Attach to k8s pod | `tests/test_attach.py::test_resolve_target_k8s` builds a stamped bead fixture (k8s_pod/ns/context set) and asserts `build_kubectl_argv` produces `["kubectl", "--context", "ctx", "-n", "ns", "exec", "-it", "pod", "--", "tmux", "attach", "-t", "po-issue-builder"]`. `tests/test_cli_attach.py::test_attach_execs_kubectl` asserts `os.execvp` was called with that argv and probe_pod returns "running". |
| Local fallback | `tests/test_attach.py::test_resolve_target_local_when_no_k8s_meta` and `tests/test_cli_attach.py::test_attach_execs_local_tmux` assert local-tmux argv `["tmux", "attach", "-t", "po-issue-builder"]`. |
| `--role builder` selects a specific role | `tests/test_cli_attach.py::test_role_flag_selects` (multiple roles in metadata; `--role builder` picks builder without prompting). |
| Unit (mock kubectl) | All `tests/test_attach.py` + `tests/test_cli_attach.py` mock `subprocess.run` for the `kubectl get pod` probe and `os.execvp` for the handoff — no real cluster. |
| e2e against kind | `tests/e2e/test_attach_kind.py` (`PO_E2E_KIND=1`-gated; skipped in default `pytest`) creates a kind cluster, deploys a minimal pod with a long-running `tmux new -d -s po-demo-builder 'sleep 600'`, stamps the bead, and runs `po attach demo --print-argv` (non-interactive debug flag) to assert the right argv was built. |
| Doc | `engdocs/attach.md` exists; `engdocs/work-pools.md` xrefs it. CI grep check: `tests/e2e/test_docs.py`-style assertion (existing pattern in repo) or a small unit asserting the file is non-empty + mentions `PO_KUBE_CONTEXT`. |

## Test plan (layers)

- **unit** (`tests/test_attach.py`, `tests/test_cli_attach.py`, `tests/test_sessions.py` extension):
  - argv builders (k8s + local).
  - role disambiguation (single/multi/TTY/non-TTY/`--list`/`--role` happy + unknown).
  - `stamp_runtime_location` no-op when `POD_NAME` unset; writes 3 keys when set; omits `po.k8s_context` when `PO_KUBE_CONTEXT` unset.
  - `probe_pod` branches: running / NotFound / forbidden / generic-error (mock `subprocess.run`).
  - `sessions.render_table` POD column visibility (back-compat).
  - session-name parity: `attach.session_name == TmuxClaudeBackend._session_name` for a sample matrix.
- **playwright**: N/A (no UI).
- **e2e** (`tests/e2e/test_attach_kind.py`): one happy-path argv assertion against a kind cluster, `PO_E2E_KIND=1`-gated. Default suite skips. Default suite covers the `po` CLI roundtrip in non-k8s mode (host fallback) under `tests/e2e/test_attach_local.py` — spawns a real `tmux new -d -s po-demo-builder` and asserts `po attach demo --print-argv` returns the matching argv (no real `execvp` so the test doesn't hijack the runner's TTY).

Layer separation reminder (per CLAUDE.md "Test layers"): the local-fallback e2e only goes in `tests/e2e/` if it spawns the real `po` binary + real tmux; the in-process Typer-runner version goes in `tests/`. We pick **one** layer per assertion to keep `software_dev_full`'s parallel `unit`/`e2e` runs from double-counting.

## Risks

- **`PO_KUBE_CONTEXT` operator footgun.** A wrong context env on the Deployment makes every `po attach` from any user route through the wrong cluster. Mitigation: warn loudly when context is unset; document the mapping in `engdocs/attach.md`; `po doctor` could optionally validate the value resolves in the user's local kubeconfig (follow-up).
- **`os.execvp` requires a real TTY.** Tests must mock it; the `--print-argv` debug flag exists specifically so e2e can assert without losing control of pytest's stdout. CI should not invoke `po attach` without `--print-argv`.
- **Pack-side dependency for full AC #1.** The pack's flow-entry path needs to call `stamp_runtime_location`. Until that lands in `../software-dev/po-formulas`, AC #1 is exercisable only via test fixtures that pre-populate the metadata. The fallback path (AC #2) keeps `po attach` useful in the meantime; the pack-side change is a small follow-up bead.
- **`SessionRow` schema bump.** Adding `pod: str | None` is back-compat for in-process callers but any pickled rows in flight would lack the field — none exist (rows are rebuilt per call), so risk is nil. Worth a one-line note in the changelog.
- **RBAC errors.** Users without `pods/exec` in the namespace will see 403s. Plan surfaces the actionable message; can't fix RBAC from here.
- **Session-name churn.** Refactoring `TmuxClaudeBackend._session_name` to call `attach.session_name` must preserve byte-exact output — covered by the parity test. If we ever change the rule, all live sessions across all rigs become unreachable; treat the helper as a stable contract.
