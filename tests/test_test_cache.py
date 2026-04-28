"""Unit tests for `prefect_orchestration.test_cache`."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from prefect_orchestration.test_cache import (
    CACHE_DIRNAME,
    CACHE_FILENAME,
    GIT_FAILED,
    SCHEMA_VERSION,
    cache_get,
    cache_key,
    cache_put,
    compute_scope_hash,
    compute_source_hash,
)


# ─────────────────────── helpers ─────────────────────────────────────


def _git_init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "x@y.z"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "x"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=str(repo), check=True
    )


def _commit_all(repo: Path, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=str(repo), check=True)


# ─────────────────────── cache_key ───────────────────────────────────


def test_cache_key_changes_when_any_component_changes() -> None:
    base = ("unit", "src-h", "col-h", "scp-h")
    k = cache_key(*base)
    assert cache_key("e2e", "src-h", "col-h", "scp-h") != k
    assert cache_key("unit", "src-h2", "col-h", "scp-h") != k
    assert cache_key("unit", "src-h", "col-h2", "scp-h") != k
    assert cache_key("unit", "src-h", "col-h", "scp-h2") != k


def test_cache_key_is_deterministic() -> None:
    a = cache_key("unit", "s", "c", "x")
    b = cache_key("unit", "s", "c", "x")
    assert a == b


# ─────────────────────── compute_scope_hash ──────────────────────────


def test_scope_hash_full_distinct_from_empty() -> None:
    """`None` (full layer) and `[]` (empty scope) must hash differently."""
    assert compute_scope_hash(None) != compute_scope_hash([])


def test_scope_hash_order_invariant() -> None:
    a = compute_scope_hash([Path("tests/test_a.py"), Path("tests/test_b.py")])
    b = compute_scope_hash([Path("tests/test_b.py"), Path("tests/test_a.py")])
    assert a == b


def test_scope_hash_different_paths_different_hash() -> None:
    a = compute_scope_hash([Path("tests/test_a.py")])
    b = compute_scope_hash([Path("tests/test_b.py")])
    assert a != b


# ─────────────────────── compute_source_hash ─────────────────────────


def test_source_hash_excludes_untracked_junk(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    _git_init(repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("a\n")
    _commit_all(repo, "init")

    h1 = compute_source_hash(repo, paths=("src",))

    # Untracked junk files do NOT change the hash.
    (repo / "src" / "junk.tmp").write_text("garbage\n")
    (repo / "src" / "__pycache__").mkdir()
    (repo / "src" / "__pycache__" / "a.cpython-313.pyc").write_text("bytecode")
    h2 = compute_source_hash(repo, paths=("src",))
    assert h1 == h2


def test_source_hash_invalidates_on_tracked_edit(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    _git_init(repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("a\n")
    _commit_all(repo, "init")

    h1 = compute_source_hash(repo, paths=("src",))

    # Modify a tracked file (uncommitted is fine — ls-files reads working tree).
    (repo / "src" / "a.py").write_text("a-modified\n")
    h2 = compute_source_hash(repo, paths=("src",))
    assert h1 != h2


def test_source_hash_returns_sentinel_on_non_repo(tmp_path: Path) -> None:
    assert compute_source_hash(tmp_path, paths=("src",)) == GIT_FAILED


# ─────────────────────── cache_get / cache_put ───────────────────────


def test_cache_round_trip(tmp_path: Path) -> None:
    verdict = {"passed": True, "count": 12, "summary": "ok"}
    cache_put(
        tmp_path,
        "k1",
        verdict,
        run_id="rid",
        layer="unit",
        source_hash="src",
        collection_hash="col",
        scope_hash="scp",
        scope_paths=["tests/test_foo.py"],
    )
    got = cache_get(tmp_path, "k1")
    assert got is not None
    assert got["passed"] is True
    assert got["count"] == 12
    assert got["summary"] == "ok"
    # Metadata fields are stripped from the surface result.
    assert "produced_at" not in got
    assert "produced_by" not in got
    assert "source_hash" not in got


def test_cache_miss_returns_none(tmp_path: Path) -> None:
    assert cache_get(tmp_path, "no-such-key") is None


def test_cache_creates_dir_lazily(tmp_path: Path) -> None:
    cache_put(
        tmp_path,
        "k",
        {"passed": True},
        run_id="r",
        layer="unit",
        source_hash="s",
        collection_hash="c",
        scope_hash="x",
    )
    assert (tmp_path / CACHE_DIRNAME).is_dir()
    assert (tmp_path / CACHE_DIRNAME / CACHE_FILENAME).is_file()


def test_cache_file_is_well_formed_json(tmp_path: Path) -> None:
    cache_put(
        tmp_path,
        "k",
        {"passed": True},
        run_id="r",
        layer="unit",
        source_hash="s",
        collection_hash="c",
        scope_hash="x",
    )
    raw = (tmp_path / CACHE_DIRNAME / CACHE_FILENAME).read_text()
    data = json.loads(raw)
    assert data["version"] == SCHEMA_VERSION
    assert "k" in data["entries"]
    entry = data["entries"]["k"]
    assert entry["produced_by"] == "r"
    assert entry["layer"] == "unit"
    assert entry["source_hash"] == "s"


def test_cache_concurrent_writes_preserve_all_entries(tmp_path: Path) -> None:
    """N threads each write a distinct key — file stays valid JSON
    and every entry is present after `join()`. Validates fcntl.flock +
    os.replace preserves entries under contention.
    """
    n = 20

    def writer(i: int) -> None:
        cache_put(
            tmp_path,
            f"k{i}",
            {"passed": True, "count": i},
            run_id=f"r{i}",
            layer="unit",
            source_hash=f"s{i}",
            collection_hash="c",
            scope_hash="x",
        )

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    raw = (tmp_path / CACHE_DIRNAME / CACHE_FILENAME).read_text()
    data = json.loads(raw)
    for i in range(n):
        entry = data["entries"].get(f"k{i}")
        assert entry is not None, f"missing k{i} after concurrent writes"
        assert entry["count"] == i


def test_cache_repeated_writes_overwrite_same_key(tmp_path: Path) -> None:
    cache_put(
        tmp_path,
        "k",
        {"passed": True, "count": 1},
        run_id="r1",
        layer="unit",
        source_hash="s",
        collection_hash="c",
        scope_hash="x",
    )
    cache_put(
        tmp_path,
        "k",
        {"passed": False, "count": 2},
        run_id="r2",
        layer="unit",
        source_hash="s",
        collection_hash="c",
        scope_hash="x",
    )
    got = cache_get(tmp_path, "k")
    assert got is not None
    assert got["passed"] is False
    assert got["count"] == 2


def test_corrupt_cache_file_treated_as_empty(tmp_path: Path) -> None:
    cache_dir = tmp_path / CACHE_DIRNAME
    cache_dir.mkdir()
    (cache_dir / CACHE_FILENAME).write_text("{not valid json")
    # cache_get on corrupt → None (empty), not raise
    assert cache_get(tmp_path, "anything") is None
    # cache_put on corrupt → starts fresh, write succeeds
    cache_put(
        tmp_path,
        "k",
        {"passed": True},
        run_id="r",
        layer="unit",
        source_hash="s",
        collection_hash="c",
        scope_hash="x",
    )
    assert cache_get(tmp_path, "k") is not None


def test_cache_get_distinct_keys_independent(tmp_path: Path) -> None:
    cache_put(
        tmp_path,
        "k1",
        {"passed": True},
        run_id="r",
        layer="unit",
        source_hash="s",
        collection_hash="c",
        scope_hash="x1",
    )
    cache_put(
        tmp_path,
        "k2",
        {"passed": False},
        run_id="r",
        layer="unit",
        source_hash="s",
        collection_hash="c",
        scope_hash="x2",
    )
    g1 = cache_get(tmp_path, "k1")
    g2 = cache_get(tmp_path, "k2")
    assert g1 is not None and g1["passed"] is True
    assert g2 is not None and g2["passed"] is False
