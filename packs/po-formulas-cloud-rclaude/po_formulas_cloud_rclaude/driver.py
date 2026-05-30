"""RClaudeEnvDriver — run PO formulas on a remote machine via rclaude.

Three backends:

- ``ssh``  — a machine you ALREADY own and have registered with rclaude
  (``~/.config/rclaude/hosts.toml``) or can reach as ``user@addr``. No
  provisioning; the flow runs as your connecting user in ``$HOME``. This
  is the daily-driver path for "run this on my laptop / home server".
- ``digitalocean`` — provision a fresh DO droplet (the original cloud-VM
  path: ``root`` login, a ``coder`` user, ``/home/coder``).
- ``daytona`` — a Daytona sandbox (SDK-native: no SSH/cloudflared). All
  remote ops run via ``process.exec``; the box has no public IP. Fast
  create-from-snapshot + suspend/resume. Secrets are delivered the same
  way (rclaude store -> tmpfs); ``attach`` uses Daytona's SSH gateway.

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
    from rclaude import secrets as _rcsecrets
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

    @staticmethod
    def _is_daytona(op: Mapping[str, Any]) -> bool:
        return op.get("backend") == "daytona"

    def _daytona_backend(self):
        from rclaude.backends.daytona_backend import DaytonaBackend

        return DaytonaBackend()

    def _daytona_exec(
        self, op: Mapping[str, Any], script: str, timeout: int = 600
    ) -> str:
        """Run a bash script in the sandbox via the Daytona SDK (no SSH)."""
        be = self._daytona_backend()
        return be.exec(op["sandbox_id"], ["bash", "-lc", script])

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

        if backend_name == "daytona":
            from rclaude.backends.daytona_backend import DaytonaBackend
            from rclaude.daytona_devenv import BASE_SNAPSHOT, DaytonaDevEnv

            snapshot = opts.get("snapshot") or snapshot_tag or BASE_SNAPSHOT
            be = DaytonaBackend()
            if snapshot == BASE_SNAPSHOT:
                DaytonaDevEnv(be).ensure_base_snapshot()
            # auto_stop=0 disables idle suspend — the PO worker must stay alive
            # for the central server to dispatch to it. (Interactive `rclaude up`
            # keeps the 30-min default; suspend-between-runs for PO would need a
            # `po env stop/start` verb — follow-up.)
            vm = be.provision_vm(
                name=name,
                from_snapshot=snapshot,
                tags=["po-env"],
                auto_stop_minutes=int(opts.get("auto_stop_minutes", 0)),
            )
            return EnvHandle(
                driver_name="rclaude",
                opaque={
                    "backend": "daytona",
                    "sandbox_id": vm.id,
                    "snapshot": snapshot,
                    "host": opts.get("host", ""),
                    "api_url": opts.get("api_url", ""),
                },
            )

        raise ValueError(
            f"Unknown rclaude backend: {backend_name!r} "
            "(supported: ssh, digitalocean, daytona)"
        )

    def teardown(self, handle: EnvHandle) -> None:
        op = handle.opaque
        if self._is_daytona(op):
            _require_rclaude()
            be = self._daytona_backend()
            # tmpfs secrets vanish with the sandbox; scrub anyway, then delete.
            try:
                be.exec(op["sandbox_id"], ["bash", "-lc", _rcsecrets.scrub_script()])
            except Exception:  # noqa: BLE001
                pass
            be.destroy_vm(op["sandbox_id"])
            return
        if self._is_ssh(op):
            # Own host: never destroy it. Stop the worker and scrub the
            # RAM-only secrets file.
            try:
                self._ssh(
                    op,
                    f"tmux kill-session -t po-worker 2>/dev/null || true; "
                    f"{_rcsecrets.scrub_script()}; echo ok",
                    timeout=30,
                )
            except Exception:  # noqa: BLE001
                pass
            return
        _require_rclaude()
        backend = DigitalOceanBackend()
        backend.destroy_vm(op["droplet_id"])

    # ── suspend / resume (optional; daytona only) ─────────────────────────────
    # Not part of the EnvDriver Protocol (keeps isinstance stable for drivers
    # that can't suspend). `po env stop/start` calls these via hasattr.

    def suspend(self, handle: EnvHandle) -> None:
        """Suspend the env: keep disk, pause compute. Daytona only."""
        op = handle.opaque
        if not self._is_daytona(op):
            raise NotImplementedError(
                f"suspend/resume is only supported on the daytona backend "
                f"(this env is {op.get('backend')!r})."
            )
        _require_rclaude()
        self._daytona_backend().stop_vm(op["sandbox_id"])

    def resume(self, handle: EnvHandle) -> None:
        """Resume a suspended env (state intact). Daytona only.

        Brings the box back; the caller (`po env start`) re-pushes the RAM-only
        secrets and restarts the worker, since tmpfs clears and the worker dies
        on suspend. Disk-resident OAuth + clones survive.
        """
        op = handle.opaque
        if not self._is_daytona(op):
            raise NotImplementedError(
                f"suspend/resume is only supported on the daytona backend "
                f"(this env is {op.get('backend')!r})."
            )
        _require_rclaude()
        self._daytona_backend().start_vm(op["sandbox_id"])

    def attach_argv(self, handle: EnvHandle, role: str, issue_safe: str) -> list[str]:
        op = handle.opaque

        if issue_safe and role:
            inner = f"tmux attach -t po-{issue_safe}-{role}"
        elif issue_safe:
            inner = (
                f"tmux attach -t $(tmux ls | grep po-{issue_safe} "
                f"| head -1 | cut -d: -f1)"
            )
        else:
            inner = "tmux attach"

        if self._is_daytona(op):
            # Attach over Daytona's SSH-access gateway (ready-made ssh_command).
            be = self._daytona_backend()
            dto = be.ssh_access(op["sandbox_id"])
            argv = shlex.split(getattr(dto, "ssh_command", "") or "")
            if not argv:
                raise RuntimeError("Daytona SSH access unavailable for attach")
            if "-t" not in argv:
                argv.insert(1, "-t")
            argv[1:1] = ["-o", "StrictHostKeyChecking=accept-new"]
            argv.append(inner)
            return argv

        ssh_prefix = ["ssh", "-t", *self._ssh_opts(op)[:-1]]
        target = op["ssh_target"] if self._is_ssh(op) else f"root@{op['ip']}"
        if self._is_ssh(op):
            return ssh_prefix + [target, inner]
        return ssh_prefix + [target, f"su - coder -c {shlex.quote(inner)}"]

    # ── identity / credentials ────────────────────────────────────────────────

    def push_identity(
        self, handle: EnvHandle, tarball_path: Path, identity_hash: str
    ) -> None:
        op = handle.opaque
        if self._is_daytona(op):
            # No scp; upload the identity tarball via the fs API, then unpack.
            be = self._daytona_backend()
            sid = op["sandbox_id"]
            be.upload_bytes(sid, "/tmp/claude-identity.tar.gz", tarball_path.read_bytes())
            self._daytona_exec(
                op,
                'mkdir -p "$HOME/.claude" && '
                'tar -xzf /tmp/claude-identity.tar.gz -C "$HOME" 2>/dev/null || true; '
                'rm -f /tmp/claude-identity.tar.gz',
            )
            return
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
        # rclaude owns the secret store + delivery. Merge this host's stored
        # secrets with any env_dict PO passes (e.g. ANTHROPIC_API_KEY) and let
        # rclaude write them to its RAM-only tmpfs file (the single writer).
        scope = op.get("host") or _rcsecrets.GLOBAL
        merged = dict(_rcsecrets.resolve(scope))
        merged.update({k: v for k, v in (env_dict or {}).items() if v})

        if self._is_daytona(op):
            be = self._daytona_backend()
            sid = op["sandbox_id"]
            if merged:
                be.exec(sid, ["bash", "-lc", _rcsecrets.write_payload_script(merged)])
            if oauth_creds_bytes is not None:
                # process.exec runs as root ($HOME=/root) — put creds where the
                # worker's claude subprocesses actually look, and stamp
                # .claude.json so --print skips onboarding/trust prompts.
                home = be.exec(sid, ["bash", "-lc", "echo $HOME"]).strip() or "/root"
                be.upload_text(
                    sid, f"{home}/.claude/.credentials.json",
                    oauth_creds_bytes.decode(), mode="600",
                )
                be.upload_text(
                    sid, f"{home}/.claude.json",
                    '{"numStartups": 1, "hasCompletedOnboarding": true, '
                    '"bypassPermissionsModeAccepted": true}',
                )
            return

        if merged:
            self._ssh(op, _rcsecrets.write_payload_script(merged), timeout=60)
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
        if self._is_daytona(op) or self._is_ssh(op):
            # No reachable bare-git endpoint (Daytona has no public IP; ssh is
            # an owned host). PO falls back to tar rig transport.
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

        if self._is_daytona(op):
            # Sandbox runs as the `daytona` user in $HOME. uv tool installs po;
            # prefect lives in the po tool venv. Worker tmux session sources the
            # RAM-only secrets so flow + agent subprocs inherit them.
            api_health_url = (
                api_url.rstrip("/") + "/health" if api_url
                else "http://127.0.0.1:4200/api/health"
            )
            # Forwarder target: the private (tailnet) Prefect host:port.
            from urllib.parse import urlparse

            _pu = urlparse(api_url) if api_url else None
            fwd_host = (_pu.hostname if _pu else None) or "127.0.0.1"
            fwd_port = (_pu.port if _pu else None) or 4200
            self._daytona_exec(
                op,
                rf"""
