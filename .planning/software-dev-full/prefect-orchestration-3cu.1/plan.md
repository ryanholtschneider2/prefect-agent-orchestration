# Plan — prefect-orchestration-3cu.1 (po-gmail tool pack)

## Context

The pack already exists at `/home/ryan-24/Desktop/Code/personal/nanocorps/po-gmail`
from a prior iteration (this run is a retry). Local sanity check confirms:

- `uv run python -m pytest` → 15 passed
- `po install --editable …/po-gmail` → succeeds
- `po list` → shows `gmail-inbox`, `gmail-send`, `gmail-thread`
- `po doctor` → surfaces 3 `po-gmail` checks (creds file, refresh token, API reachable)

The pack is therefore **substantially complete**. The job for this iteration
is to (a) verify each AC against the existing artifacts, (b) tighten anything
that's drifted from `engdocs/pack-convention.md`, and (c) leave the rig in a
state where the verifier can re-run the AC checklist deterministically.

## Affected files (best guess — confirmed during build)

All inside `/home/ryan-24/Desktop/Code/personal/nanocorps/po-gmail/`:

- `pyproject.toml` — entry-points + deps (already correct; verify only)
- `po_gmail/auth.py` — credential/token loader (verify)
- `po_gmail/service.py` — Gmail API service builder (verify)
- `po_gmail/commands.py` — `gmail_inbox`, `gmail_send`, `gmail_thread` (verify signatures + dry-run paths)
- `po_gmail/checks.py` — three `DoctorCheck` factories (verify return shape)
- `skills/gmail/SKILL.md` — vendor doc links, CLI-first framing, SDK fallback
- `overlay/CLAUDE.md` — nanocorp rules (from-address, label conventions, do-not-touch folders)
- `tests/` — existing 15-test suite covering signatures, no-creds doctor, dry-run sends, skill/overlay presence, pyproject metadata
- `README.md` — installation + bootstrap recipe

The core repo (`prefect-orchestration/`) needs **no code changes** for this
issue — `po install --editable`, `po list`, `po doctor`, and the pack-discovery
machinery (`po.commands`, `po.doctor_checks`) are all shipped and exercised by
sibling packs (`po-slack`, `po-stripe`).

## Approach

1. **Audit existing pack against the 6 ACs** using the convention doc as the
   rubric. For each AC, identify the file(s) that prove it and what would
   make a verifier mark it green.
2. **Patch any drift** found during audit. Most likely candidates:
   - SKILL.md missing `https://developers.google.com/gmail/api/llms.txt` link or
     not leading with the CLI-first ladder from convention §"Tool-access
     preference order"
   - Overlay `CLAUDE.md` missing one of the three required policy items
     (from-address, label conventions, do-not-touch folders)
   - Pyproject deps not pinning `google-api-python-client` (AC1 requires it)
   - Missing `.gitignore` patterns for `~/.config/po-gmail/*.json` analogues
     in `overlay/.env.example` (per triage risk note)
3. **Re-run the install + smoke loop** to prove everything composes:
   `po install --editable …`, `po update`, `po list | grep gmail`,
   `po doctor | grep po-gmail`, `cd po-gmail && uv run python -m pytest`.
4. **No changes to core (`prefect-orchestration/`)** — the AC says the pack
   must work with `po install --editable /path`, which already works. If the
   audit reveals a core bug, surface it as a separate bead rather than
   bundling it here.

Why this approach: principle §1 of `engdocs/principles.md` says "no
pass-through wrappers, no inventing what already works". The pack is built;
this iteration's job is to prove it satisfies the contract and fix only what's
demonstrably broken.

## Acceptance criteria (verbatim)

> (1) Python dep: `google-api-python-client`; (2) `skills/gmail/SKILL.md`
> CLI-first with vendor doc links; (3) 3 commands; (4) 3 doctor checks;
> (5) `overlay/CLAUDE.md`; (6) works with `po install --editable /path`.

## Verification strategy

