"""Pack-contributed `po doctor` check for the core `po` skill's eval freshness.

Registered via the `po.doctor_checks` entry point on the
`prefect-orchestration` distribution itself (the `po` skill ships from
core, not a sibling pack).

Status mapping:
    green  — reports/latest.json exists, pass_rate >= threshold, age <  30 days
    yellow — pass_rate >= threshold but age >= 30 days (stale)
    red    — pass_rate < threshold OR latest.json missing/malformed

`pass_rate` is read from the verdict's `n_passed / n_cases`. `threshold`
defaults to 0.75 (matching `RubricsFile.pass_threshold`'s default and the
shipped rubrics for this skill).
"""

from __future__ import annotations

import datetime as _dt
import json

from prefect_orchestration.doctor import DoctorCheck
from prefect_orchestration.skill_evals import resolve_pack_skill_dir

_PASS_RATE_THRESHOLD = 0.75
_STALE_AGE_DAYS = 30
_PACK = "prefect-orchestration"
_SKILL = "po"


def _parse_iso8601_z(text: str) -> _dt.datetime:
    """Parse a timestamp ending in `Z` as UTC.

    `_utcnow_iso()` in skill_evals stamps `YYYY-MM-DDTHH:MM:SSZ`. Python
    pre-3.11 fromisoformat() rejects the trailing `Z`; this normalizes.
    """
    normalized = text.rstrip("Z") + "+00:00" if text.endswith("Z") else text
    return _dt.datetime.fromisoformat(normalized)


def po_skill_evals_fresh() -> DoctorCheck:
    name = "po-skill-evals-fresh"
    try:
        skill_dir = resolve_pack_skill_dir(_PACK, _SKILL)
    except Exception as exc:
        return DoctorCheck(
            name=name,
            status="red",
            message=f"cannot resolve {_PACK}/{_SKILL}: {exc}",
            hint=f"reinstall: `uv tool install --force --editable .` (in {_PACK})",
        )

    report_path = skill_dir / "reports" / "latest.json"
    if not report_path.is_file():
        return DoctorCheck(
            name=name,
            status="red",
            message=f"missing {report_path}",
            hint=f"run: po run skill-evals --pack {_PACK} --skill {_SKILL}",
        )

    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return DoctorCheck(
            name=name,
            status="red",
            message=f"latest.json unreadable: {exc}",
            hint=f"re-run: po run skill-evals --pack {_PACK} --skill {_SKILL}",
        )

    n_cases = int(data.get("n_cases", 0))
    n_passed = int(data.get("n_passed", 0))
    finished_at = str(data.get("finished_at") or "")
    if n_cases <= 0 or not finished_at:
        return DoctorCheck(
            name=name,
            status="red",
            message="latest.json missing n_cases / finished_at",
            hint=f"re-run: po run skill-evals --pack {_PACK} --skill {_SKILL}",
        )

    pass_rate = n_passed / n_cases
    pretty_rate = f"{n_passed}/{n_cases} ({pass_rate:.0%})"

    if pass_rate < _PASS_RATE_THRESHOLD:
        return DoctorCheck(
            name=name,
            status="red",
            message=f"pass_rate {pretty_rate} < {_PASS_RATE_THRESHOLD:.0%}",
            hint=(
                f"inspect failures: po run skill-evals --pack {_PACK} "
                f"--skill {_SKILL} --tier regression"
            ),
        )

    try:
        finished_dt = _parse_iso8601_z(finished_at)
    except ValueError:
        return DoctorCheck(
            name=name,
            status="red",
            message=f"finished_at unparseable: {finished_at!r}",
            hint=f"re-run: po run skill-evals --pack {_PACK} --skill {_SKILL}",
        )

    age = _dt.datetime.now(_dt.timezone.utc) - finished_dt
    age_days = age.total_seconds() / 86400.0
    pretty_date = finished_dt.date().isoformat()

    if age_days >= _STALE_AGE_DAYS:
        return DoctorCheck(
            name=name,
            status="yellow",
            message=(
                f"skill po evals: {pretty_rate} (last run {pretty_date}, "
                f"{int(age_days)}d ago — stale)"
            ),
            hint=f"refresh: po run skill-evals --pack {_PACK} --skill {_SKILL}",
        )

    return DoctorCheck(
        name=name,
        status="green",
        message=f"skill po evals: {pretty_rate} ({pretty_date})",
    )