set -uo pipefail
# exec runs as root in the sandbox — no sudo. procps gives pkill/pgrep so we
# can reliably replace a stale worker; tmux for agent role sessions.
command -v pkill >/dev/null 2>&1 || apt-get install -y procps tmux 2>/dev/null || true
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null || true
fi
export PATH="$HOME/.local/bin:$PATH"
command -v po >/dev/null 2>&1 || uv tool install prefect-orchestration 2>/dev/null || true
TOOLBIN="$(uv tool dir 2>/dev/null)/prefect-orchestration/bin"
export PATH="$TOOLBIN:$PATH"
{api_line}
{_rcsecrets.source_snippet()}
# Tailscale: a Daytona cloud sandbox isn't on your tailnet, so a private
# PREFECT_API_URL is unreachable by default. ROOTLESS (no sudo): install the
# static binaries to $HOME and run tailscaled in userspace mode (SOCKS5 on
# 1055). Then run a tiny localhost->SOCKS TCP forwarder so the worker reaches
# Prefect at http://127.0.0.1:4200 — a RAW TCP relay, so BOTH the HTTP polls
# AND the events websocket tunnel through (per-library proxies like ALL_PROXY
# work for httpx but NOT Prefect's events websocket, which then times out and
# kills the worker ~40s in).
if [ -n "${{TS_AUTHKEY:-}}" ]; then
  export PATH="$HOME/.local/bin:$PATH"
  TS_SOCK="$HOME/.tailscaled.sock"
  if ! command -v tailscaled >/dev/null 2>&1; then
    TSVER=$(curl -fsSL "https://pkgs.tailscale.com/stable/?mode=json" \
      | grep -oE '"Version" *: *"[0-9.]+"' | head -1 | grep -oE '[0-9.]+')
    if [ -n "$TSVER" ]; then
      curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${{TSVER}}_amd64.tgz" -o /tmp/ts.tgz \
        && tar -xzf /tmp/ts.tgz -C /tmp \
        && mkdir -p "$HOME/.local/bin" \
        && cp "/tmp/tailscale_${{TSVER}}_amd64/tailscale" "/tmp/tailscale_${{TSVER}}_amd64/tailscaled" "$HOME/.local/bin/"
    fi
  fi
  pgrep -f "tailscaled --tun=userspace" >/dev/null 2>&1 || \
    "$HOME/.local/bin/tailscaled" --tun=userspace-networking \
      --state="$HOME/.tailscaled.state" --socket="$TS_SOCK" \
      --socks5-server=localhost:1055 >/tmp/tailscaled.log 2>&1 &
  sleep 2
  "$HOME/.local/bin/tailscale" --socket="$TS_SOCK" up --authkey="$TS_AUTHKEY" \
    --hostname="po-daytona-$(hostname)" --accept-routes >/tmp/tsup.log 2>&1 \
    || echo "WARN: tailscale up failed (see /tmp/tsup.log)" >&2
  # localhost:4200 -> SOCKS5(1055) -> {fwd_host}:{fwd_port} raw TCP forwarder.
  cat > "$HOME/.po-forwarder.py" <<'FWEOF'
