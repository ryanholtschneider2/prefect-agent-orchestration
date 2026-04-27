"""Path-aware regression-gate helpers.

Maps a build's git diff to the test files reachable from it (via stem
heuristics), plus an unconditional smoke set, so `regression_gate` can
run a scoped pytest invocation instead of the full suite. Detects
"tripwire" changes (conftest.py, pyproject.toml, uv.lock, …) that
invalidate the heuristic and force a full-suite fallback.

Public surface:

    compute_changed_files(repo_path, base_ref="origin/main") -> list[Path]
    map_files_to_tests(changed, repo_root, *, test_root=Path("tests"))
        -> tuple[set[Path], bool]
    write_tests_changed(run_dir, tests, *, force_full, smoke=...) -> Path
    read_tests_changed(run_dir) -> tuple[list[str] | None, bool]

Deterministic, no LLM, stdlib only.
"""

from __future__ import annotations

import datetime as _dt
import subprocess
from collections.abc import Iterable
from pathlib import Path

# Files whose change invalidates the stem-based mapping entirely; the
# safe answer is to fall back to the full suite. Any path whose final
# component matches an entry here counts.
TRIPWIRES: tuple[str, ...] = (
    "conftest.py",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "bun.lockb",
    ".po-env",
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    "noxfile.py",
)

# Cheap, broadly-importing tests that are run unconditionally so
# cross-cutting breakage still surfaces. Tuned for the
# `prefect-orchestration` rig; rigs with different layouts can
# override via the `smoke=` kwarg on `write_tests_changed`.
DEFAULT_SMOKE_TESTS: tuple[str, ...] = (
    "tests/test_doctor.py",
    "tests/test_packs.py",
    "tests/test_role_registry.py",
)

# Sentinel written to `tests-changed.txt` when the artifact represents
# "force the full suite" (tripwire change OR `force_full_regression`
# flag). The role prompt greps for `^__FULL__` to detect it.
FULL_SENTINEL = "__FULL__"

_ARTIFACT_NAME = "tests-changed.txt"


def compute_changed_files(
    repo_path: Path,
    base_ref: str = "origin/main",
) -> list[Path]:
    """Return paths changed between `merge-base(base_ref, HEAD)` and HEAD.

    Uses `git merge-base` so multi-commit actor-critic branches see
    the full delta (not just `HEAD~1..HEAD`, which only captures the
    last commit). Falls back to `HEAD~1..HEAD` if `base_ref` is
    unknown — common on rigs with no remote configured.

    Returned paths are relative to the repo root, as `Path` instances.
    Empty list on any git failure (caller treats this as "no targeted
    tests"; the smoke set still runs).
    """
    repo_path = Path(repo_path)
    base = _resolve_base(repo_path, base_ref)
    if base is None:
        diff_arg = "HEAD~1..HEAD"
    else:
        diff_arg = f"{base}..HEAD"
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", diff_arg],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if out.returncode != 0:
        return []
    return [Path(line) for line in out.stdout.splitlines() if line.strip()]


