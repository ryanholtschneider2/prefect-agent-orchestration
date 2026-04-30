"""E2E tests for the `po packs {install,update,uninstall,list}` CLI subcommands.

Invokes the real installed `po` script in a subprocess. Covers the
read-only and dry/error paths so the suite never mutates the user's
real `uv tool` environment. State-changing subcommands (`install`,
`update`, `uninstall`) are exercised through their `--help` output and
through error paths that fail before any `uv tool` call.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _po(
    *args: str, env_overrides: dict[str, str] | None = None, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    po_bin = REPO_ROOT / ".venv" / "bin" / "po"
    if not po_bin.exists():
        pytest.skip(f"po CLI not installed at {po_bin}; run `uv sync` first")
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(po_bin), *args],
        cwd=tempfile.mkdtemp(prefix="po-e2e-packs-"),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def test_packs_group_listed_in_root_help() -> None:
    result = _po("--help")
    assert result.returncode == 0, result.stderr
    assert "packs" in result.stdout, "`packs` group missing from `po --help`"


def test_packs_subcommands_listed_in_packs_help() -> None:
    result = _po("packs", "--help")
    assert result.returncode == 0, result.stderr
    for sub in ("install", "update", "uninstall", "list"):
        assert sub in result.stdout, f"{sub!r} missing from `po packs --help`"


def test_packs_bare_invocation_shows_help() -> None:
    """`po packs` with no subcommand should print help, not crash."""
    result = _po("packs")
    # Typer with no_args_is_help=True returns non-zero exit but emits help.
    combined = result.stdout + result.stderr
    assert "install" in combined
    assert "uninstall" in combined
    assert "Traceback" not in result.stderr


def test_packs_list_enumerates_self_and_software_dev_pack() -> None:
    """`po packs list` must enumerate the editable installs in this dev env.

    The repo's `po` CLI is installed editable, and a sibling pack
    `po-formulas-software-dev` is installed alongside it (per CLAUDE.md
    bootstrap instructions). Both should be visible in the table.
    """
    result = _po("packs", "list")
    assert result.returncode == 0, (
        f"po packs list crashed: stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "Traceback" not in result.stderr
    combined = result.stdout + result.stderr
    assert "prefect-orchestration" in combined
    for col in ("NAME", "VERSION", "SOURCE", "CONTRIBUTES"):
        assert col in combined, (
            f"`po packs list` output missing column {col!r}:\n{combined}"
        )


def test_packs_list_shows_software_dev_contributes_formulas() -> None:
    """The software-dev pack should advertise its formulas in the
    CONTRIBUTES column when installed."""
    result = _po("packs", "list")
    assert result.returncode == 0
    if "po-formulas-software-dev" not in result.stdout:
        pytest.skip("po-formulas-software-dev not installed in this env")
    assert "software-dev-full" in result.stdout, (
        f"software-dev-full formula not advertised by packs list:\n{result.stdout}"
    )


def test_packs_install_help_documents_spec_argument() -> None:
    result = _po("packs", "install", "--help")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "SPEC" in out or "spec" in out
    assert "--editable" in out


def test_packs_update_help_runs_clean() -> None:
    result = _po("packs", "update", "--help")
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr


def test_packs_uninstall_help_runs_clean() -> None:
    result = _po("packs", "uninstall", "--help")
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr


def test_packs_uninstall_self_is_refused() -> None:
    """`po packs uninstall prefect-orchestration` must refuse — the CLI
    explicitly guards against self-uninstall."""
    result = _po("packs", "uninstall", "prefect-orchestration")
    assert result.returncode != 0, (
        f"uninstall of self should fail; got exit 0\nstdout={result.stdout}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert any(
        kw in combined
        for kw in ("self", "refuse", "cannot", "would remove", "not allowed")
    ), f"unexpected refusal message:\n{result.stdout}\n---\n{result.stderr}"


def test_packs_uninstall_unknown_pack_does_not_traceback() -> None:
    """A bogus pack name must not produce a Python traceback. Exit code
    is intentionally not pinned: `uv tool uninstall` of a missing tool
    is a no-op success in some uv versions, and PO's wrapper inherits
    that behavior."""
    result = _po("packs", "uninstall", "definitely-not-a-real-pack-zzz")
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_top_level_install_verb_is_gone() -> None:
    """Old top-level `po install` must no longer exist — moved to `po packs install`."""
    result = _po("install", "--help")
    assert result.returncode != 0, (
        f"`po install` should be gone but exited 0:\n{result.stdout}"
    )


def test_top_level_uninstall_verb_is_gone() -> None:
    result = _po("uninstall", "--help")
    assert result.returncode != 0


def test_top_level_update_verb_is_gone() -> None:
    result = _po("update", "--help")
    assert result.returncode != 0
