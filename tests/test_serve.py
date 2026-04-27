"""Unit tests for prefect_orchestration.serve — pluggable PG creds plumbing."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prefect_orchestration import serve as serve_mod


@pytest.fixture
def serve_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Redirect serve.py paths into tmp + stub all subprocess shellouts."""
    home = tmp_path / "home"
    unit_dir = home / ".config" / "systemd" / "user"
    creds_dir = home / ".config" / "po"
    pg_data = home / ".local" / "share" / "prefect-postgres"

    monkeypatch.setattr(serve_mod, "UNIT_DIR", unit_dir)
    monkeypatch.setattr(serve_mod, "PG_UNIT", unit_dir / "prefect-postgres.service")
    monkeypatch.setattr(serve_mod, "SERVER_UNIT", unit_dir / "prefect-server.service")
    monkeypatch.setattr(serve_mod, "PG_DATA_DIR", pg_data)
    monkeypatch.setattr(serve_mod, "CREDS_DIR", creds_dir)
    monkeypatch.setattr(serve_mod, "CREDS_FILE", creds_dir / "serve.env")

    calls: dict[str, list] = {"run": [], "call": [], "which": []}

    def fake_which(b: str) -> str:
        calls["which"].append(b)
        return f"/usr/bin/{b}"

    def fake_run(args, **kw):
        calls["run"].append(list(args) if isinstance(args, (list, tuple)) else [args])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def fake_call(args, **kw):
        calls["call"].append(list(args))
        return 0

    monkeypatch.setattr(serve_mod.shutil, "which", fake_which)
    monkeypatch.setattr(serve_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(serve_mod.subprocess, "call", fake_call)
    return calls


def _run(*args: str) -> object:
    return CliRunner().invoke(serve_mod.app, list(args), catch_exceptions=False)


# ---- AC2: random password + 0600 creds file --------------------------------


def test_install_no_flags_generates_random_password(serve_env: dict) -> None:
    result = _run("install", "--no-enable")
    assert result.exit_code == 0, result.output

    creds = serve_mod.load_creds()
    assert creds is not None
    assert creds.pg_user == "prefect"
    assert creds.pg_db == "prefect"
    assert creds.pg_host == "127.0.0.1"
    assert creds.pg_port == "5432"
    assert len(creds.pg_password) >= 32
    assert re.match(r"^[A-Za-z0-9_\-]+$", creds.pg_password)

    # AC2: file mode 0600, parent dir 0700.
    assert serve_mod.CREDS_FILE.exists()
    assert (serve_mod.CREDS_FILE.stat().st_mode & 0o777) == 0o600
    assert (serve_mod.CREDS_DIR.stat().st_mode & 0o777) == 0o700

    # AC2: both unit files reference the creds file via EnvironmentFile.
    pg_unit = serve_mod.PG_UNIT.read_text()
    server_unit = serve_mod.SERVER_UNIT.read_text()
    expected_ref = f"EnvironmentFile={serve_mod.CREDS_FILE}"
    assert expected_ref in pg_unit
    assert expected_ref in server_unit
    # No more hardcoded prefect:prefect creds in the unit body.
    assert "POSTGRES_PASSWORD=prefect" not in pg_unit


# ---- AC1: per-field flags ---------------------------------------------------


def test_install_accepts_pg_flags(serve_env: dict) -> None:
    result = _run(
        "install",
        "--no-enable",
        "--pg-user", "alice",
        "--pg-password", "topsecret_123",
        "--pg-db", "mydb",
        "--pg-host", "10.0.0.5",
        "--pg-port", "6543",
    )
    assert result.exit_code == 0, result.output

    creds = serve_mod.load_creds()
    assert creds is not None
    assert (creds.pg_user, creds.pg_password, creds.pg_db, creds.pg_host, creds.pg_port) == (
        "alice", "topsecret_123", "mydb", "10.0.0.5", "6543",
    )

    # AC4: Prefect profile URL matches the creds.
    config_set_calls = [
        c for c in serve_env["run"]
        if isinstance(c, list) and len(c) >= 4 and c[1] == "config" and c[2] == "set"
        and c[3].startswith("PREFECT_API_DATABASE_CONNECTION_URL=")
    ]
    assert config_set_calls, f"no config set call found in {serve_env['run']!r}"
    url_arg = config_set_calls[0][3].split("=", 1)[1]
    assert url_arg == "postgresql+asyncpg://alice:topsecret_123@10.0.0.5:6543/mydb"


def test_install_rejects_unsafe_password(serve_env: dict) -> None:
    result = CliRunner().invoke(
        serve_mod.app,
        ["install", "--no-enable", "--pg-password", "has space"],
    )
    assert result.exit_code != 0


# ---- AC3: idempotency + --rotate-password ----------------------------------


def test_install_reuses_existing_creds(serve_env: dict) -> None:
    _run("install", "--no-enable")
    first = serve_mod.load_creds()
    assert first is not None

    # Re-run with no flags.
    _run("install", "--no-enable")
    second = serve_mod.load_creds()
    assert second is not None
    assert second.pg_password == first.pg_password


def test_rotate_password_rotates(serve_env: dict) -> None:
    _run("install", "--no-enable")
    first = serve_mod.load_creds()
    assert first is not None

    _run("install", "--no-enable", "--rotate-password")
    second = serve_mod.load_creds()
    assert second is not None
    assert second.pg_password != first.pg_password


# ---- AC4: profile URL matches creds (URL-encoded) --------------------------


def test_profile_url_url_encodes_password(serve_env: dict) -> None:
    # "+" is URL-special; serve allows "_.-" + alnum, but we still URL-encode.
    # Use a value that is safe-charset but also legal so we can assert encoding logic.
    result = _run(
        "install",
        "--no-enable",
        "--pg-password", "abc.DEF-ghi_123",
    )
    assert result.exit_code == 0
    config_set_calls = [
        c for c in serve_env["run"]
        if isinstance(c, list) and len(c) >= 4 and c[1] == "config" and c[2] == "set"
        and c[3].startswith("PREFECT_API_DATABASE_CONNECTION_URL=")
    ]
    url = config_set_calls[0][3].split("=", 1)[1]
    # Ensure user+password+host+port+db pulled through.
    assert "abc.DEF-ghi_123" in url
    assert url.startswith("postgresql+asyncpg://prefect:")
    assert url.endswith("@127.0.0.1:5432/prefect")


# ---- AC5: uninstall --purge-data removes creds file ------------------------


def test_uninstall_without_purge_keeps_creds(serve_env: dict) -> None:
    _run("install", "--no-enable")
    assert serve_mod.CREDS_FILE.exists()

    _run("uninstall")
    assert serve_mod.CREDS_FILE.exists(), "creds file should survive plain uninstall"


def test_uninstall_purge_data_removes_creds(serve_env: dict) -> None:
    _run("install", "--no-enable")
    assert serve_mod.CREDS_FILE.exists()

    _run("uninstall", "--purge-data")
    assert not serve_mod.CREDS_FILE.exists()


# ---- AC7: --external-pg ----------------------------------------------------


def test_install_external_pg_skips_local_container(serve_env: dict) -> None:
    ext = "postgresql://u:p@db.example.com:5432/myprefect"
    result = _run("install", "--no-enable", "--external-pg", ext)
    assert result.exit_code == 0, result.output

    # PG unit NOT written.
    assert not serve_mod.PG_UNIT.exists()
    # Server unit written, lacks Requires=prefect-postgres.
    server_body = serve_mod.SERVER_UNIT.read_text()
    assert "Requires=prefect-postgres.service" not in server_body
    assert "pg_isready" not in server_body

    # Creds file marks external mode + carries the URL.
    creds = serve_mod.load_creds()
    assert creds is not None
    assert creds.is_external()
    assert creds.external_url == ext

    # Profile URL = supplied URL exactly.
    config_set_calls = [
        c for c in serve_env["run"]
        if isinstance(c, list) and len(c) >= 4 and c[1] == "config" and c[2] == "set"
        and c[3].startswith("PREFECT_API_DATABASE_CONNECTION_URL=")
    ]
    assert config_set_calls
    assert config_set_calls[0][3] == f"PREFECT_API_DATABASE_CONNECTION_URL={ext}"


def test_install_external_pg_rejects_combined_flags(serve_env: dict) -> None:
    result = CliRunner().invoke(
        serve_mod.app,
        [
            "install", "--no-enable",
            "--external-pg", "postgresql://h/db",
            "--pg-user", "x",
        ],
    )
    assert result.exit_code != 0


def test_install_external_pg_rejects_bad_scheme(serve_env: dict) -> None:
    result = CliRunner().invoke(
        serve_mod.app,
        ["install", "--no-enable", "--external-pg", "mysql://h/db"],
    )
    assert result.exit_code != 0


# ---- build_db_url unit ------------------------------------------------------


def test_build_db_url_url_encodes() -> None:
    creds = serve_mod.ServeCreds(
        pg_user="u", pg_password="p@ss/word", pg_db="d", pg_host="h", pg_port="1"
    )
    url = serve_mod.build_db_url(creds)
    # quote() with safe="" encodes both "@" and "/" in the password.
    assert url == "postgresql+asyncpg://u:p%40ss%2Fword@h:1/d"


def test_build_db_url_external_passthrough() -> None:
    creds = serve_mod.ServeCreds(external_url="postgresql://x/y")
    assert serve_mod.build_db_url(creds) == "postgresql://x/y"


def test_load_creds_missing_returns_none(serve_env: dict) -> None:
    assert serve_mod.load_creds() is None


def test_save_then_load_roundtrip(serve_env: dict) -> None:
    creds = serve_mod.ServeCreds(
        pg_user="alice", pg_password="rot13", pg_db="db1",
        pg_host="1.2.3.4", pg_port="9000",
    )
    serve_mod.save_creds(creds)
    loaded = serve_mod.load_creds()
    assert loaded is not None
    assert (loaded.pg_user, loaded.pg_password, loaded.pg_db, loaded.pg_host, loaded.pg_port) == (
        "alice", "rot13", "db1", "1.2.3.4", "9000",
    )
    assert not loaded.is_external()
