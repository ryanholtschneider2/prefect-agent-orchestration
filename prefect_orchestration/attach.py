"""`po attach <issue> [--role X]` — auto-discover the worker pod hosting an
issue's tmux agent session(s), then `os.execvp` into either:

  - `kubectl --context <ctx> -n <ns> exec -it <pod> -- tmux attach -t <session>`
    when the bead carries `po.k8s_pod` metadata, or
  - `tmux attach -t <session>` directly when the run was on the host.

Pure logic lives here; the Typer command in `cli.py` wires it up + handles
the actual `os.execvp` handoff so this module stays trivially testable.

Also exposes `stamp_runtime_location(store)` — a formula-agnostic helper
that reads the downward-API env (`POD_NAME`, `POD_NAMESPACE`,
`PO_KUBE_CONTEXT`) and writes `po.k8s_pod` / `po.k8s_namespace` /
`po.k8s_context` onto the supplied `MetadataStore`. No-op when `POD_NAME`
is unset (host runs leave the metadata clean).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from prefect_orchestration.beads_meta import MetadataStore
from prefect_orchestration.sessions import (
    METADATA_FILENAME,
    SESSION_PREFIX,
)

META_K8S_POD = "po.k8s_pod"
META_K8S_NAMESPACE = "po.k8s_namespace"
META_K8S_CONTEXT = "po.k8s_context"

PodStatus = Literal["running", "gone", "forbidden", "unknown"]


def session_name(issue: str, role: str) -> str:
    """The tmux session name used by `TmuxClaudeBackend` for this (issue, role).

    Single source of truth for the naming rule: dots in either component
    become underscores so tmux's `session.window.pane` target syntax doesn't
    misparse `prefect-orchestration-4ja.1` as a pane reference. Any change
    here orphans live sessions across all rigs — treat as a stable contract.
    """
    safe_issue = issue.replace(".", "_")
    safe_role = role.replace(".", "_")
    return f"po-{safe_issue}-{safe_role}"


@dataclass(frozen=True)
class K8sTarget:
    context: str | None
    namespace: str
    pod: str
    session: str


@dataclass(frozen=True)
class LocalTarget:
    session: str


AttachTarget = K8sTarget | LocalTarget


def discover_roles(run_dir: Path) -> list[str]:
    """Sorted list of roles with a recorded session uuid, from `metadata.json`."""
    meta_path = run_dir / METADATA_FILENAME
    if not meta_path.exists():
        return []
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    roles: list[str] = []
    for key in meta:
        if key.startswith(SESSION_PREFIX):
            role = key[len(SESSION_PREFIX) :]
            if role:
                roles.append(role)
    roles.sort()
    return roles


def resolve_attach_target(
    *,
    issue: str,
    role: str,
    bead_metadata: dict[str, str],
) -> AttachTarget:
    """Build a K8sTarget when `po.k8s_pod` is set, else a LocalTarget."""
    sess = session_name(issue, role)
    pod = bead_metadata.get(META_K8S_POD)
    if pod:
        namespace = bead_metadata.get(META_K8S_NAMESPACE) or "default"
        context = bead_metadata.get(META_K8S_CONTEXT) or None
        return K8sTarget(context=context, namespace=namespace, pod=pod, session=sess)
    return LocalTarget(session=sess)


def build_kubectl_argv(target: K8sTarget) -> list[str]:
    """argv for `kubectl ... exec -it <pod> -- tmux attach -t <session>`."""
    argv = ["kubectl"]
    if target.context:
        argv += ["--context", target.context]
    argv += ["-n", target.namespace, "exec", "-it", target.pod, "--"]
    argv += ["tmux", "attach", "-t", target.session]
    return argv


def build_local_argv(target: LocalTarget) -> list[str]:
    """argv for `tmux attach -t <session>`."""
    return ["tmux", "attach", "-t", target.session]


class _RunFn(Protocol):
    def __call__(
        self, argv: list[str], *, capture_output: bool, text: bool, check: bool
    ) -> subprocess.CompletedProcess: ...


def probe_pod(
    target: K8sTarget,
    *,
    runner: _RunFn | None = None,
) -> tuple[PodStatus, str]:
    """Run `kubectl get pod` and classify the outcome.

    Returns `(status, detail)` where status ∈ {running, gone, forbidden, unknown}.
    `detail` is a human-readable string suitable for the user-visible error.
    `runner` is injectable so tests can mock `subprocess.run`.
    """
    argv = ["kubectl"]
    if target.context:
        argv += ["--context", target.context]
    argv += ["-n", target.namespace, "get", "pod", target.pod, "-o", "json"]

    run = runner if runner is not None else subprocess.run
    try:
        proc = run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return "unknown", "kubectl not on PATH"

    if proc.returncode == 0:
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return "unknown", "kubectl returned non-JSON output"
        phase = (data.get("status") or {}).get("phase")
        if phase == "Running":
            return "running", phase
        return "gone", f"pod phase = {phase!r}"

    stderr = (proc.stderr or "").strip()
    low = stderr.lower()
    if "notfound" in low.replace(" ", "") or "not found" in low:
        return "gone", stderr or "pod NotFound"
    if "forbidden" in low or "cannot get" in low:
        return "forbidden", stderr or "RBAC: forbidden"
    return "unknown", stderr or "kubectl failed"


def stamp_runtime_location(
    store: MetadataStore,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Stamp `po.k8s_pod` / `po.k8s_namespace` / `po.k8s_context` from env.

    Reads from `os.environ` by default (override `env=` for tests). No-op
    when `POD_NAME` is unset — host runs intentionally leave bead metadata
    clean so `po attach` falls through to local tmux.

    Returns the dict of keys actually written, for caller logging.
    """
    e = env if env is not None else dict(os.environ)
    pod = e.get("POD_NAME")
    if not pod:
        return {}
    written: dict[str, str] = {}
    store.set(META_K8S_POD, pod)
    written[META_K8S_POD] = pod
    namespace = e.get("POD_NAMESPACE")
    if namespace:
        store.set(META_K8S_NAMESPACE, namespace)
        written[META_K8S_NAMESPACE] = namespace
    context = e.get("PO_KUBE_CONTEXT")
    if context:
        store.set(META_K8S_CONTEXT, context)
        written[META_K8S_CONTEXT] = context
    return written


def fetch_bead_metadata(issue_id: str) -> dict[str, str]:
    """Read full bead metadata via `bd show <id> --json`. Empty dict on failure.

    Used by `po attach` and `po sessions` to read k8s stamping. Failure
    modes (no bd, missing bead, JSON error) all degrade to empty dict — the
    caller treats it as "no k8s metadata, fall through to local tmux".
    """
    import shutil

    if shutil.which("bd") is None:
        return {}
    proc = subprocess.run(
        ["bd", "show", issue_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, list):
        data = data[0] if data else {}
    meta = data.get("metadata") or {}
    return {str(k): str(v) for k, v in meta.items()}
