"""Real PTY lifecycle smoke for the compiled po-tui binary."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

ALT_ON = b"\x1b[?1049h"
ALT_OFF = b"\x1b[?1049l"
CURSOR_ON = b"\x1b[?25h"
BINARY = Path(__file__).resolve().parents[1] / "dist" / "po-tui"


def drain(fd: int, seconds: float = 0.5) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.05)
        if not readable:
            continue
        try:
            output.extend(os.read(fd, 65536))
        except OSError:
            break
    return bytes(output)


def run_case(name: str, action: str, env_extra: dict[str, str] | None = None) -> None:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    env = os.environ.copy()
    env.pop("BEADS_DIR", None)
    env.update(env_extra or {})
    process = subprocess.Popen(
        [str(BINARY), "--rig-path", str(BINARY.parents[2]), "--refresh-ms", "5000"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        start_new_session=True,
    )
    os.close(slave)
    output = drain(master, 0.4)
    if action == "quit":
        os.write(master, b"q")
    elif action == "resize":
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        os.killpg(process.pid, signal.SIGWINCH)
        os.write(master, b"q")
    elif action == "suspend":
        os.killpg(process.pid, signal.SIGTSTP)
        time.sleep(0.1)
        os.killpg(process.pid, signal.SIGCONT)
        time.sleep(0.1)
        os.write(master, b"q")
    elif action == "sigint":
        os.killpg(process.pid, signal.SIGINT)
    elif action == "sigterm":
        os.killpg(process.pid, signal.SIGTERM)
    output += drain(master, 1.0)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        raise AssertionError(f"{name}: process did not exit")
    os.close(master)
    assert ALT_ON in output, f"{name}: alternate screen not entered"
    assert ALT_OFF in output, f"{name}: alternate screen not restored"
    assert CURSOR_ON in output, f"{name}: cursor not restored"
    print(f"PASS {name}: exit={process.returncode} bytes={len(output)}")


def main() -> None:
    if not BINARY.exists():
        raise SystemExit("build first: bun run build")
    run_case("normal-quit", "quit")
    run_case("resize", "resize")
    run_case("sigint", "sigint")
    run_case("sigterm", "sigterm")
    run_case("suspend-resume", "suspend")
    run_case("uncaught-exception", "wait", {"PO_TUI_TEST_FAILURE": "throw"})
    run_case("unhandled-rejection", "wait", {"PO_TUI_TEST_FAILURE": "reject"})
    run_case("attach-handoff", "wait", {"PO_TUI_TEST_ATTACH_TARGET": "po-fixture-builder"})
    plain = subprocess.run([str(BINARY), "--plain", "--rig-path", str(BINARY.parents[2])], capture_output=True, check=True)
    assert ALT_ON not in plain.stdout and b"PO operations" in plain.stdout
    print("PASS non-tty-plain")


if __name__ == "__main__":
    main()
