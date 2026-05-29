"""RClaudeEnvDriver — run PO formulas on a remote machine via rclaude.

Two backends:

- ``ssh``  — a machine you ALREADY own and have registered with rclaude
  (``~/.config/rclaude/hosts.toml``) or can reach as ``user@addr``. No
  provisioning; the flow runs as your connecting user in ``$HOME``. This
  is the daily-driver path for "run this on my laptop / home server".
- ``digitalocean`` — provision a fresh DO droplet (the original cloud-VM
  path: ``root`` login, a ``coder`` user, ``/home/coder``).

The driver bootstraps a Prefect worker on the remote pointed at a central
Prefect server (so the central UI stays the source of truth) and lets the
existing ``po run --env <name>`` machinery handle pool/deployment/dispatch.

PREFECT_API_URL resolution for the remote worker (first match wins):
  1. ``opts["api_url"]`` passed at ``po env up`` time (stored in opaque)
  2. ``PO_REMOTE_API_URL`` in the dispatcher's environment
  3. derived from this machine's Tailscale IP: ``http://<ts-ip>:4200/api``
"""

from __future__ import annotations

import base64
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from prefect_orchestration.env_drivers import EnvHandle, EnvHealth

try:
    from rclaude.backends.digitalocean_backend import DigitalOceanBackend
    from rclaude.backends.ssh_backend import _resolve_host
    from rclaude.devenv import DevEnvLauncher

    _RCLAUDE = True
except ImportError:
    _RCLAUDE = False


def _require_rclaude() -> None:
    if not _RCLAUDE:
        raise RuntimeError(
            "rclaude is not installed. "
            "Install it: uv pip install --ignore-requires-python <path-to-rclaude>"
        )


def _tailscale_ip() -> str | None:
    """This machine's Tailscale IPv4, or None if tailscale isn't up."""
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    ip = out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""
    return ip or None


def _central_api_url(stored: str = "") -> str:
    """Resolve the PREFECT_API_URL the remote worker should point at."""
    if stored:
        return stored
    env = os.environ.get("PO_REMOTE_API_URL", "").strip()
    if env:
        return env
    ip = _tailscale_ip()
    if ip:
        return f"http://{ip}:4200/api"
    return ""


