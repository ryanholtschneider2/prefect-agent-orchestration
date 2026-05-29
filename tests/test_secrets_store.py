"""Unit tests for prefect_orchestration.secrets_store."""

from __future__ import annotations

import pytest

from prefect_orchestration import secrets_store as s


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    """Redirect the store + keyfile into a tmp dir; force the keyfile path
    (no OS keyring) for deterministic tests."""
    monkeypatch.setattr(s, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(s, "STORE_PATH", tmp_path / "secrets.enc")
    monkeypatch.setattr(s, "KEYFILE_PATH", tmp_path / "secrets.key")
    # Make keyring import fail so we exercise the keyfile fallback.
    monkeypatch.setitem(__import__("sys").modules, "keyring", None)
    yield


def test_set_get_roundtrip():
    s.set_secret("FOO", "bar")
    assert s.get_secret("FOO") == "bar"


def test_scope_override():
    s.set_secret("TOKEN", "global-val")
    s.set_secret("TOKEN", "env-val", scope="laptop")
    assert s.resolve("laptop")["TOKEN"] == "env-val"  # env beats global
    assert s.resolve("other")["TOKEN"] == "global-val"  # falls back to global


def test_resolve_merges_global_and_env():
    s.set_secret("G", "1")
    s.set_secret("E", "2", scope="laptop")
    assert s.resolve("laptop") == {"G": "1", "E": "2"}


def test_list_returns_keys_only():
    s.set_secret("SECRET_KEY", "supersecret")
    listing = s.list_secrets()
    assert "SECRET_KEY" in listing[s.GLOBAL]
    # values never appear anywhere in the listing structure
    assert "supersecret" not in str(listing)


def test_encrypted_at_rest():
    s.set_secret("API", "plaintext-value")
    raw = s.STORE_PATH.read_bytes()
    assert b"plaintext-value" not in raw
    assert b"API" not in raw


def test_delete():
    s.set_secret("X", "y")
    assert s.delete_secret("X") is True
    assert s.get_secret("X") is None
    assert s.delete_secret("X") is False


def test_import_env(tmp_path):
    env_file = tmp_path / "smoke.env"
    env_file.write_text('A=1\n# comment\n\nexport B="two"\nC=\'three\'\n')
    n = s.import_env(env_file, scope="laptop")
    assert n == 3
    assert s.resolve("laptop") == {"A": "1", "B": "two", "C": "three"}


def test_set_rejects_bad_key():
    with pytest.raises(s.SecretsError):
        s.set_secret("BAD=KEY", "v")