| AC | Concrete check |
|----|----------------|
| 1. Python dep `google-api-python-client` | `grep '^\s*"google-api-python-client' po-gmail/pyproject.toml` returns a line under `[project] dependencies`. Test `tests/test_pyproject_metadata.py` already asserts this — run it. |
| 2. SKILL.md CLI-first + vendor doc links | `test -f po-gmail/skills/gmail/SKILL.md && grep -q 'developers.google.com/gmail' && grep -q -i 'cli' SKILL.md`. Manual read confirms it leads with CLI ladder per convention §"Tool-access preference order" and points to `https://developers.google.com/gmail/api/quickstart/python`. |
| 3. Three commands | `po list \| grep '^command  gmail-'` shows exactly 3 rows: `gmail-inbox`, `gmail-send`, `gmail-thread`. `tests/test_commands_signatures.py` asserts importability + signature shape. |
| 4. Three doctor checks | `po doctor \| grep '^po-gmail'` shows exactly 3 rows. `tests/test_pyproject_metadata.py` asserts the entry-point group has 3 names. `tests/test_checks_no_creds.py` asserts each check returns yellow (warn) when no creds present (not red — graceful degradation). |
| 5. `overlay/CLAUDE.md` exists with nanocorp rules | `test -f po-gmail/overlay/CLAUDE.md && grep -q -i 'from-address\|from address' && grep -q -i 'label' && grep -q -i 'do not touch\|do-not-touch'`. `tests/test_skill_and_overlay.py` asserts the file exists and contains expected anchors. |
| 6. Works with `po install --editable /path` | `po install --editable /home/ryan-24/Desktop/Code/personal/nanocorps/po-gmail` exits 0; subsequently `po list` shows the 3 commands and `po doctor` runs the 3 checks. |

## Test plan

- **Unit (in `po-gmail/tests/`)** — already in place, 15 tests:
  - `test_pyproject_metadata.py` — covers AC1, AC3 (count), AC4 (count)
  - `test_commands_signatures.py` — covers AC3 (importable, callable signatures)
  - `test_commands_dryrun.py` — `gmail-send` dry-run path emits intended payload without invoking the API
  - `test_checks_no_creds.py` — covers AC4 graceful-fail behavior
  - `test_skill_and_overlay.py` — covers AC2, AC5 (file presence + key strings)
  - Re-run via `cd po-gmail && uv run python -m pytest`
- **Integration (manual, outside CI)** — covers AC6:
  - `po install --editable /home/ryan-24/Desktop/Code/personal/nanocorps/po-gmail`
  - `po update`
  - `po list | grep gmail-` (expect 3 lines, KIND=command)
  - `po doctor | grep po-gmail` (expect 3 lines)
  - `po show gmail-inbox` (expect docstring + signature)
  - These steps are already passing in the live rig (verified during planning).
- **Live API smoke (out of scope, manual only)** — actually hitting Gmail
  requires real OAuth bootstrap; documented in SKILL.md but never executed in
  CI. This is consistent with the triage's "live Gmail calls in CI are
  infeasible" note.
- **No e2e via `tests/e2e/` in `prefect-orchestration`** — the pack repo is
  the test owner; `prefect-orchestration` doesn't import or know about it.
- **No Playwright** — no UI.

## Risks

- **No core changes ⇒ no migrations, no API contract changes, no breaking
  consumers.** The pack is additive.
- **Auth-bootstrap UX**: `po doctor` will warn red/yellow on any rig where
  the user hasn't run the Google quickstart. SKILL.md must make the
  bootstrap recipe trivially copy-pastable. Risk that a verifier reads "API
  reachable: WARN" as failure — mitigate by ensuring the check returns
  yellow (warn) not red (fail) when creds are merely absent, and that the
  hint clearly points to the quickstart URL. Confirmed in
  `tests/test_checks_no_creds.py`; do not regress.
- **Secret hygiene**: ensure `overlay/.env.example` (if shipped) contains
  only variable names, never real tokens. `overlay/CLAUDE.md` should
  remind users to add `~/.config/po-gmail/` to their personal gitignore
  (the file lives outside the rig anyway, so this is belt-and-suspenders).
- **Editable install path coupling**: `[tool.uv.sources]` in
  `po-gmail/pyproject.toml` pins `prefect-orchestration` to a relative
  path (`../prefect-orchestration`). Fine on Ryan's machine; would break
  for any other user. Acceptable for v1 (per convention doc, packs are
  sibling repos by convention) but flag as a follow-up if `po install`
  ever lands on PyPI.
- **Scope creep guard**: triage explicitly calls threading-with-attachments
  / HTML bodies / multi-recipient bcc as out of scope for v1. Plan
  adheres — `gmail_send` accepts plain-text body via stdin, single `--to`,
  optional `--subject`. Anything richer waits for a follow-up bead.
