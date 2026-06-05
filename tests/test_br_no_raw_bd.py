"""Regression tests for prefect-orchestration-q7e.

On a `br` (beads_rust) rig the `bd` binary is still on `PATH` but resolves
no dolt database, so any residual hardcoded `bd` shellout prints
``Error: no beads database found`` mid-flow. These tests pin the seam
routing for the per-role-step paths exercised by `software_dev_full`:

  - `_metadata_binary` gates `--set-metadata` writes to the dolt backend.
  - `auto_store` falls back to `FileStore` on a br rig.
  - `BeadsStore` reads/writes route through the seam (no raw `bd`).
  - `stamp_run_url_on_bead` / `_stamp_run_dir_meta` no-op on a br rig.
  - `context_bundle._bd_show` resolves the binary per rig.

The unifying assertion: **no subprocess invokes a hardcoded ``bd`` on a
br rig.**
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from prefect_orchestration import context_bundle, run_handles
from prefect_orchestration.beads_meta import (
    BeadsStore,
    FileStore,
    _metadata_binary,
    auto_store,
)


@pytest.fixture(autouse=True)
def _clear_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # `resolve_backend` honours PO_BEADS_BACKEND first; clear it so the
    # `.beads/metadata.json` sniff drives every case deterministically.
    monkeypatch.delenv("PO_BEADS_BACKEND", raising=False)


def _write_meta(rig: Path, payload: dict) -> None:
    beads = rig / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "metadata.json").write_text(json.dumps(payload))


def _br_rig(rig: Path) -> Path:
    _write_meta(rig, {"database": "beads.db", "jsonl_export": "issues.jsonl"})
    return rig


def _dolt_rig(rig: Path) -> Path:
    _write_meta(rig, {"dolt_mode": "server", "database": "dolt"})
    return rig


# ───────────────────────── _metadata_binary ─────────────────────────


def test_metadata_binary_dolt_returns_bd(tmp_path: Path) -> None:
    _dolt_rig(tmp_path)
    with patch("prefect_orchestration.beads_meta.shutil.which", return_value="/x/bd"):
        assert _metadata_binary(tmp_path) == "bd"


def test_metadata_binary_br_returns_none_even_with_bd_on_path(tmp_path: Path) -> None:
    # bd is on PATH (as on a real br host) but the backend is br, so the
    # metadata channel is unavailable: must resolve to None, not "bd".
    _br_rig(tmp_path)
    with patch("prefect_orchestration.beads_meta.shutil.which", return_value="/x/bd"):
        assert _metadata_binary(tmp_path) is None


def test_metadata_binary_dolt_returns_none_when_bd_absent(tmp_path: Path) -> None:
    _dolt_rig(tmp_path)
    with patch("prefect_orchestration.beads_meta.shutil.which", return_value=None):
        assert _metadata_binary(tmp_path) is None


# ───────────────────────── auto_store ─────────────────────────


def test_auto_store_br_falls_back_to_filestore(tmp_path: Path) -> None:
    _br_rig(tmp_path)
    with patch("prefect_orchestration.beads_meta.shutil.which", return_value="/x/bd"):
        store = auto_store("seed-1", tmp_path / "run", rig_path=tmp_path)
    assert isinstance(store, FileStore)


def test_auto_store_dolt_uses_beadsstore(tmp_path: Path) -> None:
    _dolt_rig(tmp_path)
    with patch("prefect_orchestration.beads_meta.shutil.which", return_value="/x/bd"):
        store = auto_store("seed-1", tmp_path / "run", rig_path=tmp_path)
    assert isinstance(store, BeadsStore)


# ───────────────────────── BeadsStore on br ─────────────────────────


def test_beadsstore_set_raises_on_br_without_shelling_bd(tmp_path: Path) -> None:
    _br_rig(tmp_path)
    store = BeadsStore(parent_id="seed-1", rig_path=tmp_path)
    with patch("prefect_orchestration.beads_meta.subprocess.run") as run:
        with pytest.raises(NotImplementedError):
            store.set("session_builder", "uuid-123")
    run.assert_not_called()


def test_beadsstore_show_metadata_empty_on_br_without_shelling_bd(
    tmp_path: Path,
) -> None:
    _br_rig(tmp_path)
    store = BeadsStore(parent_id="seed-1", rig_path=tmp_path)
    with patch("prefect_orchestration.beads_meta.subprocess.run") as run:
        assert store._show_metadata() == {}
        assert store.get("anything") is None
    run.assert_not_called()


def test_beadsstore_set_uses_bd_on_dolt(tmp_path: Path) -> None:
    _dolt_rig(tmp_path)
    store = BeadsStore(parent_id="seed-1", rig_path=tmp_path)
    with (
        patch("prefect_orchestration.beads_meta.shutil.which", return_value="/x/bd"),
        patch("prefect_orchestration.beads_meta.subprocess.run") as run,
    ):
        store.set("k", "v")
    argv = run.call_args[0][0]
    assert argv[0] == "bd" and "--set-metadata" in argv


# ───────────────────────── stamp_run_url_on_bead ─────────────────────────


def test_stamp_run_url_noop_on_br(tmp_path: Path) -> None:
    _br_rig(tmp_path)
    with patch("prefect_orchestration.run_handles.subprocess.run") as run:
        run_handles.stamp_run_url_on_bead("issue-1", "flow-run-1", rig_path=tmp_path)
    run.assert_not_called()


def test_stamp_run_url_shells_bd_on_dolt(tmp_path: Path) -> None:
    _dolt_rig(tmp_path)
    with (
        patch("prefect_orchestration.beads_meta.shutil.which", return_value="/x/bd"),
        patch(
            "prefect_orchestration.run_handles.prefect_run_url",
            return_value="http://x/runs/1",
        ),
        patch("prefect_orchestration.run_handles.subprocess.run") as run,
    ):
        run_handles.stamp_run_url_on_bead("issue-1", "flow-run-1", rig_path=tmp_path)
    argv = run.call_args[0][0]
    assert argv[0] == "bd" and "--set-metadata" in argv


# ───────────────────────── agent_step._stamp_run_dir_meta ─────────────────────────


def test_stamp_run_dir_meta_noop_on_br(tmp_path: Path) -> None:
    from prefect_orchestration import agent_step

    _br_rig(tmp_path)
    with patch("prefect_orchestration.agent_step.subprocess.run") as run:
        agent_step._stamp_run_dir_meta("seed-1", tmp_path, tmp_path / "run")
    run.assert_not_called()


# ───────────────────────── context_bundle._bd_show ─────────────────────────


def test_context_bundle_bd_show_uses_br_binary_on_br_rig(tmp_path: Path) -> None:
    _br_rig(tmp_path)

    captured: dict[str, list] = {}

    def fake_run(argv, *a, **k):  # noqa: ANN001, ANN002, ANN003
        captured["argv"] = argv
        return type("R", (), {"returncode": 0, "stdout": "ok"})()

    # br must be on PATH for the seam to resolve it.
    with (
        patch(
            "prefect_orchestration.beads_meta.shutil.which",
            side_effect=lambda b: f"/x/{b}" if b == "br" else None,
        ),
        patch("prefect_orchestration.context_bundle.subprocess.run", fake_run),
    ):
        out = context_bundle._bd_show("seed-1", tmp_path)
    assert out == "ok"
    assert captured["argv"][0] == "br"


def test_context_bundle_bd_show_empty_when_no_binary(tmp_path: Path) -> None:
    _br_rig(tmp_path)
    with (
        patch("prefect_orchestration.beads_meta.shutil.which", return_value=None),
        patch("prefect_orchestration.context_bundle.subprocess.run") as run,
    ):
        assert context_bundle._bd_show("seed-1", tmp_path) == ""
    run.assert_not_called()
