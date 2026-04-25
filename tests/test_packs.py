"""Unit tests for prefect_orchestration.packs — the pack lifecycle module."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from prefect_orchestration import packs


# ---- source classification & spec disambiguation -----------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("po-formulas-software-dev", "pypi"),
        ("git+https://github.com/org/pack.git", "git"),
        ("git@github.com:org/pack.git", "git"),
        ("https://github.com/org/pack.git", "git"),
        ("https://github.com/org/pack.git@main", "git"),
    ],
)
def test_classify_spec_urls_and_pypi(spec: str, expected: str) -> None:
    assert packs.classify_spec(spec) == expected


def test_classify_spec_path_detects_existing_dir(tmp_path: Path) -> None:
    assert packs.classify_spec(str(tmp_path)) == "path"


def test_classify_spec_missing_path_falls_through_to_pypi(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert packs.classify_spec(str(missing)) == "pypi"


# ---- argv construction -------------------------------------------------------


def test_install_argv_non_editable() -> None:
    argv = packs._install_argv("some-pack", editable=False)
    assert argv == [
        "tool",
        "install",
        "--reinstall",
        packs.CORE_DISTRIBUTION,
        "--with",
        "some-pack",
    ]


def test_install_argv_editable() -> None:
    argv = packs._install_argv("/abs/path", editable=True)
    assert argv[-2:] == ["--with-editable", "/abs/path"]
    assert "--reinstall" in argv
    assert packs.CORE_DISTRIBUTION in argv


# ---- install() ---------------------------------------------------------------


def test_install_invokes_uv_with_pack_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        called.append(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(packs, "_run_uv", fake_run)
    monkeypatch.setattr(packs, "discover_packs", lambda: [])
    packs.install("po-formulas-software-dev")
    assert called == [
        [
            "tool",
            "install",
            "--reinstall",
            packs.CORE_DISTRIBUTION,
            "--with",
            "po-formulas-software-dev",
        ]
    ]


def test_install_local_dir_becomes_editable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: list[list[str]] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        called.append(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(packs, "_run_uv", fake_run)
    monkeypatch.setattr(packs, "discover_packs", lambda: [])
    packs.install(str(tmp_path))
    assert called[0][-2] == "--with-editable"
    assert called[0][-1] == str(tmp_path)


def test_install_rejects_command_collision_with_core_verb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pack registering a `po.commands` entry that shadows a core verb
    (e.g. `run`) must be rejected at install time per principle §4."""

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    bad = packs.PackInfo(
        name="po-formulas-bad",
        version="0.1",
        source="pypi",
        contributions={"po.commands": ["run"]},
    )
    monkeypatch.setattr(packs, "_run_uv", fake_run)
    monkeypatch.setattr(packs, "discover_packs", lambda: [bad])

    with pytest.raises(packs.PackError) as exc_info:
        packs.install("po-formulas-bad")
    msg = str(exc_info.value)
    assert "po-formulas-bad" in msg
    assert "run" in msg
    assert "po uninstall" in msg


def test_install_maps_uv_failure_to_packerror(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, args, stderr="boom")

    monkeypatch.setattr(packs, "_run_uv", fake_run)
    with pytest.raises(packs.PackError) as exc_info:
        packs.install("whatever")
    assert "boom" in str(exc_info.value)
    assert "whatever" in str(exc_info.value)


# ---- uninstall() -------------------------------------------------------------


def test_uninstall_refuses_self() -> None:
    with pytest.raises(packs.PackError) as exc_info:
        packs.uninstall(packs.CORE_DISTRIBUTION)
    msg = str(exc_info.value)
    assert "refusing" in msg
    assert "uv tool uninstall" in msg


def test_uninstall_rebuilds_env_without_target(monkeypatch: pytest.MonkeyPatch) -> None:
    other = packs.PackInfo(
        name="po-formulas-other",
        version="0.1",
        source="pypi",
        contributions={"po.formulas": ["x"]},
    )
    target = packs.PackInfo(
        name="po-formulas-target",
        version="0.1",
        source="pypi",
        contributions={"po.formulas": ["y"]},
    )
    monkeypatch.setattr(packs, "discover_packs", lambda: [other, target])

    called: list[list[str]] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        called.append(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(packs, "_run_uv", fake_run)
    packs.uninstall("po-formulas-target")
    assert called
    argv = called[0]
    assert "po-formulas-other" in argv
    assert "po-formulas-target" not in argv
    assert "--reinstall" in argv


# ---- update() ----------------------------------------------------------------


def test_update_all_reinstalls_each_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    a = packs.PackInfo(
        name="po-formulas-a",
        version="0.1",
        source="pypi",
        contributions={"po.formulas": ["a"]},
    )
    b = packs.PackInfo(
        name="po-formulas-b",
        version="0.2",
        source="editable",
        source_detail="/abs/b",
        contributions={"po.formulas": ["b"]},
    )
    monkeypatch.setattr(packs, "discover_packs", lambda: [a, b])

    called: list[list[str]] = []
    monkeypatch.setattr(
        packs,
        "_run_uv",
        lambda args: (
            called.append(args)
            or subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr=""
            )
        ),
    )
    refreshed = packs.update()
    assert refreshed == ["po-formulas-a", "po-formulas-b"]
    argv = called[0]
    # editable pack uses --with-editable with its path
    i = argv.index("--with-editable")
    assert argv[i + 1] == "/abs/b"
    # pypi pack uses --with name
    i = argv.index("--with")
    assert argv[i + 1] == "po-formulas-a"


