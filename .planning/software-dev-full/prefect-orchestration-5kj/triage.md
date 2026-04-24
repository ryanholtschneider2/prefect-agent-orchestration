# Triage: prefect-orchestration-5kj

## Summary
Add a lightweight agent-to-agent messaging helper module (`po_formulas/mail.py`) that uses beads issues as the transport layer (inspired by Gas City). Provides `send(to, subject, body)` and `inbox(agent)` wrappers over `bd create --type=message` and `bd list --type=message --assignee=...`. Also updates builder/critic role prompts to check inbox before producing verdicts, includes a demo test, and adds a README section documenting the pattern.

## Flags
- `has_ui`: **false** — no UI; this is a Python module + prompt fragments.
- `has_backend`: **true** — new Python module (`po_formulas/mail.py`) plus prompt fragment edits.
- `needs_migration`: **false** — relies on beads' existing issue store; no schema changes required (uses `--type=message` which beads accepts as a type string).
- `is_docs_only`: **false** — code changes (mail module, prompt updates) alongside a README section.

## Risks & Open Questions
- Beads `bd create --type=` accepts free-form types? Need to confirm `message` is a valid/accepted issue_type or whether it collides with existing task/bug/feature/epic enum values. If enum-restricted, either extend beads or encode message semantics via labels/tags.
- `bd list --type=message --assignee=...` filter support — verify beads CLI supports filtering by a custom type.
- Inbox pollution: mail issues live in the same tracker as real work; need clear separation (prefix, tag, or status) so `bd ready`/`bd list` don't surface messages as work items.
- Ack/read semantics: this design is fire-and-forget. Acceptance criteria don't specify read receipts; may need a minimal "read" marker (close issue on read?) to prevent re-processing on every turn.
- Prompt fragment injection point: how do builder/critic role prompts get updated? Need to locate the prompt source (likely in `po_formulas` or an agent config) before implementation.
- Demo test execution: "critic messages builder, builder reads on next turn" requires multi-turn agent orchestration harness — confirm existing test infrastructure supports this or whether a simpler unit-level demo suffices.
