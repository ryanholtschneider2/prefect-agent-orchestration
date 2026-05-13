"""`po.env_drivers` entry-point group — cloud-env driver Protocol.

Drivers implement the contract that `po env up / down / attach` and the
`--env <name>` flag dispatch against. Core ships no real driver — the
in-tree `NoopDriver` is for unit tests only and is NOT registered as a
`po.env_drivers` entry point.

See `engdocs/cloud-envs.md` § "Writing a driver" for the integration
guide and the rclaude pack (prefect-orchestration-9ws.3) for the
canonical real-world impl.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


# Constrained to JSON-native types so 9ws.4 can persist EnvHandle to TOML
# without inventing a custom encoder.
_JSON_TYPES = (str, int, float, bool, type(None), list, dict)


@dataclass(frozen=True)
class EnvHandle:
    """Opaque, per-env handle returned by `provision()`.

    `driver_name` is the entry-point name (e.g. "daytona"). `opaque` is
    a JSON-serializable mapping of driver-internal state — core never
    inspects it. 9ws.4 persists `(driver_name, opaque)` pairs to
    `~/.config/po/envs/<name>.toml`.

    Mutability note: `Mapping[str, Any]` is a typing hint; the underlying
    default-factory dict is not deep-frozen. Drivers MUST NOT mutate
    `opaque` in place — return a new EnvHandle if state changes.
    """

    driver_name: str
    opaque: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        def _check(v: Any) -> None:
            if isinstance(v, dict):
                for k, vv in v.items():
                    if not isinstance(k, str):
                        raise TypeError(
                            f"EnvHandle.opaque keys must be str, got {type(k).__name__}"
                        )
                    _check(vv)
            elif isinstance(v, list):
                for vv in v:
                    _check(vv)
            elif not isinstance(v, _JSON_TYPES):
                raise TypeError(
                    f"EnvHandle.opaque values must be JSON-native; "
                    f"got {type(v).__name__}"
                )

        _check(dict(self.opaque))


@dataclass(frozen=True)
class EnvHealth:
    """Result of `EnvDriver.health()`.

    `ok` is the boolean gate (matches `Status.OK` vs `Status.FAIL`).
    `details` is a free-form mapping for diagnostics that surface in
    `po env doctor` output. Drivers should populate at minimum
    `reachable: bool`, `worker_alive: bool`, `last_seen_at: str | None`.
    """

    ok: bool
    summary: str
    details: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class EnvDriver(Protocol):
    """The plugin contract every cloud-env pack implements.

    All methods are synchronous; drivers may run asyncio internally.
    Drivers MUST NOT mutate `EnvHandle.opaque` in place — return a new
    handle if state changes during provisioning.

    `runtime_checkable` validates method presence only; the descriptive
    `name: str` attribute and method type annotations are NOT enforced
    at runtime. Use mypy / pyright during driver development.
    """

    name: str

    def provision(
        self,
        name: str,
        snapshot_tag: str,
        opts: Mapping[str, Any] | None = None,
    ) -> EnvHandle:
        """Create a sandbox/VM/pod from `snapshot_tag`, return its handle.

        `name` is the user-facing env name (`po env up --name <name>`).
        `opts` carries driver-specific knobs (region, size, …) parsed from
        CLI flags upstream. Idempotent re-runs (same `name`) should reuse
        the existing sandbox or raise a clearly-typed `EnvAlreadyExists`.
        """
        ...

    def teardown(self, handle: EnvHandle) -> None:
        """Destroy the sandbox/VM/pod. Idempotent on already-gone envs."""
        ...

    def attach_argv(self, handle: EnvHandle, role: str, issue_safe: str) -> list[str]:
        """Return the argv `po attach` should `os.execvp()` into.

        Typically `["daytona", "ssh", "<sid>", "-t",
                    f"tmux attach -t po-{issue_safe}-{role}"]`.
        `issue_safe` is already the dot-sanitized form (issue id with
        `.` → `_`); drivers don't sanitize again.
        """
        ...

    def push_identity(
        self, handle: EnvHandle, tarball_path: Path, identity_hash: str
    ) -> None:
        """Upload the curated `~/.claude/` tarball to the sandbox.

        `identity_hash` is the local content hash; drivers should
        persist it (e.g. as `/home/coder/.claude/.identity-hash`) so
        the next dispatch can short-circuit when local hash hasn't
        changed.
        """
        ...

    def push_credentials(
        self,
        handle: EnvHandle,
        env_dict: Mapping[str, str],
        oauth_creds_bytes: bytes | None,
    ) -> None:
        """Inject per-sandbox env vars and optional OAuth credentials.

        `env_dict` keys are env var names (`ANTHROPIC_API_KEY`,
        `GITHUB_TOKEN`, `PO_BACKEND`, …); driver wires them via
        whatever mechanism the platform provides (Daytona's secret
        API, k8s Secret, Modal env, …).

        `oauth_creds_bytes` (when not None) is the raw bytes of
        `~/.claude/.credentials.json`; driver writes them at the
        sandbox-side conventional path with mode 0600.
        """
        ...

    def ensure_rig_remote(self, handle: EnvHandle) -> str:
        """Bootstrap a bare git remote inside the sandbox; return SSH URL.

        Idempotent: returns the same URL on subsequent calls. Used by
        `po run --env <name>` to `git push` the rig before flow
        dispatch. Returns `""` if the driver uses tar-based rig
        transport instead (the `--rig-transport=tar` fallback).
        """
        ...

    def start_worker(self, handle: EnvHandle, pool_name: str) -> None:
        """Start `prefect worker --pool <pool_name>` inside the sandbox.

        Driver is responsible for making the worker survive driver-
        exit (supervisord, systemd, nohup, whatever the platform
        supports). Idempotent on already-running workers.
        """
        ...

    def health(self, handle: EnvHandle) -> EnvHealth:
        """Cheap read-only probe; used by `po env doctor` and `po env list`."""
        ...


def load_drivers() -> dict[str, EnvDriver]:
    """Return {name: instantiated EnvDriver} for every registered EP.

    Failed loads / instantiations are skipped silently — matches the
    `load_commands()` / `load_formulas()` precedent. Each entry-point
    target must be either a class implementing `EnvDriver` (called with
    no args) or a callable returning an `EnvDriver` instance. Targets
    that load successfully but don't satisfy `isinstance(..., EnvDriver)`
    are silently skipped (the doctor check surfaces these as WARN).

    Last-write-wins on name collision (matches `load_commands`).
    """
    out: dict[str, EnvDriver] = {}
    try:
        eps = entry_points(group="po.env_drivers")
    except TypeError:
        eps = entry_points().get("po.env_drivers", [])  # type: ignore[assignment]
    for ep in eps:
        try:
            target = ep.load()
            instance = target() if callable(target) else target
        except Exception:
            continue
        if isinstance(instance, EnvDriver):
            out[ep.name] = instance
    return out


def list_driver_eps() -> list:
    """Raw entry-point list for doctor / packs.list — preserves dist info."""
    try:
        return list(entry_points(group="po.env_drivers"))
    except TypeError:
        return list(entry_points().get("po.env_drivers", []))  # type: ignore[assignment]


@dataclass
class NoopDriver:
    """Trivial driver that records calls in memory. Tests only.

    Not registered in core's `pyproject.toml` — tests instantiate it
    directly. The "doctor lists registered drivers" criterion is exercised
    by a separate test that monkeypatches `list_driver_eps`.

    Pack authors: this is a copy-paste skeleton — annotations and
    method shape match `EnvDriver` exactly.
    """

    name: str = "noop"
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    def provision(
        self,
        name: str,
        snapshot_tag: str,
        opts: Mapping[str, Any] | None = None,
    ) -> EnvHandle:
        self.calls.append(("provision", name, snapshot_tag, dict(opts or {})))
        return EnvHandle(
            driver_name="noop",
            opaque={"name": name, "snapshot_tag": snapshot_tag},
        )

    def teardown(self, handle: EnvHandle) -> None:
        self.calls.append(("teardown", handle.driver_name))

    def attach_argv(self, handle: EnvHandle, role: str, issue_safe: str) -> list[str]:
        self.calls.append(("attach_argv", role, issue_safe))
        return ["true"]

    def push_identity(
        self, handle: EnvHandle, tarball_path: Path, identity_hash: str
    ) -> None:
        self.calls.append(("push_identity", str(tarball_path), identity_hash))

    def push_credentials(
        self,
        handle: EnvHandle,
        env_dict: Mapping[str, str],
        oauth_creds_bytes: bytes | None,
    ) -> None:
        self.calls.append(
            (
                "push_credentials",
                dict(env_dict),
                len(oauth_creds_bytes) if oauth_creds_bytes else 0,
            )
        )

    def ensure_rig_remote(self, handle: EnvHandle) -> str:
        self.calls.append(("ensure_rig_remote",))
        return ""

    def start_worker(self, handle: EnvHandle, pool_name: str) -> None:
        self.calls.append(("start_worker", pool_name))

    def health(self, handle: EnvHandle) -> EnvHealth:
        self.calls.append(("health",))
        return EnvHealth(ok=True, summary="noop healthy")


__all__ = [
    "EnvDriver",
    "EnvHandle",
    "EnvHealth",
    "NoopDriver",
    "list_driver_eps",
    "load_drivers",
]
