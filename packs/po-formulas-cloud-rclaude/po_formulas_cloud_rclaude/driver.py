"""RClaudeEnvDriver — DigitalOcean/Hetzner VMs via rclaude.devenv."""

from __future__ import annotations

import base64
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from prefect_orchestration.env_drivers import EnvHandle, EnvHealth

try:
    from rclaude.backends.digitalocean_backend import DigitalOceanBackend
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


def _get_backend(backend_name: str) -> Any:
    if backend_name == "digitalocean":
        return DigitalOceanBackend()
    raise ValueError(f"Unknown rclaude backend: {backend_name!r}")


@dataclass
class RClaudeEnvDriver:
    name: str = "rclaude"

    def _ssh_opts(self, ip: str, key: str) -> list[str]:
        opts = [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=30",
            "-o", "BatchMode=yes",
        ]
        if key:
            opts += ["-i", key]
        return opts + [f"root@{ip}"]

    def _ssh(self, ip: str, key: str, script: str, timeout: int = 600) -> str:
        r = subprocess.run(
            ["ssh", *self._ssh_opts(ip, key), "bash -s"],
            input=script.encode(),
            capture_output=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            raise RuntimeError(f"SSH failed: {r.stderr.decode()[-400:]}")
        return r.stdout.decode()

    def provision(
        self,
        name: str,
        snapshot_tag: str,
        opts: Mapping[str, Any] | None = None,
    ) -> EnvHandle:
        _require_rclaude()
        opts = dict(opts or {})
        backend_name = opts.get("backend", "digitalocean")
        backend = _get_backend(backend_name)
        launcher = DevEnvLauncher(backend)
        info = launcher.launch(name=name)
        ssh_key = str(backend.ssh_key_path_for()) if hasattr(backend, "ssh_key_path_for") else ""
        return EnvHandle(
            driver_name="rclaude",
            opaque={
                "droplet_id": info.droplet_id,
                "ip": info.ip,
                "backend": backend_name,
                "ssh_key": ssh_key,
            },
        )

    def teardown(self, handle: EnvHandle) -> None:
        _require_rclaude()
        op = handle.opaque
        backend = _get_backend(op.get("backend", "digitalocean"))
        backend.destroy_vm(op["droplet_id"])

    def attach_argv(self, handle: EnvHandle, role: str, issue_safe: str) -> list[str]:
        ip = handle.opaque["ip"]
        key = handle.opaque.get("ssh_key", "")
        ssh_prefix = ["ssh", "-t", *self._ssh_opts(ip, key)[:-1]]  # drop target
        target = f"root@{ip}"
        if issue_safe and role:
            cmd = f"su - coder -c 'tmux attach -t po-{issue_safe}-{role}'"
        elif issue_safe:
            cmd = f"su - coder -c 'tmux attach -t $(tmux ls | grep po-{issue_safe} | head -1 | cut -d: -f1)'"
        else:
            cmd = "su - coder -c 'tmux attach'"
        return ssh_prefix + [target, cmd]

    def push_identity(
        self, handle: EnvHandle, tarball_path: Path, identity_hash: str
    ) -> None:
        ip, key = handle.opaque["ip"], handle.opaque.get("ssh_key", "")
        ssh_opts_no_target = self._ssh_opts(ip, key)[:-1]
        subprocess.run(
            [
                "scp",
                *ssh_opts_no_target,
                str(tarball_path),
                f"root@{ip}:/tmp/claude-identity.tar.gz",
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        self._ssh(
            ip,
            key,
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
        ip, key = handle.opaque["ip"], handle.opaque.get("ssh_key", "")
        if env_dict:
            lines = "\n".join(
                f"export {k}={shlex.quote(v)}" for k, v in env_dict.items() if v
            )
            b64 = base64.b64encode(lines.encode()).decode()
            self._ssh(
                ip,
                key,
                f"""
echo '{b64}' | base64 -d > /etc/po-env
chmod 644 /etc/po-env
grep -q 'source /etc/po-env' /home/coder/.bashrc 2>/dev/null || \
  echo 'source /etc/po-env 2>/dev/null' >> /home/coder/.bashrc
""",
            )
        if oauth_creds_bytes is not None:
            b64 = base64.b64encode(oauth_creds_bytes).decode()
            self._ssh(
                ip,
                key,
                f"""
mkdir -p /home/coder/.claude
echo '{b64}' | base64 -d > /home/coder/.claude/.credentials.json
chmod 600 /home/coder/.claude/.credentials.json
chown coder:coder /home/coder/.claude/.credentials.json
""",
            )

    def ensure_rig_remote(self, handle: EnvHandle) -> str:
        ip, key = handle.opaque["ip"], handle.opaque.get("ssh_key", "")
        self._ssh(
            ip,
            key,
            """
if [ ! -d /home/coder/rig-remote.git ]; then
  git init --bare /home/coder/rig-remote.git
  chown -R coder:coder /home/coder/rig-remote.git
fi
""",
        )
        return f"ssh://root@{ip}/home/coder/rig-remote.git"

    def start_worker(self, handle: EnvHandle, pool_name: str) -> None:
        ip, key = handle.opaque["ip"], handle.opaque.get("ssh_key", "")
        escaped_pool = shlex.quote(pool_name)
        self._ssh(
            ip,
            key,
            rf"""
set -euo pipefail
apt-get install -y tmux 2>/dev/null || true

if ! command -v uv &>/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | HOME=/home/coder sh
fi

su - coder -c "
  export PATH=/home/coder/.local/bin:\$PATH
  uv tool install prefect-orchestration 2>/dev/null || true
  uv tool install po-formulas-software-dev 2>/dev/null || true
"

su - coder -c "
  tmux has-session -t prefect-worker 2>/dev/null && exit 0
  tmux new-session -d -s prefect-worker \
    'export PATH=/home/coder/.local/bin:\$PATH; prefect worker start --pool {escaped_pool}'
"
""",
            timeout=300,
        )

    def health(self, handle: EnvHandle) -> EnvHealth:
        ip, key = handle.opaque["ip"], handle.opaque.get("ssh_key", "")
        try:
            self._ssh(ip, key, "echo ok", timeout=15)
            return EnvHealth(ok=True, summary="SSH reachable")
        except Exception as exc:
            return EnvHealth(ok=False, summary=f"SSH failed: {exc}")

    def build_image(self, opts: Mapping[str, Any] | None = None) -> None:
        pass  # VM driver: no image to bake

    def fs_download(
        self, handle: EnvHandle, remote_path: str, local_path: Path
    ) -> None:
        ip, key = handle.opaque["ip"], handle.opaque.get("ssh_key", "")
        local_path.mkdir(parents=True, exist_ok=True)
        ssh_cmd = f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        if key:
            ssh_cmd += f" -i {key}"
        subprocess.run(
            [
                "rsync",
                "-az",
                "-e",
                ssh_cmd,
                f"coder@{ip}:{remote_path}/",
                str(local_path) + "/",
            ],
            capture_output=True,
            timeout=300,
        )