import asyncio
from python_socks.async_.asyncio import Proxy
TH, TP = "{fwd_host}", {fwd_port}
async def handle(r, w):
    try:
        s = await Proxy.from_url("socks5://127.0.0.1:1055").connect(dest_host=TH, dest_port=TP)
        rr, rw = await asyncio.open_connection(sock=s)
    except Exception:
        try: w.close()
        except Exception: pass
        return
    async def pipe(a, b):
        try:
            while True:
                d = await a.read(65536)
                if not d: break
                b.write(d); await b.drain()
        except Exception: pass
        finally:
            try: b.close()
            except Exception: pass
    await asyncio.gather(pipe(r, rw), pipe(rr, w))
async def main():
    srv = await asyncio.start_server(handle, "127.0.0.1", 4200)
    async with srv: await srv.serve_forever()
asyncio.run(main())
FWEOF
  TOOLPY="$(uv tool dir 2>/dev/null)/prefect-orchestration/bin/python"
  pkill -f po-forwarder.py 2>/dev/null || true
  nohup "$TOOLPY" "$HOME/.po-forwarder.py" >/tmp/po-forwarder.log 2>&1 &
  for _ in $(seq 1 20); do
    curl -fsS -m 4 -o /dev/null http://127.0.0.1:4200/api/health && break || sleep 2
  done
  export PREFECT_API_URL=http://127.0.0.1:4200/api
