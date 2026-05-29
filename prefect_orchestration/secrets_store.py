"""Encrypted local secret store for `--env` dispatch.

Secrets live OUT of the remote box (on the dispatcher), encrypted at rest, and
are injected into the remote run at spawn time — never committed, never baked
into images, never sent through the Prefect API. This mirrors the control-plane
model of hosted background-agent systems: the orchestrator holds the encrypted
store and pushes/brokers env vars when a session spawns.

Storage:
  ~/.config/po/secrets.enc   AES-256-GCM ciphertext (mode 0600)
  ~/.config/po/secrets.key   32-byte data key (mode 0600) — fallback when the
                             OS keyring isn't available (it usually isn't on a
                             headless box). If `keyring` is importable the data
                             key is kept there instead and no keyfile is written.

Scopes:
  global      applies to every env
  <env-name>  applies only to that env; overrides global on key collision

`resolve(env_name)` returns the merged {KEY: VALUE} dict the driver injects.
`list_secrets()` returns KEYS ONLY — values never leave via listing.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CONFIG_DIR = Path.home() / ".config" / "po"
STORE_PATH = CONFIG_DIR / "secrets.enc"
KEYFILE_PATH = CONFIG_DIR / "secrets.key"
_KEYRING_SERVICE = "po-secrets"
_KEYRING_USER = "data-key"
GLOBAL = "global"


class SecretsError(RuntimeError):
    """Raised for secret-store failures surfaced to the CLI."""


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)


def _load_key() -> bytes:
    """Return the 32-byte data key, creating it on first use.

    Prefers the OS keyring; falls back to a 0600 keyfile next to the store.
    """
    try:
        import keyring  # type: ignore

        stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        if stored:
            return base64.b64decode(stored)
        key = AESGCM.generate_key(bit_length=256)
        keyring.set_password(
            _KEYRING_SERVICE, _KEYRING_USER, base64.b64encode(key).decode()
        )
        return key
    except Exception:
        pass  # keyring absent/unusable — fall back to keyfile

    _ensure_dir()
    if KEYFILE_PATH.exists():
        return base64.b64decode(KEYFILE_PATH.read_text().strip())
    key = AESGCM.generate_key(bit_length=256)
    KEYFILE_PATH.write_text(base64.b64encode(key).decode())
    os.chmod(KEYFILE_PATH, 0o600)
    return key


def _load_all() -> dict[str, dict[str, str]]:
    """Decrypt the store. Shape: {"global": {...}, "envs": {env: {...}}}."""
    if not STORE_PATH.exists():
        return {"global": {}, "envs": {}}
    blob = STORE_PATH.read_bytes()
    if len(blob) < 13:
        raise SecretsError(f"corrupt secret store at {STORE_PATH}")
    nonce, ct = blob[:12], blob[12:]
    try:
        pt = AESGCM(_load_key()).decrypt(nonce, ct, None)
    except Exception as exc:  # noqa: BLE001
        raise SecretsError(
            f"could not decrypt {STORE_PATH} (wrong/lost key?): {exc}"
        ) from exc
    data: dict[str, Any] = json.loads(pt)
    data.setdefault("global", {})
    data.setdefault("envs", {})
    return data


def _save_all(data: dict[str, dict[str, str]]) -> None:
    _ensure_dir()
    nonce = os.urandom(12)
    ct = AESGCM(_load_key()).encrypt(nonce, json.dumps(data).encode(), None)
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_bytes(nonce + ct)
    os.chmod(tmp, 0o600)
    tmp.replace(STORE_PATH)
    os.chmod(STORE_PATH, 0o600)


def _bucket(data: dict, scope: str) -> dict[str, str]:
    if scope == GLOBAL:
        return data["global"]
    return data["envs"].setdefault(scope, {})


def set_secret(key: str, value: str, *, scope: str = GLOBAL) -> None:
    if not key or "=" in key:
        raise SecretsError(f"invalid secret key: {key!r}")
    data = _load_all()
    _bucket(data, scope)[key] = value
    _save_all(data)


def delete_secret(key: str, *, scope: str = GLOBAL) -> bool:
    data = _load_all()
    bucket = _bucket(data, scope)
    if key not in bucket:
        return False
    del bucket[key]
    _save_all(data)
    return True


def list_secrets(*, scope: str | None = None) -> dict[str, list[str]]:
    """Return {scope: [KEYS]} — values are never returned here."""
    data = _load_all()
    out: dict[str, list[str]] = {GLOBAL: sorted(data["global"])}
    for env_name, kv in data["envs"].items():
        out[env_name] = sorted(kv)
    if scope is not None:
        return {scope: out.get(scope, [])}
    return out


def get_secret(key: str, *, scope: str = GLOBAL) -> str | None:
    return _bucket(_load_all(), scope).get(key)


def import_env(path: Path, *, scope: str = GLOBAL) -> int:
    """Bulk-import KEY=VALUE lines from a .env file. Returns count imported."""
    if not path.exists():
        raise SecretsError(f"no such file: {path}")
    data = _load_all()
    bucket = _bucket(data, scope)
    n = 0
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if not k:
            continue
        bucket[k] = v
        n += 1
    _save_all(data)
    return n


def resolve(env_name: str) -> dict[str, str]:
    """Merged {KEY: VALUE} for an env: global first, env-scoped overrides."""
    data = _load_all()
    merged = dict(data["global"])
    merged.update(data["envs"].get(env_name, {}))
    return merged