@dataclass
class RClaudeEnvDriver:
    name: str = "rclaude"

    # ── connection helpers ───────────────────────────────────────────────────

    @staticmethod
    def _is_ssh(op: Mapping[str, Any]) -> bool:
        return op.get("backend") == "ssh"

    def _ssh_opts(self, op: Mapping[str, Any]) -> list[str]:
        """SSH args INCLUDING the target as the last element."""
        if self._is_ssh(op):
            key = op.get("ssh_key", "")
            opts = [
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=30",
                "-o", "BatchMode=yes",
            ]
            if key:
                opts += ["-i", key]
            return opts + [op["ssh_target"]]
        # cloud VM: root@ip
        key = op.get("ssh_key", "")
        opts = [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=30",
            "-o", "BatchMode=yes",
        ]
        if key:
            opts += ["-i", key]
        return opts + [f"root@{op['ip']}"]

    def _scp_target(self, op: Mapping[str, Any], remote_path: str) -> str:
        target = op["ssh_target"] if self._is_ssh(op) else f"root@{op['ip']}"
        return f"{target}:{remote_path}"

    def _ssh(self, op: Mapping[str, Any], script: str, timeout: int = 600) -> str:
        r = subprocess.run(
            ["ssh", *self._ssh_opts(op), "bash -s"],
            input=script.encode(),
            capture_output=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            raise RuntimeError(f"SSH failed: {r.stderr.decode()[-400:]}")
        return r.stdout.decode()

    # ── provisioning ─────────────────────────────────────────────────────────

    def provision(
        self,
        name: str,
        snapshot_tag: str,
        opts: Mapping[str, Any] | None = None,
    ) -> EnvHandle:
        _require_rclaude()
        opts = dict(opts or {})
        backend_name = opts.get("backend", "ssh")

        if backend_name == "ssh":
            # Own host: resolve a registered rclaude host (alias) or user@addr.
            # `host` opt overrides; otherwise the env name doubles as the alias.
            host_arg = opts.get("host") or name
            spec = _resolve_host(host_arg)
            if not Path(spec.key_path).exists():
                raise RuntimeError(
                    f"SSH key not found at {spec.key_path} for host {host_arg!r}. "
                    f"Register it in ~/.config/rclaude/hosts.toml or pass user@addr."
                )
            return EnvHandle(
                driver_name="rclaude",
                opaque={
                    "backend": "ssh",
                    "host": host_arg,
                    "ssh_target": spec.ssh_target,
                    "ssh_user": spec.user,
                    "ssh_addr": spec.address,
                    "ssh_key": spec.key_path,
                    "api_url": opts.get("api_url", ""),
                },
            )

        if backend_name == "digitalocean":
            backend = DigitalOceanBackend()
            launcher = DevEnvLauncher(backend)
            info = launcher.launch(name=name)
            ssh_key = (
                str(backend.ssh_key_path_for())
                if hasattr(backend, "ssh_key_path_for")
                else ""
            )
            return EnvHandle(
                driver_name="rclaude",
                opaque={
                    "backend": "digitalocean",
                    "droplet_id": info.droplet_id,
                    "ip": info.ip,
                    "ssh_key": ssh_key,
                    "api_url": opts.get("api_url", ""),
                },
            )

        raise ValueError(
            f"Unknown rclaude backend: {backend_name!r} (supported: ssh, digitalocean)"
        )

    def teardown(self, handle: EnvHandle) -> None:
        op = handle.opaque
        if self._is_ssh(op):
            # Own host: never destroy it. Just stop the rclaude-managed worker.
            try:
                self._ssh(
                    op,
                    "tmux kill-session -t po-worker 2>/dev/null || true; echo ok",
                    timeout=30,
                )
            except Exception:  # noqa: BLE001
                pass
            return
        _require_rclaude()
        backend = DigitalOceanBackend()
        backend.destroy_vm(op["droplet_id"])

    def attach_argv(self, handle: EnvHandle, role: str, issue_safe: str) -> list[str]:
        op = handle.opaque
        ssh_prefix = ["ssh", "-t", *self._ssh_opts(op)[:-1]]
        target = op["ssh_target"] if self._is_ssh(op) else f"root@{op['ip']}"

        if issue_safe and role:
            inner = f"tmux attach -t po-{issue_safe}-{role}"
        elif issue_safe:
            inner = (
                f"tmux attach -t $(tmux ls | grep po-{issue_safe} "
                f"| head -1 | cut -d: -f1)"
            )
        else:
            inner = "tmux attach"

        if self._is_ssh(op):
            return ssh_prefix + [target, inner]
        return ssh_prefix + [target, f"su - coder -c {shlex.quote(inner)}"]

    # ── identity / credentials ────────────────────────────────────────────────

    def push_identity(
        self, handle: EnvHandle, tarball_path: Path, identity_hash: str
    ) -> None:
        op = handle.opaque
        ssh_opts_no_target = self._ssh_opts(op)[:-1]
        subprocess.run(
            [
                "scp",
                *ssh_opts_no_target,
                str(tarball_path),
                self._scp_target(op, "/tmp/claude-identity.tar.gz"),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        if self._is_ssh(op):
            self._ssh(
                op,
                """
mkdir -p "$HOME/.claude"
tar -xzf /tmp/claude-identity.tar.gz -C "$HOME" 2>/dev/null || true
rm -f /tmp/claude-identity.tar.gz
""",
            )
            return
        self._ssh(
            op,
            """
mkdir -p /home/coder/.claude
tar -xzf /tmp/claude-identity.tar.gz -C /home/coder 2>/dev/null || true
chown -R coder:coder /home/coder/.claude
rm -f /tmp/claude-identity.tar.gz
""",
        )

    def push_credentials(
        self,
        handle: EnvHandle,
        env_dict: Mapping[str, str],
        oauth_creds_bytes: bytes | None,
    ) -> None:
        op = handle.opaque
        is_ssh = self._is_ssh(op)
        if env_dict:
            lines = "\n".join(
                f"export {k}={shlex.quote(v)}" for k, v in env_dict.items() if v
            )
            b64 = base64.b64encode(lines.encode()).decode()
            if is_ssh:
                self._ssh(
                    op,
                    f"""
echo '{b64}' | base64 -d > "$HOME/.po-env"
chmod 644 "$HOME/.po-env"
grep -q 'source $HOME/.po-env' "$HOME/.bashrc" 2>/dev/null || \
  echo 'source $HOME/.po-env 2>/dev/null' >> "$HOME/.bashrc"
""",
                )
            else:
                self._ssh(
                    op,
                    f"""
echo '{b64}' | base64 -d > /etc/po-env
chmod 644 /etc/po-env
grep -q 'source /etc/po-env' /home/coder/.bashrc 2>/dev/null || \
  echo 'source /etc/po-env 2>/dev/null' >> /home/coder/.bashrc
""",
                )
        if oauth_creds_bytes is not None:
            b64 = base64.b64encode(oauth_creds_bytes).decode()
            if is_ssh:
                self._ssh(
                    op,
                    f"""
mkdir -p "$HOME/.claude"
echo '{b64}' | base64 -d > "$HOME/.claude/.credentials.json"
chmod 600 "$HOME/.claude/.credentials.json"
""",
                )
            else:
                self._ssh(
                    op,
                    f"""
mkdir -p /home/coder/.claude
echo '{b64}' | base64 -d > /home/coder/.claude/.credentials.json
chmod 600 /home/coder/.claude/.credentials.json
chown coder:coder /home/coder/.claude/.credentials.json
""",
                )

    def ensure_rig_remote(self, handle: EnvHandle) -> str:
        op = handle.opaque
        if self._is_ssh(op):
            # Own host: no git push. The operator points --rig-path at a path
            # that already exists on the remote (your other dev machine has
            # the repo checked out). Tar/no-transport fallback.
            return ""
        self._ssh(
            op,
            """
if [ ! -d /home/coder/rig-remote.git ]; then
  git init --bare /home/coder/rig-remote.git
  chown -R coder:coder /home/coder/rig-remote.git
fi
""",
        )
        return f"ssh://root@{op['ip']}/home/coder/rig-remote.git"

    # ── worker bootstrap ───────────────────────────────────────────────────────

    def start_worker(self, handle: EnvHandle, pool_name: str) -> None:
        op = handle.opaque
        escaped_pool = shlex.quote(pool_name)
        api_url = _central_api_url(op.get("api_url", ""))
        api_export = (
            f'export PREFECT_API_URL={shlex.quote(api_url)}; ' if api_url else ""
        )
        if not api_url:
            print(
                "[rclaude-driver] WARNING: could not resolve a central "
                "PREFECT_API_URL (no opts.api_url, no PO_REMOTE_API_URL, no "
                "Tailscale IP). The remote worker will use its own local "
                "Prefect default and likely never see dispatched runs.",
            )

        # PREFECT_API_URL exported in the bootstrap shell so the tmux server
        # (started from it) inherits it; the worker subprocess inherits in turn.
        api_line = (
            f"export PREFECT_API_URL={shlex.quote(api_url)}" if api_url else ":"
        )

        if self._is_ssh(op):
            # Own host, connecting user, $HOME. Best-effort prereq install;
            # editable/local packs arrive via `po env sync-packs <name>`.
            # `prefect` lives INSIDE the po tool venv (uv tool install only
            # exposes the `po` shim), so we add the tool bin dir to PATH.
            self._ssh(
                op,
                rf"""
set -uo pipefail
command -v tmux >/dev/null 2>&1 || (sudo apt-get install -y tmux 2>/dev/null || true)
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null || true
fi
export PATH="$HOME/.local/bin:$PATH"
# Only PyPI-install if po is absent — a bare `uv tool install` would EVICT
# the editable packs placed by `po env sync-packs`.
command -v po >/dev/null 2>&1 || uv tool install prefect-orchestration 2>/dev/null || true
TOOLBIN="$(uv tool dir 2>/dev/null)/prefect-orchestration/bin"
export PATH="$TOOLBIN:$PATH"
{api_line}
if ! command -v prefect >/dev/null 2>&1; then
  echo "ERROR: prefect not in po tool env — run 'po env sync-packs <name>' first" >&2
  exit 1
fi
WORKER_SESSION=po-worker
if tmux has-session -t "$WORKER_SESSION" 2>/dev/null; then
  echo "worker tmux session already running"
  exit 0
fi
tmux new-session -d -s "$WORKER_SESSION" \
  "exec prefect worker start --pool {escaped_pool} --type process"
sleep 4
if tmux has-session -t "$WORKER_SESSION" 2>/dev/null; then
  echo "started worker on pool {pool_name}"
else
  echo "ERROR: worker session died on startup (check prefect/pool/api-url)" >&2
  exit 1
fi
""",
                timeout=300,
            )
            return

        # cloud VM (root + coder user)
        self._ssh(
            op,
            rf"""
set -euo pipefail
apt-get install -y tmux 2>/dev/null || true
if ! command -v uv &>/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | HOME=/home/coder sh
fi
su - coder -c "
  export PATH=/home/coder/.local/bin:\$PATH
  command -v po >/dev/null 2>&1 || uv tool install prefect-orchestration 2>/dev/null || true
"
su - coder -c "
  export PATH=/home/coder/.local/bin:\$PATH
  TOOLBIN=\$(uv tool dir 2>/dev/null)/prefect-orchestration/bin
  export PATH=\$TOOLBIN:\$PATH
  {api_line}
  tmux has-session -t po-worker 2>/dev/null && exit 0
  tmux new-session -d -s po-worker 'exec prefect worker start --pool {escaped_pool} --type process'
"
""",
            timeout=300,
        )

    def health(self, handle: EnvHandle) -> EnvHealth:
        op = handle.opaque
        target = op.get("ssh_target") if self._is_ssh(op) else op.get("ip")
        try:
            self._ssh(op, "echo ok", timeout=15)
            return EnvHealth(ok=True, summary=f"SSH reachable ({target})")
        except Exception as exc:  # noqa: BLE001
            return EnvHealth(ok=False, summary=f"SSH failed: {exc}")

    def build_image(self, opts: Mapping[str, Any] | None = None) -> None:
        pass  # VM/own-host driver: no image to bake

    # ── pack sync (editable/local packs the auto-installer can't reach) ───────

    def _rsync_up(self, op: Mapping[str, Any], local: str, remote_rel: str) -> None:
        ssh_cmd = "ssh " + " ".join(self._ssh_opts(op)[:-1])
        target = op["ssh_target"] if self._is_ssh(op) else f"root@{op['ip']}"
        # rsync won't create intermediate parents; ensure the dest dir exists.
        self._ssh(op, f'mkdir -p {shlex.quote(remote_rel)}', timeout=30)
        subprocess.run(
            [
                "rsync", "-az", "--delete", "-e", ssh_cmd,
                "--exclude", ".git", "--exclude", ".venv",
                "--exclude", "__pycache__", "--exclude", "node_modules",
                "--exclude", "*.egg-info", "--exclude", ".pytest_cache",
                f"{local.rstrip('/')}/",
                f"{target}:{remote_rel}/",
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )

    def _local_editable_packs(self) -> list[tuple[str, str]]:
        """[(dist_name, local_path)] for every editable pack + rclaude itself.

        prefect-orchestration (the tool primary) is returned first so callers
        can install it with `--editable` and the rest as `--with-editable`.
        """
        from prefect_orchestration import packs as _packs

        out: list[tuple[str, str]] = []
        for p in _packs.discover_packs():
            if p.source == "editable" and p.source_detail:
                out.append((p.name, p.source_detail))
        # rclaude is a driver dependency, not a po pack — add it from PEP 610.
        try:
            import json
            from importlib.metadata import distribution

            raw = distribution("rclaude").read_text("direct_url.json") or "{}"
            data = json.loads(raw)
            if data.get("dir_info", {}).get("editable") and data.get(
                "url", ""
            ).startswith("file://"):
                out.append(("rclaude", data["url"][len("file://") :]))
        except Exception:  # noqa: BLE001
            pass
        # primary first
        out.sort(key=lambda t: 0 if t[0] == "prefect-orchestration" else 1)
        return out

    def sync_packs(self, handle: EnvHandle) -> None:
        """Mirror local editable packs to the remote and reinstall the po tool.

        Bridges the gap the auto-installer can't: packs installed editable /
        local on the dispatcher (not on PyPI) are rsynced to
        ``~/.po-packs/<name>/`` on the remote, then the remote ``po`` tool env
        is rebuilt with one ``uv tool install`` so every formula is importable
        by the remote worker.
        """
        op = handle.opaque
        packs = self._local_editable_packs()
        if not packs:
            print("[rclaude-driver] no editable packs to sync")
            return

        # Preserve each pack's path RELATIVE to a common base so inter-pack
        # `[tool.uv.sources]` relatives (e.g. `../../prefect-orchestration`)
        # still resolve on the remote under ~/.po-packs/<relpath>.
        base = os.path.commonpath([lp for _, lp in packs])
        rel = {name: os.path.relpath(lp, base) for name, lp in packs}

        for name, local in packs:
            self._rsync_up(op, local, f".po-packs/{rel[name]}")
            print(f"[rclaude-driver] synced {name} -> .po-packs/{rel[name]}")

        primary = next((p for p in packs if p[0] == "prefect-orchestration"), None)
        extras = [p for p in packs if p[0] != "prefect-orchestration"]
        if primary is None:
            print(
                "[rclaude-driver] WARNING: prefect-orchestration not editable "
                "locally; skipping remote reinstall (synced sources only)."
            )
            return

        argv = (
            f'uv tool install --reinstall '
            f'--editable "$HOME/.po-packs/{rel["prefect-orchestration"]}"'
        )
        for name, _ in extras:
            argv += f' --with-editable "$HOME/.po-packs/{rel[name]}"'

        self._ssh(
            op,
            rf"""
set -uo pipefail
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null || true
fi
export PATH="$HOME/.local/bin:$PATH"
if ! {argv}; then
  echo "[remote] ERROR: uv tool install failed" >&2
  exit 1
fi
echo "[remote] po tool rebuilt with {len(packs)} editable pack(s)"
""",
            timeout=600,
        )
        print(f"[rclaude-driver] reinstalled remote po tool with {len(packs)} pack(s)")

    def fs_download(
        self, handle: EnvHandle, remote_path: str, local_path: Path
    ) -> None:
        op = handle.opaque
        ssh_cmd = "ssh " + " ".join(self._ssh_opts(op)[:-1])

        if self._is_ssh(op):
            # Own host: --rig-path points at the REMOTE tree, so core's
            # `local_path` (rig_path/.planning/<formula>/<issue>) is actually
            # the run-dir's ABSOLUTE path ON THE REMOTE. That path generally
            # doesn't exist on the dispatcher, so mirror it into a local cache
            # instead of trying to write the remote path locally.
            remote_src = str(local_path)
            dest = Path.home() / ".cache" / "po" / "env-runs" / local_path.name
            dest.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "rsync", "-az", "-e", ssh_cmd,
                    f"{op['ssh_target']}:{remote_src}/",
                    str(dest) + "/",
                ],
                capture_output=True,
                timeout=300,
            )
            print(f"[rclaude-driver] run artifacts mirrored to {dest}")
            return

        local_path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "rsync", "-az", "-e", ssh_cmd,
                f"coder@{op['ip']}:{remote_path}/",
                str(local_path) + "/",
            ],
            capture_output=True,
            timeout=300,
        )