fi
if ! command -v prefect >/dev/null 2>&1; then
  echo "ERROR: prefect not in po tool env — bake packs via build_image or sync" >&2
  exit 1
fi
# process.exec runs as root; Claude Code refuses --dangerously-skip-permissions
# as root unless IS_SANDBOX=1. The worker passes its env to agent subprocesses.
export IS_SANDBOX=1
# Launch the worker via nohup in THIS shell (which has PREFECT_API_URL + PATH).
# Pass IS_SANDBOX via `env` explicitly — the agent's claude subprocess inherits
# the worker's os.environ (agent_session._clean_env copies it), and Claude Code
# needs IS_SANDBOX=1 to allow --dangerously-skip-permissions as root.
# Kill any stale worker first (portable; procps may be freshly installed).
pkill -f "prefect worker start" 2>/dev/null || \
  for p in /proc/[0-9]*; do grep -qa "prefect worker start" "$p/cmdline" 2>/dev/null && kill "$(basename "$p")" 2>/dev/null; done
sleep 1
nohup env IS_SANDBOX=1 prefect worker start --pool {escaped_pool} --type process \
  > /tmp/po-worker.log 2>&1 &
WORKER_PID=$!
sleep 6
# portable liveness check (pgrep/tmux may be absent in the sandbox)
if kill -0 "$WORKER_PID" 2>/dev/null; then
  echo "started worker on pool {pool_name}"
else
  echo "ERROR: worker died on startup:" >&2; tail -15 /tmp/po-worker.log >&2; exit 1
