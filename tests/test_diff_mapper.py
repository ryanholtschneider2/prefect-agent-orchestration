"""Unit tests for `prefect_orchestration.diff_mapper`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from prefect_orchestration.diff_mapper import (
    DEFAULT_SMOKE_TESTS,
    FULL_SENTINEL,
    TRIPWIRES,
    compute_changed_files,
    map_files_to_tests,
    read_tests_changed,
    write_tests_changed,
)


# ─────────────────────── helpers ─────────────────────────────────────


def _run(repo: Path, *argv: str) -> None:
    subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)


def _git_init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _run(repo, "git", "init", "-q", "-b", "main")
    _run(repo, "git", "config", "user.email", "test@example.com")
    _run(repo, "git", "config", "user.name", "Test")
    _run(repo, "git", "config", "commit.gpgsign", "false")


def _commit_all(repo: Path, msg: str) -> None:
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-q", "-m", msg)


def _make_layout(repo: Path) -> None:
    """Mimic prefect-orchestration's actual layout: flat tests/."""
    (repo / "prefect_orchestration").mkdir()
    (repo / "prefect_orchestration" / "__init__.py").write_text("")
    (repo / "prefect_orchestration" / "foo.py").write_text("def f(): ...\n")
    (repo / "prefect_orchestration" / "sub").mkdir()
    (repo / "prefect_orchestration" / "sub" / "__init__.py").write_text("")
    (repo / "prefect_orchestration" / "sub" / "bar.py").write_text("def b(): ...\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_foo.py").write_text("def test_f(): ...\n")
    (repo / "tests" / "test_bar.py").write_text("def test_b(): ...\n")
    (repo / "tests" / "sub").mkdir()
    (repo / "tests" / "sub" / "test_bar.py").write_text("def test_b2(): ...\n")
    (repo / "tests" / "conftest.py").write_text("")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")


# ─────────────────────── compute_changed_files ───────────────────────


def test_compute_changed_files_uses_merge_base(tmp_path: Path) -> None:
    """Multi-commit branches see the full delta against base, not just HEAD~1."""
    repo = tmp_path / "r"
    _git_init(repo)
    _make_layout(repo)
    _commit_all(repo, "initial")
    # Create a branch and make 2 commits — HEAD~1..HEAD would only catch one.
    _run(repo, "git", "checkout", "-q", "-b", "feature")
    (repo / "prefect_orchestration" / "foo.py").write_text("def f(): return 1\n")
    _commit_all(repo, "first change")
    (repo / "prefect_orchestration" / "sub" / "bar.py").write_text(
        "def b(): return 2\n"
    )
    _commit_all(repo, "second change")

    changed = compute_changed_files(repo, base_ref="main")
    names = {str(p) for p in changed}
    assert "prefect_orchestration/foo.py" in names
    assert "prefect_orchestration/sub/bar.py" in names


def test_compute_changed_files_falls_back_when_base_unknown(tmp_path: Path) -> None:
    """Unknown base_ref → fall back to HEAD~1..HEAD without raising."""
    repo = tmp_path / "r"
    _git_init(repo)
    _make_layout(repo)
    _commit_all(repo, "initial")
    (repo / "prefect_orchestration" / "foo.py").write_text("def f(): return 1\n")
    _commit_all(repo, "tweak")

    changed = compute_changed_files(repo, base_ref="origin/does-not-exist")
    assert any(str(p) == "prefect_orchestration/foo.py" for p in changed)


def test_compute_changed_files_returns_empty_on_non_repo(tmp_path: Path) -> None:
    """Non-git dirs don't crash the helper."""
    assert compute_changed_files(tmp_path) == []


# ─────────────────────── map_files_to_tests ──────────────────────────


