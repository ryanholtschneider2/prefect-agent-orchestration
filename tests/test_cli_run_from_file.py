"""Unit tests for `po run --from-file <scratch.py>` and the scratch loader.

Covers AC1 (loader + CLI dispatch) and AC2 (kwargs parsing parity).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prefect_orchestration import cli, scratch_loader


def _write(tmp_path: Path, body: str, name: str = "scratch.py") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body))
    return path


# ---- scratch_loader --------------------------------------------------------


def test_load_single_flow_auto_detects(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        from prefect import flow

        @flow
        def hello():
            return "hi"
        """,
    )
    flow_obj = scratch_loader.load_flow_from_file(path)
    assert flow_obj.name == "hello"
    assert flow_obj() == "hi"


def test_load_multi_flow_requires_name(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        from prefect import flow

        @flow
        def a():
            return 1

        @flow
        def b():
            return 2
        """,
        name="multi.py",
    )
    with pytest.raises(scratch_loader.ScratchLoadError) as exc_info:
        scratch_loader.load_flow_from_file(path)
    msg = str(exc_info.value)
    assert "a" in msg and "b" in msg and "--name" in msg


def test_load_picks_named_flow(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        from prefect import flow

        @flow
        def alpha():
            return "A"

        @flow
        def beta():
            return "B"
        """,
        name="picked.py",
    )
    flow_obj = scratch_loader.load_flow_from_file(path, name="beta")
    assert flow_obj() == "B"


def test_load_unknown_name_lists_candidates(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        from prefect import flow

        @flow
        def only_one():
            return 0
        """,
        name="one.py",
    )
    with pytest.raises(scratch_loader.ScratchLoadError) as exc_info:
        scratch_loader.load_flow_from_file(path, name="missing")
    assert "only_one" in str(exc_info.value)


def test_load_no_flow_at_all(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        x = 1
        def not_a_flow():
            return x
        """,
        name="empty.py",
    )
    with pytest.raises(scratch_loader.ScratchLoadError) as exc_info:
        scratch_loader.load_flow_from_file(path)
    assert "no Prefect @flow" in str(exc_info.value)


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(scratch_loader.ScratchLoadError) as exc_info:
        scratch_loader.load_flow_from_file(tmp_path / "nope.py")
    assert "no such file" in str(exc_info.value)


def test_load_rejects_non_py(tmp_path: Path) -> None:
    bad = tmp_path / "x.txt"
    bad.write_text("hi")
    with pytest.raises(scratch_loader.ScratchLoadError) as exc_info:
        scratch_loader.load_flow_from_file(bad)
    assert ".py" in str(exc_info.value)


def test_load_idempotent_for_same_path(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        from prefect import flow

        @flow
        def once():
            return "x"
        """,
        name="idem.py",
    )
    f1 = scratch_loader.load_flow_from_file(path)
    f2 = scratch_loader.load_flow_from_file(path)
    assert f1 is f2  # cached module → same flow object


def test_load_failed_import_does_not_pollute_sys_modules(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        raise RuntimeError("boom")
        """,
        name="boom.py",
    )
    before = set(sys.modules)
    with pytest.raises(RuntimeError):
        scratch_loader.load_flow_from_file(path)
    leaked = {m for m in sys.modules if m.startswith("po_scratch_")} - {
        m for m in before if m.startswith("po_scratch_")
    }
    assert not leaked


# ---- CLI wiring ------------------------------------------------------------


def test_cli_run_from_file_invokes_flow(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        from prefect import flow

        @flow
        def hello():
            return "hi-from-cli"
        """,
        name="cli_hello.py",
    )
    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--from-file", str(path)])
    assert result.exit_code == 0, result.output
    assert "hi-from-cli" in result.output


def test_cli_run_from_file_passes_kwargs(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        from prefect import flow

        @flow
        def add(a: int = 0, b: int = 0):
            return a + b
        """,
        name="cli_add.py",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["run", "--from-file", str(path), "--a", "2", "--b", "3"]
    )
    assert result.exit_code == 0, result.output
    assert "5" in result.output


def test_cli_run_from_file_with_name_selects_flow(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        from prefect import flow

        @flow
        def alpha():
            return "A"

        @flow
        def beta():
            return "B"
        """,
        name="cli_picked.py",
    )
    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--from-file", str(path), "--name", "beta"])
    assert result.exit_code == 0, result.output
    assert "B" in result.output


def test_cli_run_rejects_both_name_and_from_file(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        from prefect import flow

        @flow
        def f():
            return 1
        """,
        name="conflict.py",
    )
    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "some-formula", "--from-file", str(path)])
    assert result.exit_code == 2
    assert "not both" in result.output


def test_cli_run_missing_name_and_from_file(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["run"])
    assert result.exit_code == 2
    assert "missing formula name" in result.output or "--from-file" in result.output


def test_cli_run_from_file_bad_path(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--from-file", str(tmp_path / "nope.py")])
    assert result.exit_code == 2
    assert "no such file" in result.output