fi
""",
                timeout=300,
            )
            # Ship bd + dolt so flows that shell out to bd work in the sandbox.
            try:
                self.install_bd_tools(op)
            except Exception as exc:  # noqa: BLE001
                print(f"[rclaude-driver] WARNING: bd/dolt install failed: {exc}")
            return

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
# Inherit injected secrets (rclaude tmpfs, RAM-only) into worker -> flow subprocs.
{_rcsecrets.source_snippet()}
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
  {_rcsecrets.source_snippet()}
  tmux has-session -t po-worker 2>/dev/null && exit 0
  tmux new-session -d -s po-worker 'exec prefect worker start --pool {escaped_pool} --type process'
"
""",
            timeout=300,
        )

    def health(self, handle: EnvHandle) -> EnvHealth:
        op = handle.opaque
        if self._is_daytona(op):
            sid = op.get("sandbox_id", "")
            try:
                self._daytona_exec(op, "echo ok", timeout=15)
                return EnvHealth(ok=True, summary=f"daytona sandbox reachable ({sid})")
            except Exception as exc:  # noqa: BLE001
                return EnvHealth(ok=False, summary=f"daytona exec failed: {exc}")
        target = op.get("ssh_target") if self._is_ssh(op) else op.get("ip")
        try:
            self._ssh(op, "echo ok", timeout=15)
            return EnvHealth(ok=True, summary=f"SSH reachable ({target})")
        except Exception as exc:  # noqa: BLE001
            return EnvHealth(ok=False, summary=f"SSH failed: {exc}")

    def build_image(self, opts: Mapping[str, Any] | None = None) -> None:
        """Bake the reusable Daytona base snapshot (no-op for VM/own-host).

        For daytona this pre-pays the slow ttyd+node+Claude install once so
        `provision` is near-instant. Prefect/PO packs are installed at
        worker-start (start_worker), matching the ssh/cloud path.
        """
        opts = dict(opts or {})
        if opts.get("backend") != "daytona":
            return
        _require_rclaude()
        from rclaude.daytona_devenv import DaytonaDevEnv

        DaytonaDevEnv().ensure_base_snapshot(rebuild=bool(opts.get("rebuild")))

    # ── pack sync (editable/local packs the auto-installer can't reach) ───────

    _PACK_EXCLUDES = (
        ".git", ".venv", "__pycache__", "node_modules",
        "*.egg-info", ".pytest_cache", ".mypy_cache",
    )

    def _push_dir(self, op: Mapping[str, Any], local: str, remote_rel: str) -> None:
        """Mirror a local dir to <remote $HOME>/<remote_rel> (transport-aware)."""
        if self._is_daytona(op):
            self._daytona_push_dir(op, local, remote_rel)
        else:
            self._rsync_up(op, local, remote_rel)

    def _daytona_push_dir(
        self, op: Mapping[str, Any], local: str, remote_rel: str
    ) -> None:
        """tar (with excludes) -> fs.upload_file -> exec-unpack into sandbox $HOME.

        No rsync/scp (no public IP). Upload via the Daytona fs API, NOT
        base64-over-exec — the latter exceeds Daytona's command-length limit for
        larger packs. Mirrors _rsync_up's exclude set + --delete semantics.
        """
        import subprocess

        excl = []
        for pat in self._PACK_EXCLUDES:
            excl += ["--exclude", pat]
        tar = subprocess.run(
            ["tar", "-czf", "-", *excl, "-C", local.rstrip("/"), "."],
            capture_output=True,
            check=True,
            timeout=120,
        )
        sid = op["sandbox_id"]
        remote_tgz = f"/tmp/po-pack-{remote_rel.replace('/', '_')}.tgz"
        self._daytona_backend().upload_bytes(sid, remote_tgz, tar.stdout)
        rel_q = shlex.quote(remote_rel)
        self._daytona_exec(
            op,
            f'rm -rf "$HOME"/{rel_q} && mkdir -p "$HOME"/{rel_q} && '
            f'tar -xzf {shlex.quote(remote_tgz)} -C "$HOME"/{rel_q} && '
            f'rm -f {shlex.quote(remote_tgz)}',
            timeout=300,
        )

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

    def push_rig(self, handle: EnvHandle, rig_path: Path) -> str:
        """Deliver the rig into the env; return the remote rig path (or "").

        Non-daytona drivers rely on git/ssh (the rig is already present on the
        remote) and return "" — no rig_path remap. Daytona has no public IP, so
        tar the rig (incl .beads, minus .git/.venv) -> fs upload -> extract to
        <home>/rig, and return that absolute path so the dispatched flow + the
        run-dir mirror-back target the sandbox copy.
        """
        op = handle.opaque
        if not self._is_daytona(op):
            return ""
        import subprocess

        sid = op["sandbox_id"]
        be = self._daytona_backend()
        home = be.exec(sid, ["bash", "-lc", "echo $HOME"]).strip() or "/home/daytona"
        remote_rig = f"{home}/rig"
        excl: list[str] = []
        for pat in self._PACK_EXCLUDES:
            excl += ["--exclude", pat]
        tar = subprocess.run(
            ["tar", "-czf", "-", *excl, "-C", str(rig_path), "."],
            capture_output=True, check=True, timeout=300,
        )
        be.upload_bytes(sid, "/tmp/po-rig.tgz", tar.stdout)
        self._daytona_exec(
            op,
            f"rm -rf {shlex.quote(remote_rig)} && mkdir -p {shlex.quote(remote_rig)} && "
            f"tar -xzf /tmp/po-rig.tgz -C {shlex.quote(remote_rig)} && rm -f /tmp/po-rig.tgz && "
            # Drop the dispatcher's embedded-dolt port pointer so the sandbox's
            # bd starts its OWN dolt server against the delivered .beads/dolt.
            f"rm -f {shlex.quote(remote_rig)}/.beads/dolt-server.port",
            timeout=300,
        )
        return remote_rig

    def install_bd_tools(self, op: Mapping[str, Any]) -> None:
        """Upload the bd + dolt binaries (gzipped) so flows can use bd in the
        sandbox. Local-only fork has no release URL, so ship the dispatcher's
        binaries. No-op when a tool isn't on the dispatcher's PATH."""
        import gzip
        import shutil

        be = self._daytona_backend()
        sid = op["sandbox_id"]
        for tool in ("dolt", "bd"):  # dolt first (bd depends on it)
            local = shutil.which(tool)
            if not local:
                continue
            be.upload_bytes(sid, f"/tmp/{tool}.gz", gzip.compress(Path(local).read_bytes()))
            be.exec(sid, ["bash", "-lc",
                f"mkdir -p $HOME/.local/bin && gunzip -c /tmp/{tool}.gz > $HOME/.local/bin/{tool} "
                f"&& chmod +x $HOME/.local/bin/{tool} && rm -f /tmp/{tool}.gz"])

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
            self._push_dir(op, local, f".po-packs/{rel[name]}")
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
        # python-socks powers the daytona localhost->SOCKS forwarder that
        # tunnels the worker's HTTP + events-websocket through the tailnet.
        # (socksio kept for httpx SOCKS as a belt-and-suspenders.)
        argv += " --with python-socks --with socksio"

        install_script = rf"""
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
"""
        if self._is_daytona(op):
            self._daytona_exec(op, install_script, timeout=600)
        else:
            self._ssh(op, install_script, timeout=600)
        print(f"[rclaude-driver] reinstalled remote po tool with {len(packs)} pack(s)")

    def fs_download(
        self, handle: EnvHandle, remote_path: str, local_path: Path
    ) -> None:
        op = handle.opaque

        if self._is_daytona(op):
            # No public IP for rsync — tar the run-dir to a temp file in the
            # sandbox, pull it via the fs API, unpack locally.
            local_path.mkdir(parents=True, exist_ok=True)
            be = self._daytona_backend()
            sid = op["sandbox_id"]
            be.exec(
                sid, ["bash", "-lc",
                      f"cd {shlex.quote(remote_path)} 2>/dev/null && "
                      "tar -czf /tmp/po-dl.tgz . || true"],
            )
            try:
                raw = be.download_bytes(sid, "/tmp/po-dl.tgz")
            except Exception:  # noqa: BLE001
                raw = b""
            if raw:
                import io
                import tarfile

                with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
                    tf.extractall(local_path)
            return

        ssh_cmd = "ssh " + " ".join(self._ssh_opts(op)[:-1])

        if self._is_ssh(op):
            # Own host: --rig-path points at the REMOTE tree, so core's
            # `local_path` (rig_path/.planning/<formula>/<issue>) is the
            # run-dir's ABSOLUTE path ON THE REMOTE — it won't exist locally.
            # Mirror into a dispatcher-local cache instead.
            #
            # The <formula> segment core guesses from the dispatched formula
            # name does NOT always match where the flow actually wrote (e.g.
            # software-dev-edit/fast share `.planning/software-dev-full/<id>/`
            # by design). So pull every `.planning/*/<issue>/` subtree from the
            # remote, not just the guessed path.
            issue = local_path.name
            planning_root = local_path.parent.parent  # <rig>/.planning
            dest_root = Path.home() / ".cache" / "po" / "env-runs"
            dest_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "rsync", "-az", "-e", ssh_cmd, "--prune-empty-dirs",
                    "--include=/*/", f"--include=/*/{issue}/***", "--exclude=*",
                    f"{op['ssh_target']}:{planning_root}/",
                    str(dest_root) + "/",
                ],
                capture_output=True,
                timeout=300,
            )
            print(
                f"[rclaude-driver] run artifacts mirrored under "
                f"{dest_root}/<formula>/{issue}/"
            )
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
