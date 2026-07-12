from pathlib import Path

from typer.testing import CliRunner

from prefect_orchestration import cli


def test_tui_forwards_supported_options(monkeypatch, tmp_path: Path) -> None:
    binary = tmp_path / "po-tui"
    binary.write_text("fixture")
    binary.chmod(0o755)

    monkeypatch.setattr(cli.os, "access", lambda *_args: False)
    monkeypatch.setattr(
        cli.shutil, "which", lambda name: str(binary) if name == "po-tui" else None
    )
    invoked: list[object] = []
    monkeypatch.setattr(
        cli.os, "execvp", lambda executable, argv: invoked.extend([executable, argv])
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "tui",
            "--rig-path",
            "/tmp/rig",
            "--prefect-url",
            "http://prefect/api",
            "--refresh-ms",
            "2500",
            "--ascii",
            "--plain",
        ],
    )

    assert result.exit_code == 0
    assert invoked == [
        str(binary),
        [
            str(binary),
            "--rig-path",
            "/tmp/rig",
            "--prefect-url",
            "http://prefect/api",
            "--refresh-ms",
            "2500",
            "--ascii",
            "--plain",
        ],
    ]
