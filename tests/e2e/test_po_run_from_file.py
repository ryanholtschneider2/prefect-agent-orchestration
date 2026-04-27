"""E2E test for `po run --from-file <scratch.py>`.

Exercises the real installed `po` binary on a scratch flow file. AC3
(Prefect UI shows the run normally) is covered indirectly: when
PREFECT_API_URL points at a reachable server, `po run` registers the
flow run with the API. With the bogus default URL set by `po_runner`,
Prefect falls back to local execution and still produces a return value.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Callable

import subprocess


def test_po_run_from_file_returns_flow_value(
    po_runner: Callable[..., subprocess.CompletedProcess[str]],
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "hello.py"
    scratch.write_text(
        textwrap.dedent(
            """
            from prefect import flow

            @flow
            def hello():
                return "scratch-roundtrip"
            """
        )
    )
    result = po_runner("run", "--from-file", str(scratch))
    assert result.returncode == 0, result.stderr
    assert "scratch-roundtrip" in result.stdout


def test_po_run_from_file_with_kwargs(
    po_runner: Callable[..., subprocess.CompletedProcess[str]],
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "add.py"
    scratch.write_text(
        textwrap.dedent(
            """
            from prefect import flow

            @flow
            def add(a: int = 0, b: int = 0):
                return a + b
            """
        )
    )
    result = po_runner("run", "--from-file", str(scratch), "--a", "4", "--b", "5")
    assert result.returncode == 0, result.stderr
    assert "9" in result.stdout


def test_po_run_from_file_missing_path(
    po_runner: Callable[..., subprocess.CompletedProcess[str]],
    tmp_path: Path,
) -> None:
    result = po_runner("run", "--from-file", str(tmp_path / "nope.py"))
    assert result.returncode == 2
    assert "no such file" in result.stderr
