"""Unit tests for `iter_bead_ids` — the run-dir convention→backend id map."""

from __future__ import annotations

from pathlib import Path

from prefect_orchestration import iter_bead_ids


def test_convention_id_shape() -> None:
    # Hyphen-separated so beads-rust accepts the id (delegates to
    # beads_meta.iter_bead_id, the single source of truth).
    assert iter_bead_ids.convention_id("bd-3ih", "build", 3) == "bd-3ih-build-iter3"


def test_lookup_missing_file_returns_none(tmp_path: Path) -> None:
    # Fresh run-dir, no map written yet.
    assert iter_bead_ids.lookup(tmp_path, "bd-3ih.build.iter1") is None


def test_record_then_lookup_round_trips(tmp_path: Path) -> None:
    key = iter_bead_ids.convention_id("bd-3ih", "build", 1)
    iter_bead_ids.record(tmp_path, key, "bd-22q")
    assert iter_bead_ids.lookup(tmp_path, key) == "bd-22q"
    # The map file lands in the run-dir under the documented name.
    assert (tmp_path / iter_bead_ids.MAP_FILENAME).is_file()


def test_record_accumulates_multiple_keys(tmp_path: Path) -> None:
    iter_bead_ids.record(tmp_path, "s.build.iter1", "real-a")
    iter_bead_ids.record(tmp_path, "s.lint.iter1", "real-b")
    assert iter_bead_ids.lookup(tmp_path, "s.build.iter1") == "real-a"
    assert iter_bead_ids.lookup(tmp_path, "s.lint.iter1") == "real-b"


def test_record_is_noop_when_unchanged(tmp_path: Path) -> None:
    key = "s.build.iter1"
    iter_bead_ids.record(tmp_path, key, "real-a")
    path = tmp_path / iter_bead_ids.MAP_FILENAME
    before = path.read_text()
    iter_bead_ids.record(tmp_path, key, "real-a")  # same mapping
    assert path.read_text() == before


def test_record_overwrites_existing_key(tmp_path: Path) -> None:
    key = "s.build.iter1"
    iter_bead_ids.record(tmp_path, key, "real-a")
    iter_bead_ids.record(tmp_path, key, "real-b")
    assert iter_bead_ids.lookup(tmp_path, key) == "real-b"


def test_lookup_corrupt_file_returns_none(tmp_path: Path) -> None:
    (tmp_path / iter_bead_ids.MAP_FILENAME).write_text("{not valid json")
    assert iter_bead_ids.lookup(tmp_path, "s.build.iter1") is None


def test_lookup_non_dict_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / iter_bead_ids.MAP_FILENAME).write_text("[1, 2, 3]")
    assert iter_bead_ids.lookup(tmp_path, "s.build.iter1") is None


def test_record_recovers_from_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / iter_bead_ids.MAP_FILENAME
    path.write_text("garbage{")
    iter_bead_ids.record(tmp_path, "s.build.iter1", "real-a")
    assert iter_bead_ids.lookup(tmp_path, "s.build.iter1") == "real-a"
