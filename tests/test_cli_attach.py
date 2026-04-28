"""Unit tests for the `po attach` Typer command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prefect_orchestration import attach, cli, run_lookup


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed(tmp_path: Path, *, roles: list[str]) -> run_lookup.RunLocation:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    meta = {f"session_{r}": f"uuid-{r}" for r in roles}
    (run_dir / "metadata.json").write_text(json.dumps(meta))
    return run_lookup.RunLocation(rig_path=tmp_path, run_dir=run_dir)


def _patch(monkeypatch, loc: run_lookup.RunLocation, bead_meta: dict[str, str]):
    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", lambda _id: loc)
    monkeypatch.setattr(cli._attach, "fetch_bead_metadata", lambda _id: bead_meta)


def test_attach_prints_argv_local(tmp_path, runner, monkeypatch):
    loc = _seed(tmp_path, roles=["builder"])
    _patch(monkeypatch, loc, bead_meta={})

    result = runner.invoke(cli.app, ["attach", "issue", "--print-argv"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "tmux attach -t po-issue-builder"


def test_attach_prints_argv_kubectl(tmp_path, runner, monkeypatch):
    loc = _seed(tmp_path, roles=["builder"])
    _patch(
        monkeypatch,
        loc,
        bead_meta={
            attach.META_K8S_POD: "po-worker-abc",
            attach.META_K8S_NAMESPACE: "po-system",
            attach.META_K8S_CONTEXT: "prod-east",
        },
    )
    monkeypatch.setattr(cli._attach, "probe_pod", lambda _t: ("running", "Running"))

    result = runner.invoke(cli.app, ["attach", "issue", "--print-argv"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == (
        "kubectl --context prod-east -n po-system "
        "exec -it po-worker-abc -- tmux attach -t po-issue-builder"
    )


def test_attach_role_flag_selects(tmp_path, runner, monkeypatch):
    loc = _seed(tmp_path, roles=["builder", "critic", "verifier"])
    _patch(monkeypatch, loc, bead_meta={})

    result = runner.invoke(
        cli.app, ["attach", "issue", "--role", "builder", "--print-argv"]
    )
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "tmux attach -t po-issue-builder"


def test_attach_role_unknown(tmp_path, runner, monkeypatch):
    loc = _seed(tmp_path, roles=["builder"])
    _patch(monkeypatch, loc, bead_meta={})

    result = runner.invoke(
        cli.app, ["attach", "issue", "--role", "ghost", "--print-argv"]
    )
    assert result.exit_code == 4
    assert "unknown role" in result.stderr


def test_attach_multiple_roles_non_tty_lists_and_exits(tmp_path, runner, monkeypatch):
    loc = _seed(tmp_path, roles=["builder", "critic"])
    _patch(monkeypatch, loc, bead_meta={})

    # CliRunner stdin is non-TTY by default.
    result = runner.invoke(cli.app, ["attach", "issue", "--print-argv"])
    assert result.exit_code == 5
    assert "specify --role" in result.stderr
    assert "builder" in result.stderr and "critic" in result.stderr


def test_attach_list_only(tmp_path, runner, monkeypatch):
    loc = _seed(tmp_path, roles=["builder", "critic"])
    _patch(
        monkeypatch,
        loc,
        bead_meta={
            attach.META_K8S_POD: "p",
            attach.META_K8S_NAMESPACE: "ns",
            attach.META_K8S_CONTEXT: "ctx",
        },
    )

    result = runner.invoke(cli.app, ["attach", "issue", "--list"])
    assert result.exit_code == 0, result.stderr
    out = result.stdout
    assert "builder" in out and "critic" in out
    assert "k8s" in out
    assert "pod=p" in out


def test_attach_pod_gone(tmp_path, runner, monkeypatch):
    loc = _seed(tmp_path, roles=["builder"])
    _patch(
        monkeypatch,
        loc,
        bead_meta={
            attach.META_K8S_POD: "ghost-pod",
            attach.META_K8S_NAMESPACE: "ns",
            attach.META_K8S_CONTEXT: "ctx",
        },
    )
    monkeypatch.setattr(cli._attach, "probe_pod", lambda _t: ("gone", "pod NotFound"))

    result = runner.invoke(cli.app, ["attach", "issue", "--print-argv"])
    assert result.exit_code == 6
    assert "pod gone" in result.stderr
    assert "ghost-pod" in result.stderr
    assert "po retry" in result.stderr


def test_attach_forbidden(tmp_path, runner, monkeypatch):
    loc = _seed(tmp_path, roles=["builder"])
    _patch(
        monkeypatch,
        loc,
        bead_meta={
            attach.META_K8S_POD: "p",
            attach.META_K8S_NAMESPACE: "ns",
            attach.META_K8S_CONTEXT: "ctx",
        },
    )
    monkeypatch.setattr(
        cli._attach, "probe_pod", lambda _t: ("forbidden", "RBAC: forbidden")
    )

    result = runner.invoke(cli.app, ["attach", "issue", "--print-argv"])
    assert result.exit_code == 7
    assert "RBAC" in result.stderr
    assert "ns" in result.stderr


def test_attach_warns_when_context_missing(tmp_path, runner, monkeypatch):
    loc = _seed(tmp_path, roles=["builder"])
    _patch(
        monkeypatch,
        loc,
        bead_meta={
            attach.META_K8S_POD: "p",
            attach.META_K8S_NAMESPACE: "ns",
            # no context
        },
    )
    monkeypatch.setattr(cli._attach, "probe_pod", lambda _t: ("running", "Running"))

    result = runner.invoke(cli.app, ["attach", "issue", "--print-argv"])
    assert result.exit_code == 0, result.stderr
    assert "PO_KUBE_CONTEXT" in result.stderr
    # No --context in argv
    assert "--context" not in result.stdout


def test_attach_no_roles(tmp_path, runner, monkeypatch):
    loc = _seed(tmp_path, roles=[])
    _patch(monkeypatch, loc, bead_meta={})

    result = runner.invoke(cli.app, ["attach", "issue", "--print-argv"])
    assert result.exit_code == 3
    assert "no roles found" in result.stderr


def test_attach_run_dir_not_found(runner, monkeypatch):
    def raiser(_id):
        raise run_lookup.RunDirNotFound("nope")

    monkeypatch.setattr(cli._run_lookup, "resolve_run_dir", raiser)
    result = runner.invoke(cli.app, ["attach", "issue", "--print-argv"])
    assert result.exit_code == 2
    assert "nope" in result.stderr
