# Plan: prefect-orchestration-5kj — Beads-as-mail helper

## Acceptance criteria (verbatim)

1. `po_formulas/mail.py` with `send()` and `inbox()` functions
2. builder/critic prompts updated to check inbox before producing verdict
3. demo test: critic messages builder with "fix X", builder reads it on next turn
4. README section on agent messaging

## Context & decisions

- The repo currently contains only the `prefect_orchestration/` core package; `po_formulas/` is referenced from `prefect_orchestration/cli.py` and `deployments.py` as entry points (`po_formulas.software_dev:software_dev_full`, etc.) but the package does not yet exist in-tree. Issue text mandates the module path `po_formulas/mail.py`, so we create that package here as a side-effect.
- `bd create --type=message` is **not** supported out of the box — beads accepts `bug|feature|task|epic|chore|decision` unless `types.custom` is configured. To avoid requiring users to reconfigure beads, we encode mail semantics with `--type=task` + `--labels=mail` and a standardized title prefix `[mail:<to>] <subject>`. `inbox()` filters via `bd list --labels=mail --assignee=<agent> --status=open --json`.
- Read semantics: `inbox()` returns open mail issues; the caller (or a helper `mark_read(id)`) closes them via `bd close` to remove from future inboxes. This keeps it fire-and-forget while still idempotent across turns.
- No prompts directory exists yet. The builder/critic prompt strings live (will live) under `po_formulas/software_dev/prompts/`. Since those prompts don't ship in this repo today (they're sibling/future work), we add a reusable snippet file `po_formulas/mail_prompt.md` and document how builder/critic prompts should `{{include}}` it. We also update `AGENTS.md` (the agent-facing docs file at repo root) with the fragment so existing/future role prompts can pull it in verbatim.
- Demo test uses a **subprocess-level mock**: monkeypatch `subprocess.run` to capture `bd` invocations and fabricate JSON list responses. This avoids requiring a live beads DB in CI and also satisfies AC #3 ("critic messages builder, builder reads on next turn") at unit level. A second, opt-in integration test (`@pytest.mark.skipif(not shutil.which('bd'))`) runs the same flow against a real `bd`.

## Affected files

New:
- `po_formulas/__init__.py` — package marker
- `po_formulas/mail.py` — `send(to, subject, body, *, from_agent=None) -> str`, `inbox(agent, *, include_read=False) -> list[Mail]`, `mark_read(mail_id) -> None`, and a `Mail` dataclass.
- `po_formulas/mail_prompt.md` — role-prompt fragment: "Before producing your verdict, run `inbox('<role>')` and address any messages addressed to you."
- `tests/test_mail.py` — unit tests with `subprocess.run` monkeypatched; includes the critic→builder demo scenario.

Modified:
- `README.md` — new "## Agent messaging (beads-as-mail)" section documenting `send/inbox/mark_read`, labels convention, and prompt-fragment usage.
- `AGENTS.md` — append a short "Check your inbox" block pointing at the prompt fragment.
- `pyproject.toml` — add `po_formulas` to the packages list (currently only `prefect_orchestration`) so the new package is installable.

## Approach

1. Implement `po_formulas/mail.py` as a thin `subprocess.run(["bd", ...])` wrapper, mirroring the style of `prefect_orchestration/beads_meta.py` (same error model: `check=True` on writes, tolerate JSON parse failures on reads by returning `[]`). Short-circuit to a no-op (raise `RuntimeError("bd not on PATH")` from `send`; empty list from `inbox`) when `shutil.which("bd")` is None — same pattern used in `beads_meta._bd_available`.
2. Title format: `[mail:<to>] <subject>`. Description: body plus a trailing `\n\n---\nFrom: <from_agent>\n` block. Labels: `mail,mail-to:<to>` so filtering by recipient is cheap. Assignee set to `<to>` so `bd list --assignee=<to>` works too (double-filter).
3. `inbox(agent)` shells `bd list --labels=mail --assignee=<agent> --status=open --json` and parses into `Mail(id, from_agent, subject, body, created_at)`. Subject parsed by stripping the `[mail:<to>] ` prefix; `from_agent` recovered from the description footer.
4. `mark_read(mail_id)` runs `bd close <id> --reason="read"`.
5. Update `AGENTS.md` + ship `mail_prompt.md` so future builder/critic prompts can inline the fragment (satisfies AC #2 without requiring the prompts themselves to exist in-repo today).
6. README section gives a 5-line example and lists the label/title conventions.

## Verification strategy

- **AC #1** — `pytest tests/test_mail.py::test_send_invokes_bd_create` asserts `send("builder", "fix X", "see plan.md")` issues a `bd create` with `--type=task`, `--labels=mail,mail-to:builder`, `--assignee=builder`, title `[mail:builder] fix X`. `test_inbox_parses_bd_list` asserts round-trip parse of fake JSON.
- **AC #2** — static check: `grep -q 'mail_prompt' AGENTS.md` and existence of `po_formulas/mail_prompt.md` containing the "check inbox" sentence. Test `tests/test_mail.py::test_prompt_fragment_exists` reads the file and asserts the required keywords (`inbox`, recipient placeholder).
- **AC #3** — `test_critic_messages_builder_demo` monkeypatches `subprocess.run` with a fake backend that records `bd create` as a stored message and replays it on the next `bd list` call. Scenario: critic calls `send("builder", "fix X", ...)`; then builder calls `inbox("builder")` and gets one `Mail` with subject `"fix X"`.
- **AC #4** — `grep -q 'Agent messaging' README.md` plus reviewer eyeballing the section.

## Test plan

- **Unit (pytest)**: new `tests/test_mail.py` — covers send payload, inbox parse, mark_read, bd-missing short-circuit, and the demo handoff.
- **Integration (optional, skip when bd missing)**: same send→inbox→close cycle against a real `bd` binary, gated on `shutil.which("bd")`.
- **Playwright / E2E**: N/A (no UI, no network service).

Baseline test run had 1 pre-existing failure (`test_tmux_backend_passes_resume_across_turns`) unrelated to mail; this plan does not touch it. The new tests must bring total passes up by ≥5 without regressing the other 16.

## Risks

- **Custom `bd` type**: triage raised `--type=message` — we sidestep by using `--type=task` + label. If project later adopts custom types, `send()` gains a `--type=message` flag behind a feature switch; no consumer change required.
- **Label filter availability**: `bd list --labels=` is assumed; if absent, fall back to filtering by assignee plus a title-prefix check in Python. Low impact (parse work moves from bd to us).
- **Inbox pollution in `bd ready`**: mail issues are `type=task`, so they'd appear in `bd ready`. Mitigation: set `--priority=4` (backlog) on mail by default and document the `labels=mail` exclusion pattern in README so humans can filter them out of dashboards.
- **Package layout**: creating a top-level `po_formulas/` package in this repo may conflict with a separate `po-formulas` package if one exists on disk elsewhere. We confirm during build by searching for any pre-existing `po_formulas` on `PYTHONPATH`; if found, we namespace as `prefect_orchestration.mail` and add a shim re-export at `po_formulas.mail` so the issue's import path still works.
- **No migration / API contract**: pure additive; no consumers today since `po_formulas` doesn't yet exist in-tree.
