"""Unit tests for `prefect_orchestration.templates.render_template`.

Validates the `agents/<role>/prompt.md` layout introduced by
prefect-orchestration-4ja.3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prefect_orchestration.templates import render_template


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_renders_role_prompt(tmp_path: Path) -> None:
    _write(tmp_path / "triager" / "prompt.md", "issue: {{issue_id}}")
    assert render_template(tmp_path, "triager", issue_id="abc") == "issue: abc"


def test_hyphenated_role(tmp_path: Path) -> None:
    _write(tmp_path / "plan-critic" / "prompt.md", "iter {{n}}")
    assert render_template(tmp_path, "plan-critic", n=3) == "iter 3"


def test_missing_role_raises_filenotfound_with_role_name(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as exc:
        render_template(tmp_path, "nope")
    assert "nope" in str(exc.value)


def test_missing_var_raises_keyerror(tmp_path: Path) -> None:
    _write(tmp_path / "triager" / "prompt.md", "{{missing}}")
    with pytest.raises(KeyError) as exc:
        render_template(tmp_path, "triager")
    assert "missing" in str(exc.value)


def test_substitutes_multiple_vars(tmp_path: Path) -> None:
    _write(tmp_path / "builder" / "prompt.md", "{{a}}-{{b}}-{{a}}")
    assert render_template(tmp_path, "builder", a="x", b="y") == "x-y-x"
