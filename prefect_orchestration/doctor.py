"""`po doctor` — read-only health check of the PO wiring.

Runs a fixed list of independent checks (`bd` CLI reachable, Prefect
server reachable, at least one work pool, formula entry points load,
deployment register() callables load, uv-tool install fresh, LOGFIRE
telemetry token present) and returns structured `CheckResult`s. The
CLI renders them as a table and exits non-zero only when a `FAIL`
(critical) check is present — `WARN`s never affect the exit code.

Pure introspection: no disk writes, no Prefect API mutations. Only
GETs, env reads, and `importlib.metadata` introspection.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from importlib.metadata import entry_points
from typing import Callable

from prefect_orchestration import deployments as _deployments


class Status(str, Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    status: Status
    message: str
    remediation: str = ""


# -- individual checks --------------------------------------------------


def check_bd_on_path() -> CheckResult:
    """`bd` binary on PATH and runnable."""
    name = "bd on PATH"
    path = shutil.which("bd")
    if not path:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message="not found on PATH",
            remediation="install beads: see https://github.com/steveyegge/beads",
        )
    try:
        proc = subprocess.run(
            ["bd", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message="`bd --version` timed out after 5s",
            remediation="check your beads install for a hang",
        )
    if proc.returncode != 0:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message=f"`bd --version` exited {proc.returncode}",
            remediation="reinstall beads",
        )
    return CheckResult(
        name=name, status=Status.OK, message=(proc.stdout or "").strip() or path
    )


def check_prefect_api_reachable() -> CheckResult:
    """PREFECT_API_URL set AND server responds."""
    name = "Prefect API reachable"
    url = os.environ.get("PREFECT_API_URL")
    if not url:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message="PREFECT_API_URL not set",
            remediation="start a server (`prefect server start`) and export PREFECT_API_URL=http://127.0.0.1:4200/api",
        )
    try:
        # Lazy import — keeps `po --help` snappy.
        from prefect.client.orchestration import get_client

        async def _probe() -> None:
            async with get_client() as client:
                # `hello()` is the simplest GET-shaped health probe across
                # Prefect 3.x. If it's removed in a future release, swap
                # for `api_healthcheck()` or a bare httpx GET of /health.
                await client.hello()

        asyncio.run(asyncio.wait_for(_probe(), timeout=5.0))
    except Exception as exc:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message=f"cannot reach {url}: {exc}",
            remediation="ensure a Prefect server is running at PREFECT_API_URL",
        )
    return CheckResult(name=name, status=Status.OK, message=url)


def check_work_pool_exists() -> CheckResult:
    """At least one work pool registered on the server."""
    name = "Work pool exists"
    if not os.environ.get("PREFECT_API_URL"):
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message="skipped — Prefect API unreachable",
            remediation="fix the Prefect API reachable check first",
        )
    try:
        from prefect.client.orchestration import get_client

        async def _list() -> list:
            async with get_client() as client:
                return list(await client.read_work_pools())

        pools = asyncio.run(asyncio.wait_for(_list(), timeout=5.0))
    except Exception as exc:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message=f"read_work_pools() failed: {exc}",
            remediation="check Prefect server health",
        )
    if not pools:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message="no work pools registered",
            remediation="prefect work-pool create po --type process",
        )
    names = ", ".join(sorted(getattr(p, "name", "?") for p in pools))
    return CheckResult(
        name=name, status=Status.OK, message=f"{len(pools)} pool(s): {names}"
    )


def _iter_formula_eps() -> list:
    try:
        return list(entry_points(group="po.formulas"))
    except TypeError:
        return list(entry_points().get("po.formulas", []))  # type: ignore[attr-defined]


def check_formulas_load() -> CheckResult:
    """Every `po.formulas` entry point resolves."""
    name = "Formulas load"
    eps = _iter_formula_eps()
    if not eps:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message="no formulas registered",
            remediation="install a pack (e.g. `uv add po-formulas-software-dev`)",
        )
    failures: list[str] = []
    for ep in eps:
        try:
            ep.load()
        except Exception as exc:
            failures.append(f"{ep.name}: {exc}")
    if failures:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message="; ".join(failures),
            remediation="fix or reinstall the offending pack",
        )
    return CheckResult(name=name, status=Status.OK, message=f"{len(eps)} formula(s)")


def check_deployments_load() -> CheckResult:
    """Every `po.deployments` register() callable loads without error."""
    name = "Deployments load"
    try:
        loaded, errors = _deployments.load_deployments()
    except Exception as exc:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message=f"load_deployments() crashed: {exc}",
            remediation="check `po deploy` for a traceback",
        )
    if errors:
        joined = "; ".join(f"{e.pack}: {e.error}" for e in errors)
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message=joined,
            remediation="fix the offending pack's register() callable",
        )
    return CheckResult(
        name=name, status=Status.OK, message=f"{len(loaded)} deployment(s)"
    )


def check_po_list_nonempty() -> CheckResult:
    """`po list` would return at least one formula."""
    name = "po list non-empty"
    count = len(_iter_formula_eps())
    if count == 0:
        return CheckResult(
            name=name,
            status=Status.FAIL,
            message="`po list` would be empty",
            remediation="install a formula pack (`uv add po-formulas-software-dev`)",
        )
    return CheckResult(name=name, status=Status.OK, message=f"{count} formula(s)")


def check_uv_tool_fresh() -> CheckResult:
    """Cross-check: in-process entry points match the `po` binary's.

    Detects the common failure mode where the `po` binary on PATH points
    at a different uv-tool install than the Python process that's
    executing doctor — i.e. entry-point metadata the user sees via
    `po list` doesn't match what this process knows about.
    """
    name = "uv-tool install fresh"
    in_proc = {ep.name for ep in _iter_formula_eps()}
    po_bin = shutil.which("po")
    if not po_bin:
        return CheckResult(
            name=name,
            status=Status.WARN,
            message="`po` binary not on PATH — skipped",
            remediation="uv tool install --force --editable .",
        )
    try:
        proc = subprocess.run(
            [po_bin, "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name=name,
            status=Status.WARN,
            message="`po list` timed out",
            remediation="uv tool install --force --editable .",
        )
    if proc.returncode != 0:
        return CheckResult(
            name=name,
            status=Status.WARN,
            message=f"`po list` exited {proc.returncode}",
            remediation="uv tool install --force --editable .",
        )
    # Parse the first whitespace token of each non-indented line-pair.
    # `po list` output shape: "  <name>  <module>:<fn>" followed by a
    # continuation doc line. We want the leftmost token of lines that
    # start with whitespace + alphanum.
    seen: set[str] = set()
    for raw in proc.stdout.splitlines():
        stripped = raw.strip()
        if not stripped or ":" not in stripped:
            continue
        first = stripped.split()[0]
        # Heuristic: names are identifiers or hyphenated slugs; module
        # paths contain a dot before the colon. Discard the latter.
        if "." in first.split(":", 1)[0]:
            continue
        seen.add(first)
    if not seen:
        # Empty `po list` is the nonempty check's job, not this one.
        return CheckResult(
            name=name, status=Status.OK, message="no formulas to cross-check"
        )
    if seen != in_proc:
        missing = sorted(in_proc - seen)
        extra = sorted(seen - in_proc)
        parts = []
        if missing:
            parts.append(f"missing from `po list`: {missing}")
        if extra:
            parts.append(f"only in `po list`: {extra}")
        return CheckResult(
            name=name,
            status=Status.WARN,
            message="; ".join(parts),
            remediation="uv tool install --force --editable . --with-editable <pack>",
        )
    return CheckResult(
        name=name, status=Status.OK, message="entry points match `po list`"
    )


def check_logfire_token() -> CheckResult:
    """LOGFIRE_TOKEN set (warn-only)."""
    name = "LOGFIRE_TOKEN"
    if os.environ.get("LOGFIRE_TOKEN"):
        return CheckResult(name=name, status=Status.OK, message="set")
    return CheckResult(
        name=name,
        status=Status.WARN,
        message="not set (telemetry disabled)",
        remediation="export LOGFIRE_TOKEN to enable telemetry (beads 9cn)",
    )


# -- aggregator ---------------------------------------------------------


ALL_CHECKS: list[Callable[[], CheckResult]] = [
    check_bd_on_path,
    check_prefect_api_reachable,
    check_work_pool_exists,
    check_formulas_load,
    check_deployments_load,
    check_po_list_nonempty,
    check_uv_tool_fresh,
    check_logfire_token,
]


@dataclass
class DoctorReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.FAIL]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.WARN]

    @property
    def exit_code(self) -> int:
        return 1 if self.failures else 0


def run_doctor(
    checks: list[Callable[[], CheckResult]] | None = None,
) -> DoctorReport:
    """Run every check, isolating per-check exceptions as FAILs."""
    report = DoctorReport()
    for fn in checks or ALL_CHECKS:
        try:
            report.results.append(fn())
        except Exception as exc:
            report.results.append(
                CheckResult(
                    name=getattr(fn, "__name__", "unknown"),
                    status=Status.FAIL,
                    message=f"check raised: {exc}",
                    remediation="file a bug — doctor checks should not raise",
                )
            )
    return report


# -- rendering ----------------------------------------------------------


def render_table(report: DoctorReport) -> str:
    """Fixed-width table; remediation on the line below non-OK rows."""
    headers = ("CHECK", "STATUS", "MESSAGE")
    rows = [(r.name, r.status.value, r.message) for r in report.results]
    widths = [
        max(len(headers[0]), *(len(r[0]) for r in rows)) if rows else len(headers[0]),
        max(len(headers[1]), *(len(r[1]) for r in rows)) if rows else len(headers[1]),
        max(len(headers[2]), *(len(r[2]) for r in rows)) if rows else len(headers[2]),
    ]
    fmt = f"{{:<{widths[0]}}}  {{:<{widths[1]}}}  {{:<{widths[2]}}}"
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    for r in report.results:
        lines.append(fmt.format(r.name, r.status.value, r.message))
        if r.status is not Status.OK and r.remediation:
            lines.append(f"  -> {r.remediation}")
    lines.append("")
    lines.append(
        f"{len(report.failures)} failure(s), {len(report.warnings)} warning(s)."
    )
    return "\n".join(lines)
