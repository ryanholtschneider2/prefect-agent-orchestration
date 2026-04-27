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


# ── identity-aware rendering (prefect-orchestration-o2r) ──


def test_no_identity_renders_unchanged(tmp_path: Path) -> None:
    _write(tmp_path / "triager" / "prompt.md", "body {{x}}")
    out = render_template(tmp_path, "triager", x="z")
    assert out == "body z"
    assert "<self>" not in out


def test_identity_self_block_prepended(tmp_path: Path) -> None:
    _write(tmp_path / "triager" / "prompt.md", "body")
    _write(
        tmp_path / "triager" / "identity.toml",
        '[identity]\nname = "tri"\nemail = "t@x"\nslack = "@tri"\nmodel = "opus"\n',
    )
    out = render_template(tmp_path, "triager")
    assert out.startswith("<self>\n")
    assert "You are tri." in out
    assert "email: t@x" in out
    assert "slack: @tri" in out
    assert "mail_agent_name: tri" in out
    assert "model: opus" in out
    # self block ends before prompt body
    assert out.endswith("body")
    assert out.index("</self>") < out.index("body")


def test_rig_overlay_overrides_pack(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    rig = tmp_path / "rig"
    _write(pack / "triager" / "prompt.md", "body")
    _write(
        pack / "triager" / "identity.toml",
        '[identity]\nname = "X"\nemail = "x@x"\nslack = "@x"\n',
    )
    _write(
        rig / ".claude" / "agents" / "triager" / "identity.toml",
        '[identity]\nname = "Y"\n',
    )
    out = render_template(pack, "triager", rig_path=rig)
    assert "You are Y." in out
    assert "You are X." not in out
    # other fields still come from pack (per-field merge)
    assert "email: x@x" in out
    assert "slack: @x" in out


def test_agent_name_var_from_identity(tmp_path: Path) -> None:
    _write(
        tmp_path / "builder" / "prompt.md",
        'register_agent name="{{agent_name}}"',
    )
    _write(
        tmp_path / "builder" / "identity.toml",
        '[identity]\nname = "acquisitions-bot"\n',
    )
    out = render_template(tmp_path, "builder")
    assert 'register_agent name="acquisitions-bot"' in out


def test_caller_var_overrides_identity(tmp_path: Path) -> None:
    _write(tmp_path / "builder" / "prompt.md", "{{agent_name}}")
    _write(
        tmp_path / "builder" / "identity.toml",
        '[identity]\nname = "from-toml"\n',
    )
    out = render_template(tmp_path, "builder", agent_name="from-caller")
    # Caller's kwarg wins for the {{agent_name}} substitution.
    assert out.endswith("from-caller")


def test_smoke_overlay_precedence_in_rendered_prompt(tmp_path: Path) -> None:
    """AC #5: pack ships identity X, rig overlay overrides to Y, prompt shows Y."""
    pack = tmp_path / "pack"
    rig = tmp_path / "rig"
    _write(
        pack / "acquisitions" / "prompt.md",
        'mail_agent_name="{{agent_mail_name}}"',
    )
    _write(
        pack / "acquisitions" / "identity.toml",
        '[identity]\nname = "X-bot"\nemail = "x@nanocorp.example"\n',
    )
    _write(
        rig / ".claude" / "agents" / "acquisitions" / "identity.toml",
        '[identity]\nname = "Y-bot"\n',
    )
    out = render_template(pack, "acquisitions", rig_path=rig)
    # <self> block reflects the rig override
    assert "You are Y-bot." in out
    assert "You are X-bot." not in out
    # mail_agent_name falls back to (overridden) name
    assert 'mail_agent_name="Y-bot"' in out
    # email comes from pack (per-field merge)
    assert "email: x@nanocorp.example" in out
