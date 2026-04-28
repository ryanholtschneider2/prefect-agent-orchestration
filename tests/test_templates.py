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


def test_rig_path_kwarg_is_also_substitution_var(tmp_path: Path) -> None:
    """Regression: when callers do `render_template(..., **ctx)` with ctx
    containing both `rig_path` (consumed by the keyword-only param for
    identity overlay) AND the prompt text references `{{rig_path}}`,
    the explicit binding stole `rig_path` from `**vars` and the
    substitution failed with 'rig_path was not provided'.

    Fix: render_template echoes `rig_path` back into merged_vars when
    none of the **vars carried it.
    """
    pack = tmp_path / "pack"
    rig = tmp_path / "rig"
    rig.mkdir()
    _write(pack / "triager" / "prompt.md", "rig at: {{rig_path}}")

    # Mirror the formula's call shape: ctx unpacked into **vars where
    # rig_path is one of the entries.
    ctx = {"rig_path": str(rig), "issue_id": "x"}
    out = render_template(
        pack,
        "triager",
        rig_path=rig,
        **{k: v for k, v in ctx.items() if k != "rig_path"},
    )
    assert f"rig at: {rig}" in out

    # Also verify the case where the formula passes rig_path explicitly
    # only (without it being in the ctx-as-vars at all): it should still
    # be available as a substitution var.
    out2 = render_template(pack, "triager", rig_path=rig)
    assert f"rig at: {rig}" in out2


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


# ── per-role memory loader (prefect-orchestration-4xo) ──


def test_memory_block_prepended_when_present(tmp_path: Path) -> None:
    _write(tmp_path / "triager" / "prompt.md", "body")
    _write(tmp_path / "triager" / "memory" / "MEMORY.md", "remember: foo")
    out = render_template(tmp_path, "triager")
    assert out.startswith("<memory>\nremember: foo\n</memory>\n\n")
    assert out.endswith("body")


def test_no_memory_dir_renders_unchanged(tmp_path: Path) -> None:
    _write(tmp_path / "triager" / "prompt.md", "body {{x}}")
    out = render_template(tmp_path, "triager", x="z")
    assert out == "body z"
    assert "<memory>" not in out


def test_empty_memory_file_renders_no_block(tmp_path: Path) -> None:
    _write(tmp_path / "triager" / "prompt.md", "body")
    _write(tmp_path / "triager" / "memory" / "MEMORY.md", "   \n\n")
    out = render_template(tmp_path, "triager")
    assert out == "body"
    assert "<memory>" not in out


def test_memory_block_precedes_self_block(tmp_path: Path) -> None:
    _write(tmp_path / "triager" / "prompt.md", "body")
    _write(
        tmp_path / "triager" / "identity.toml",
        '[identity]\nname = "tri"\nemail = "t@x"\nslack = "@tri"\n',
    )
    _write(tmp_path / "triager" / "memory" / "MEMORY.md", "memo")
    out = render_template(tmp_path, "triager")
    assert out.startswith("<memory>\nmemo\n</memory>\n\n")
    assert out.index("<memory>") < out.index("<self>") < out.index("body")


def test_rig_overlay_memory_overrides_pack_memory(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    rig = tmp_path / "rig"
    _write(pack / "triager" / "prompt.md", "body")
    _write(pack / "triager" / "memory" / "MEMORY.md", "PACK-MEMORY")
    _write(
        rig / ".claude" / "agents" / "triager" / "memory" / "MEMORY.md",
        "RIG-MEMORY",
    )
    out = render_template(pack, "triager", rig_path=rig)
    assert "RIG-MEMORY" in out
    assert "PACK-MEMORY" not in out


def test_memory_content_is_not_substituted(tmp_path: Path) -> None:
    """Verbatim: literal `{{...}}` in MEMORY.md must not raise KeyError."""
    _write(tmp_path / "triager" / "prompt.md", "body")
    _write(
        tmp_path / "triager" / "memory" / "MEMORY.md",
        "note: do not substitute {{notavar}} here",
    )
    out = render_template(tmp_path, "triager")
    assert "{{notavar}}" in out


def test_smoke_second_turn_sees_first_turn_memory(tmp_path: Path) -> None:
    """AC #4: agent writes memory on turn 1, sees it on turn 2."""
    _write(tmp_path / "triager" / "prompt.md", "body")

    # Turn 1: no memory yet.
    first = render_template(tmp_path, "triager")
    assert "<memory>" not in first

    # Agent writes its own MEMORY.md (simulated).
    _write(
        tmp_path / "triager" / "memory" / "MEMORY.md",
        "learned-on-turn-1: the pack ships X",
    )

    # Turn 2: prompt now carries the memory block.
    second = render_template(tmp_path, "triager")
    assert "<memory>" in second
    assert "learned-on-turn-1: the pack ships X" in second