def test_update_named_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packs, "discover_packs", lambda: [])
    with pytest.raises(packs.PackError) as exc_info:
        packs.update("po-nope")
    assert "po-nope" in str(exc_info.value)


def test_update_named_one_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    a = packs.PackInfo(
        name="po-formulas-a",
        version="0.1",
        source="pypi",
        contributions={"po.formulas": ["a"]},
    )
    b = packs.PackInfo(
        name="po-formulas-b",
        version="0.2",
        source="pypi",
        contributions={"po.formulas": ["b"]},
    )
    monkeypatch.setattr(packs, "discover_packs", lambda: [a, b])

    called: list[list[str]] = []
    monkeypatch.setattr(
        packs,
        "_run_uv",
        lambda args: (
            called.append(args)
            or subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr=""
            )
        ),
    )
    refreshed = packs.update("po-formulas-b")
    assert refreshed == ["po-formulas-b"]
    argv = called[0]
    assert "po-formulas-b" in argv
    assert "po-formulas-a" not in argv


# ---- find_uv -----------------------------------------------------------------


def test_find_uv_missing_raises_with_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packs.shutil, "which", lambda _name: None)
    with pytest.raises(packs.PackError) as exc_info:
        packs.find_uv()
    assert "astral.sh/uv" in str(exc_info.value)


# ---- discover_packs / source classification ---------------------------------


class _FakeEP:
    def __init__(self, name: str, group: str) -> None:
        self.name = name
        self.group = group


class _FakeDist:
    def __init__(
        self,
        name: str,
        version: str,
        eps: list[_FakeEP],
        direct_url: dict | None = None,
    ) -> None:
        self._name = name
        self._version = version
        self.entry_points = eps
        self._direct_url = direct_url
        self.metadata = {"Name": name, "Version": version}

    @property
    def name(self) -> str:
        return self._name

    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json" and self._direct_url is not None:
            return json.dumps(self._direct_url)
        return None


def test_discover_filters_to_po_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    dists = [
        _FakeDist("po-formulas-x", "0.1", [_FakeEP("f1", "po.formulas")]),
        _FakeDist("unrelated", "1.0", [_FakeEP("console", "console_scripts")]),
        _FakeDist(
            "po-all",
            "0.2",
            [
                _FakeEP("f", "po.formulas"),
                _FakeEP("d", "po.deployments"),
                _FakeEP("c", "po.commands"),
                _FakeEP("k", "po.doctor_checks"),
            ],
        ),
    ]
    monkeypatch.setattr(packs, "distributions", lambda: dists)
    out = packs.discover_packs()
    names = [p.name for p in out]
    assert "po-formulas-x" in names
    assert "po-all" in names
    assert "unrelated" not in names


def test_discover_source_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    dists = [
        _FakeDist(
            "pack-ed",
            "0.1",
            [_FakeEP("f", "po.formulas")],
            direct_url={"url": "file:///abs/path", "dir_info": {"editable": True}},
        ),
        _FakeDist(
            "pack-git",
            "0.1",
            [_FakeEP("f", "po.formulas")],
            direct_url={
                "url": "https://github.com/o/r.git",
                "vcs_info": {"vcs": "git"},
            },
        ),
        _FakeDist("pack-pypi", "0.1", [_FakeEP("f", "po.formulas")], direct_url=None),
    ]
    monkeypatch.setattr(packs, "distributions", lambda: dists)
    by_name = {p.name: p for p in packs.discover_packs()}
    assert by_name["pack-ed"].source == "editable"
    assert by_name["pack-ed"].source_detail == "/abs/path"
    assert by_name["pack-git"].source == "git"
    assert by_name["pack-pypi"].source == "pypi"


def test_discover_includes_core_even_with_no_eps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dists = [_FakeDist(packs.CORE_DISTRIBUTION, "1.0", [])]
    monkeypatch.setattr(packs, "distributions", lambda: dists)
    out = packs.discover_packs()
    assert [p.name for p in out] == [packs.CORE_DISTRIBUTION]


# ---- render_packs_table ------------------------------------------------------


def test_render_packs_table_shows_grouped_contributions() -> None:
    pi = packs.PackInfo(
        name="po-formulas-x",
        version="0.1",
        source="pypi",
        contributions={
            "po.formulas": ["flow-a", "flow-b"],
            "po.commands": ["cmd-x"],
        },
    )
    table = packs.render_packs_table([pi])
    assert "NAME" in table
    assert "CONTRIBUTES" in table
    assert "po-formulas-x" in table
    assert "formulas=flow-a,flow-b" in table
    assert "commands=cmd-x" in table


def test_render_packs_table_empty() -> None:
    assert packs.render_packs_table([]) == "no packs installed."
