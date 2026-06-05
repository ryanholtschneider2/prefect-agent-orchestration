"""Unit tests for `prefect_orchestration.beads_backend` (verdict-channel seam).

Backs `prefect-orchestration-9xa`. Mocks `subprocess.run` for the dolt/br
shellouts; the real-br round-trip is gated on `shutil.which("br")`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from prefect_orchestration import beads_backend
from prefect_orchestration.beads_backend import (
    normalize_dep_rows,
    read_verdict,
    resolve_backend,
    write_verdict,
)


def _write_meta(rig: Path, payload: dict) -> None:
    beads = rig / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "metadata.json").write_text(json.dumps(payload))


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> object:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


# ───────────────────────── resolve_backend ─────────────────────────


def test_resolve_backend_env_override_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_meta(tmp_path, {"dolt_mode": "server"})  # would sniff dolt
    monkeypatch.setenv("PO_BEADS_BACKEND", "br")
    assert resolve_backend(tmp_path) == "br"


def test_resolve_backend_env_invalid_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_meta(tmp_path, {"database": "beads.db", "jsonl_export": "issues.jsonl"})
    monkeypatch.setenv("PO_BEADS_BACKEND", "nonsense")
    assert resolve_backend(tmp_path) == "br"  # ignored env -> sniff


def test_resolve_backend_sniffs_dolt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PO_BEADS_BACKEND", raising=False)
    _write_meta(tmp_path, {"dolt_mode": "server", "database": "dolt"})
    assert resolve_backend(tmp_path) == "dolt"


def test_resolve_backend_sniffs_br(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PO_BEADS_BACKEND", raising=False)
    _write_meta(tmp_path, {"database": "beads.db", "jsonl_export": "issues.jsonl"})
    assert resolve_backend(tmp_path) == "br"


def test_resolve_backend_default_dolt_when_no_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PO_BEADS_BACKEND", raising=False)
    assert resolve_backend(tmp_path) == "dolt"  # no .beads/ -> safe default


# ───────────────────────── read_verdict (br) ─────────────────────────


def _br_show_stdout(comments: list[dict]) -> str:
    return json.dumps([{"id": "x-1", "title": "t", "comments": comments}])


def test_read_verdict_br_latest_wins() -> None:
    comments = [
        {"id": 1, "text": 'po-verdict:builder:{"v": 1}'},
        {"id": 3, "text": 'po-verdict:builder:{"v": 3}'},
        {"id": 2, "text": 'po-verdict:builder:{"v": 2}'},
    ]
    with patch("subprocess.run", return_value=_completed(_br_show_stdout(comments))):
        result = read_verdict("x-1", "builder", backend="br", rig_path=None, timeout=10)
    assert result == {"v": 3}  # max id, not last in list


def test_read_verdict_br_role_filter() -> None:
    comments = [
        {"id": 1, "text": 'po-verdict:builder:{"role": "builder"}'},
        {"id": 2, "text": 'po-verdict:linter:{"role": "linter"}'},
    ]
    with patch("subprocess.run", return_value=_completed(_br_show_stdout(comments))):
        result = read_verdict("x-1", "builder", backend="br", rig_path=None, timeout=10)
    assert result == {"role": "builder"}  # ignores the linter comment


def test_read_verdict_br_missing_comment_raises_keyerror() -> None:
    comments = [{"id": 1, "text": "just a normal comment"}]
    with patch(
        "subprocess.run", return_value=_completed(_br_show_stdout(comments))
    ) as mock_run:
        with pytest.raises(KeyError):
            read_verdict("x-1", "builder", backend="br", rig_path=None, timeout=10)
    assert mock_run.call_count == 1  # semantic failure: not retried here


def test_read_verdict_br_nonzero_exit_raises_filenotfound() -> None:
    with patch(
        "subprocess.run", return_value=_completed("", returncode=3, stderr="no")
    ):
        with pytest.raises(FileNotFoundError):
            read_verdict("nope", "builder", backend="br", rig_path=None, timeout=10)


def test_read_verdict_br_unparseable_raises_valueerror() -> None:
    with patch("subprocess.run", return_value=_completed("not json")):
        with pytest.raises(ValueError):
            read_verdict("x-1", "builder", backend="br", rig_path=None, timeout=10)


def test_read_verdict_br_scalar_payload_wrapped() -> None:
    comments = [{"id": 1, "text": "po-verdict:builder:42"}]
    with patch("subprocess.run", return_value=_completed(_br_show_stdout(comments))):
        result = read_verdict("x-1", "builder", backend="br", rig_path=None, timeout=10)
    assert result == {"value": 42}


# ───────────────────────── read_verdict (dolt) ─────────────────────────


def test_read_verdict_dolt_reads_metadata() -> None:
    stdout = json.dumps([{"id": "d-1", "metadata": {"po.triage": {"flags": ["a"]}}}])
    with patch("subprocess.run", return_value=_completed(stdout)):
        result = read_verdict(
            "d-1", "triage", backend="dolt", rig_path=None, timeout=10
        )
    assert result == {"flags": ["a"]}


# ───────────────────────── write_verdict ─────────────────────────


def test_write_verdict_br_appends_comment() -> None:
    with patch("subprocess.run", return_value=_completed()) as mock_run:
        write_verdict("x-1", "builder", {"v": 2}, backend="br", rig_path=None)
    cmd = mock_run.call_args.args[0]
    assert cmd[:4] == ["br", "comments", "add", "x-1"]
    assert cmd[4] == 'po-verdict:builder:{"v": 2}'


def test_write_verdict_dolt_sets_metadata() -> None:
    with patch("subprocess.run", return_value=_completed()) as mock_run:
        write_verdict("d-1", "triage", {"v": 1}, backend="dolt", rig_path=None)
    cmd = mock_run.call_args.args[0]
    assert cmd[:4] == ["bd", "update", "d-1", "--set-metadata"]
    assert cmd[4] == 'po.triage={"v": 1}'


def test_write_verdict_raises_on_nonzero_exit() -> None:
    with patch("subprocess.run", return_value=_completed("", returncode=1, stderr="x")):
        with pytest.raises(RuntimeError):
            write_verdict("x-1", "builder", {"v": 1}, backend="br", rig_path=None)


# ───────────────────────── normalize_dep_rows ─────────────────────────


def test_normalize_dep_rows_dolt_passthrough() -> None:
    rows = [{"id": "A", "status": "open", "title": "A"}]
    assert normalize_dep_rows(rows, direction="up", backend="dolt") is rows


def test_normalize_dep_rows_br_up_uses_issue_id() -> None:
    rows = [
        {
            "issue_id": "child",
            "depends_on_id": "parent",
            "status": "open",
            "title": "child",
        }
    ]
    out = normalize_dep_rows(rows, direction="up", backend="br")
    assert out[0]["id"] == "child"
    assert out[0]["status"] == "open"
    assert out[0]["title"] == "child"


def test_normalize_dep_rows_br_down_uses_depends_on_id() -> None:
    rows = [
        {
            "issue_id": "child",
            "depends_on_id": "parent",
            "status": "open",
            "title": "parent",
        }
    ]
    out = normalize_dep_rows(rows, direction="down", backend="br")
    assert out[0]["id"] == "parent"


# ───────────────────────── real-br round-trip ─────────────────────────


@pytest.mark.skipif(shutil.which("br") is None, reason="br CLI not installed")
def test_real_br_verdict_round_trip(tmp_path: Path) -> None:
    """Genuine `br init` -> two writes -> read returns the latest payload."""
    subprocess.run(
        ["br", "init"], cwd=tmp_path, check=True, capture_output=True, text=True
    )
    create = subprocess.run(
        ["br", "create", "round-trip", "--json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    bead_id = json.loads(create.stdout)["id"]

    write_verdict(bead_id, "builder", {"v": 1}, backend="br", rig_path=tmp_path)
    write_verdict(bead_id, "builder", {"v": 2}, backend="br", rig_path=tmp_path)

    result = read_verdict(
        bead_id, "builder", backend="br", rig_path=tmp_path, timeout=10
    )
    assert result == {"v": 2}

    # A missing role raises KeyError (semantic failure, not retried).
    with pytest.raises(KeyError):
        read_verdict(bead_id, "linter", backend="br", rig_path=tmp_path, timeout=10)


@pytest.mark.skipif(shutil.which("br") is None, reason="br CLI not installed")
def test_real_br_metadata_sniff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real `br init` workspace is detected as the br backend."""
    monkeypatch.delenv("PO_BEADS_BACKEND", raising=False)
    subprocess.run(
        ["br", "init"], cwd=tmp_path, check=True, capture_output=True, text=True
    )
    assert resolve_backend(tmp_path) == "br"


def test_binary_map() -> None:
    assert beads_backend.BINARY == {"dolt": "bd", "br": "br"}
