#!/usr/bin/env python3
"""Rewrite dotted iter-bead ids to hyphens in a beads JSONL export.

``beads_rust`` (``br``) rejects dots in issue ids, but PO's legacy iter-bead
convention was ``<seed>.<step>.iter<N>`` (e.g. ``courtpro-0qt.ralph.iter1``).
Before importing a dolt export into br with ``br sync --import-only``, every
id and dependency reference that names an iter bead has to move to the new
``<seed>-<step>-iter<N>`` form (see
:func:`prefect_orchestration.beads_meta.iter_bead_id`).

The rewrite is a plain dot->hyphen substitution on the id-bearing fields
(``id``, ``issue_id``, ``depends_on_id``) of each JSONL record. This is safe
because of a system invariant: **only iter beads carry dots in their id.**
Seed / parent / epic ids are ``<prefix>-<hash>`` with no dots, so the
substitution is a no-op on them and only rewrites the iter-bead separators.

Usable as a library (``rewrite_record`` / ``rewrite_jsonl_text``) — the unit
test drives these directly — or as a CLI:

    python setup/migrate_jsonl_ids.py export.jsonl > export.hyphenated.jsonl
    python setup/migrate_jsonl_ids.py --in-place export.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# Keys whose values name a bead and therefore must be rewritten. Both the
# owning bead (``id``) and dependency endpoints (``issue_id`` /
# ``depends_on_id``) are covered so edges to/from iter beads stay connected.
ID_KEYS: frozenset[str] = frozenset({"id", "issue_id", "depends_on_id"})


def _rewrite_id(value: str) -> str:
    """Dot->hyphen on a single id value (no-op when it has no dots)."""
    return value.replace(".", "-")


def rewrite_record(obj: Any) -> Any:
    """Return *obj* with every id-bearing field's dots rewritten to hyphens.

    Recurses through nested dicts and lists so dependency rows embedded in a
    record (``{"dependencies": [{"depends_on_id": ...}, ...]}``) are caught
    too. Non-container values and unrelated keys pass through untouched. Does
    not mutate the input.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, val in obj.items():
            if key in ID_KEYS and isinstance(val, str):
                out[key] = _rewrite_id(val)
            else:
                out[key] = rewrite_record(val)
        return out
    if isinstance(obj, list):
        return [rewrite_record(item) for item in obj]
    return obj


def rewrite_jsonl_text(text: str) -> str:
    """Rewrite every JSON object line in a JSONL *text* blob.

    Blank lines are preserved; non-JSON lines are passed through verbatim
    (a bd export is one JSON object per line, but we stay lenient).
    """
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            out_lines.append(line)
            continue
        out_lines.append(json.dumps(rewrite_record(obj)))
    # Trailing newline keeps the file POSIX-clean for downstream tools.
    return "\n".join(out_lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="JSONL export to rewrite.")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the file instead of writing the result to stdout.",
    )
    args = parser.parse_args(argv)

    with open(args.path, encoding="utf-8") as fh:
        rewritten = rewrite_jsonl_text(fh.read())

    if args.in_place:
        with open(args.path, "w", encoding="utf-8") as fh:
            fh.write(rewritten)
    else:
        sys.stdout.write(rewritten)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