def _resolve_base(repo_path: Path, base_ref: str) -> str | None:
    """Return the merge-base SHA of `base_ref` and HEAD, or None."""
    try:
        out = subprocess.run(
            ["git", "merge-base", base_ref, "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if out.returncode != 0:
        return None
    sha = out.stdout.strip()
    return sha or None


def map_files_to_tests(
    changed: Iterable[Path],
    repo_root: Path,
    *,
    test_root: Path = Path("tests"),
) -> tuple[set[Path], bool]:
    """Map source-file changes to test files via stem heuristics.

    Heuristics, applied in order to each changed path:

      • path's basename matches a TRIPWIRE → return (set(), True)
        immediately; caller MUST run the full suite.
      • path is itself a test file (under `test_root` and matches
        `test_*.py`) → include it directly.
      • path is a non-test `.py` file → look for
        `<test_root>/<rest>/test_<stem>.py` and
        `<test_root>/test_<stem>.py`; include any that exist.
      • anything else → no contribution. The smoke set is the safety
        net.

    Returns `(mapped_test_paths_relative_to_repo_root, force_full)`.
    """
    repo_root = Path(repo_root)
    mapped: set[Path] = set()
    for raw in changed:
        rel = Path(raw)
        if rel.name in TRIPWIRES:
            return set(), True
        if _is_test_file(rel, test_root):
            if (repo_root / rel).is_file():
                mapped.add(rel)
            continue
        if rel.suffix != ".py":
            # Non-Python source changes (md, yaml, …) don't map; smoke
            # covers cross-cutting risk.
            continue
        mapped.update(_candidate_tests_for(rel, repo_root, test_root))
    return mapped, False


def _is_test_file(rel: Path, test_root: Path) -> bool:
    """True iff `rel` is under `test_root` and looks like `test_*.py`."""
    try:
        rel.relative_to(test_root)
    except ValueError:
        return False
    return rel.suffix == ".py" and rel.name.startswith("test_")


def _candidate_tests_for(
    rel: Path,
    repo_root: Path,
    test_root: Path,
) -> set[Path]:
    """Test files plausibly covering the changed source `rel`.

    Tries, in order:
      • `<test_root>/<rel.parent without first segment>/test_<stem>.py`
        — preserves package layout under tests/.
      • `<test_root>/test_<stem>.py`
        — flat fallback (this rig's actual layout).
    """
    stem = rel.stem
    test_name = f"test_{stem}.py"
    out: set[Path] = set()
    # `prefect_orchestration/foo/bar.py` → `tests/foo/test_bar.py`
    parts = rel.parts
    if len(parts) >= 2:
        sub = Path(*parts[1:-1]) if len(parts) > 2 else Path()
        nested = test_root / sub / test_name
        if (repo_root / nested).is_file():
            out.add(nested)
    # `prefect_orchestration/foo.py` → `tests/test_foo.py`
    flat = test_root / test_name
    if (repo_root / flat).is_file():
        out.add(flat)
    return out


def write_tests_changed(
    run_dir: Path,
    tests: set[Path],
    *,
    force_full: bool,
    smoke: Iterable[str] = DEFAULT_SMOKE_TESTS,
    base_ref: str = "origin/main",
    n_changed: int | None = None,
) -> Path:
    """Write `tests-changed.txt` to run_dir. Return the file path.

    File layout (header lines start with `#`, blanks ignored):

        # Generated by prefect_orchestration.diff_mapper @ <ISO ts>
        # base_ref=<ref>  changed_files=<n>  force_full=<bool>
        __FULL__                  ← only when force_full is True
        tests/<...>.py            ← otherwise: smoke ∪ mapped, sorted
        ...
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    artifact = run_dir / _ARTIFACT_NAME
    now = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
    n = n_changed if n_changed is not None else len(tests)
    header = (
        f"# Generated by prefect_orchestration.diff_mapper @ {now}\n"
        f"# base_ref={base_ref}  changed_files={n}  force_full={force_full}\n"
    )
    if force_full:
        artifact.write_text(header + FULL_SENTINEL + "\n")
        return artifact
    merged = sorted({str(Path(t)) for t in tests} | {str(Path(s)) for s in smoke})
    body = "\n".join(merged)
    artifact.write_text(header + body + ("\n" if body else ""))
    return artifact


def read_tests_changed(run_dir: Path) -> tuple[list[str] | None, bool]:
    """Read `tests-changed.txt`. Return `(paths, force_full)`.

    `(None, True)` → file missing OR `__FULL__` sentinel present
    (caller runs the full suite).
    `([...], False)` → list of test paths to run; smoke is already
    merged in.
    """
    artifact = Path(run_dir) / _ARTIFACT_NAME
    if not artifact.is_file():
        return None, True
    paths: list[str] = []
    for line in artifact.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s == FULL_SENTINEL:
            return None, True
        paths.append(s)
    return paths, False


__all__ = [
    "DEFAULT_SMOKE_TESTS",
    "FULL_SENTINEL",
    "TRIPWIRES",
    "compute_changed_files",
    "map_files_to_tests",
    "read_tests_changed",
    "write_tests_changed",
]
