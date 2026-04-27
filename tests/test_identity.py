"""Unit tests for `prefect_orchestration.identity`."""

from __future__ import annotations

from pathlib import Path

import pytest

from prefect_orchestration.identity import (
    Identity,
    IdentityLoadError,
    format_self_block,
    identity_vars,
    load_identity,
)


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_load_returns_none_when_no_files(tmp_path: Path) -> None:
    assert load_identity(tmp_path, "triager") is None
    assert load_identity(tmp_path, "triager", rig_path=tmp_path) is None


def test_load_pack_only(tmp_path: Path) -> None:
    _write(
        tmp_path / "triager" / "identity.toml",
        '[identity]\nname = "tri"\nemail = "t@x"\n',
    )
    ident = load_identity(tmp_path, "triager")
    assert ident == Identity(name="tri", email="t@x")


def test_rig_overlay_per_field_merge(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    rig = tmp_path / "rig"
    _write(
        pack / "triager" / "identity.toml",
        '[identity]\nname = "X"\nemail = "x@x"\nslack = "@x"\n',
    )
    _write(
        rig / ".claude" / "agents" / "triager" / "identity.toml",
        '[identity]\nname = "Y"\n',
    )
    ident = load_identity(pack, "triager", rig_path=rig)
    assert ident == Identity(name="Y", email="x@x", slack="@x")


def test_rig_only_no_pack(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    rig = tmp_path / "rig"
    _write(
        rig / ".claude" / "agents" / "builder" / "identity.toml",
        '[identity]\nname = "rig-only"\n',
    )
    ident = load_identity(pack, "builder", rig_path=rig)
    assert ident == Identity(name="rig-only")


def test_malformed_toml_raises(tmp_path: Path) -> None:
    _write(tmp_path / "r" / "identity.toml", "not = valid = toml")
    with pytest.raises(IdentityLoadError):
        load_identity(tmp_path, "r")


def test_non_string_field_raises(tmp_path: Path) -> None:
    _write(tmp_path / "r" / "identity.toml", "[identity]\nname = 7\n")
    with pytest.raises(IdentityLoadError):
        load_identity(tmp_path, "r")


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    _write(
        tmp_path / "r" / "identity.toml",
        '[identity]\nname = "x"\nbogus = "ignored"\n',
    )
    assert load_identity(tmp_path, "r") == Identity(name="x")


def test_mail_agent_name_falls_back_to_name() -> None:
    ident = Identity(name="bot")
    assert ident.effective_mail_agent_name == "bot"
    ident2 = Identity(name="bot", mail_agent_name="bot-mail")
    assert ident2.effective_mail_agent_name == "bot-mail"


def test_format_self_block_emits_present_only() -> None:
    ident = Identity(name="bot", email="b@x")
    out = format_self_block(ident)
    assert out.startswith("<self>\n")
    assert "You are bot." in out
    assert "email: b@x" in out
    assert "slack:" not in out
    assert "model:" not in out
    assert out.endswith("</self>\n\n")


def test_format_self_block_includes_mail_fallback() -> None:
    ident = Identity(name="bot")
    out = format_self_block(ident)
    assert "mail_agent_name: bot" in out


def test_format_self_block_empty() -> None:
    assert format_self_block(Identity()) == ""


def test_identity_vars_filters_none() -> None:
    ident = Identity(name="bot", email=None, slack="@b")
    vars_ = identity_vars(ident)
    assert vars_ == {
        "agent_name": "bot",
        "agent_slack": "@b",
        "agent_mail_name": "bot",
    }


def test_identity_vars_none_input() -> None:
    assert identity_vars(None) == {}