def test_map_flat_module_to_top_level_test(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _make_layout(repo)
    mapped, force = map_files_to_tests(
        [Path("prefect_orchestration/foo.py")],
        repo,
    )
    assert force is False
    assert Path("tests/test_foo.py") in mapped


def test_map_nested_module_to_nested_and_flat_tests(tmp_path: Path) -> None:
    """A nested source file matches both `tests/sub/test_bar.py` and `tests/test_bar.py` when both exist."""
    repo = tmp_path / "r"
    repo.mkdir()
    _make_layout(repo)
    mapped, force = map_files_to_tests(
        [Path("prefect_orchestration/sub/bar.py")],
        repo,
    )
    assert force is False
    assert Path("tests/sub/test_bar.py") in mapped
    assert Path("tests/test_bar.py") in mapped


def test_changed_test_file_maps_to_itself(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _make_layout(repo)
    mapped, force = map_files_to_tests([Path("tests/test_foo.py")], repo)
    assert force is False
    assert mapped == {Path("tests/test_foo.py")}


def test_unmapped_change_yields_empty_no_tripwire(tmp_path: Path) -> None:
    """A source change with no matching test contributes nothing (smoke covers it)."""
    repo = tmp_path / "r"
    repo.mkdir()
    _make_layout(repo)
    (repo / "prefect_orchestration" / "orphan.py").write_text("")
    mapped, force = map_files_to_tests(
        [Path("prefect_orchestration/orphan.py")],
        repo,
    )
    assert force is False
    assert mapped == set()


def test_non_python_change_contributes_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _make_layout(repo)
    mapped, force = map_files_to_tests([Path("README.md")], repo)
    assert force is False
    assert mapped == set()


@pytest.mark.parametrize("tripwire", list(TRIPWIRES))
def test_tripwire_forces_full(tmp_path: Path, tripwire: str) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _make_layout(repo)
    mapped, force = map_files_to_tests(
        [Path("prefect_orchestration/foo.py"), Path(f"some/dir/{tripwire}")],
        repo,
    )
    assert force is True
    assert mapped == set()


def test_top_level_conftest_is_a_tripwire(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _make_layout(repo)
    _, force = map_files_to_tests([Path("tests/conftest.py")], repo)
    assert force is True


# ─────────────────────── write / read round-trip ─────────────────────


def test_write_and_read_round_trip(tmp_path: Path) -> None:
    run_dir = tmp_path / "rd"
    tests = {Path("tests/test_foo.py"), Path("tests/sub/test_bar.py")}
    write_tests_changed(run_dir, tests, force_full=False)

    paths, force = read_tests_changed(run_dir)
    assert force is False
    # Smoke is merged in, then sorted.
    assert paths is not None
    assert "tests/test_foo.py" in paths
    assert "tests/sub/test_bar.py" in paths
    for smoke in DEFAULT_SMOKE_TESTS:
        assert smoke in paths


def test_write_with_force_full_emits_sentinel(tmp_path: Path) -> None:
    run_dir = tmp_path / "rd"
    artifact = write_tests_changed(run_dir, set(), force_full=True)
    text = artifact.read_text()
    assert FULL_SENTINEL in text
    paths, force = read_tests_changed(run_dir)
    assert force is True
    assert paths is None


def test_read_missing_file_means_force_full(tmp_path: Path) -> None:
    """Missing artifact → caller treats as full suite (safe default)."""
    paths, force = read_tests_changed(tmp_path)
    assert force is True
    assert paths is None


def test_empty_diff_still_includes_smoke(tmp_path: Path) -> None:
    """Empty mapped set still yields the unconditional smoke set (AC #4)."""
    run_dir = tmp_path / "rd"
    write_tests_changed(run_dir, set(), force_full=False)
    paths, force = read_tests_changed(run_dir)
    assert force is False
    assert paths is not None
    assert set(paths) == set(DEFAULT_SMOKE_TESTS)


def test_smoke_override_replaces_default(tmp_path: Path) -> None:
    run_dir = tmp_path / "rd"
    write_tests_changed(
        run_dir,
        {Path("tests/test_foo.py")},
        force_full=False,
        smoke=("tests/test_custom_smoke.py",),
    )
    paths, _ = read_tests_changed(run_dir)
    assert paths is not None
    assert set(paths) == {"tests/test_foo.py", "tests/test_custom_smoke.py"}


def test_artifact_header_records_force_full_flag(tmp_path: Path) -> None:
    run_dir = tmp_path / "rd"
    artifact = write_tests_changed(run_dir, set(), force_full=True)
    text = artifact.read_text()
    assert "force_full=True" in text
    artifact = write_tests_changed(
        run_dir, {Path("tests/test_foo.py")}, force_full=False
    )
    text = artifact.read_text()
    assert "force_full=False" in text
