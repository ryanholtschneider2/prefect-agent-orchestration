# CLAUDE.md — `tui/` (po TUI)

Ink/React TUI built on Bun. Distributed as a single-file binary via
`bun build --compile`. Wired into the Python `po` CLI by shelling out
to `dist/po-tui`.

## Bun test conventions

- **Use `bun test` (the test runner is `bun:test`).** Scripts: `bun test`
  (run), `bun run typecheck` (`tsc --noEmit`), `bun run build`. There is
  no Vitest, no Jest — don't add `vi.mock` / `jest.mock` references.
- **Do NOT use `mock.module(...)` at the top of a test file.** As of Bun
  1.3.6, a `mock.module("./path", …)` call in `foo.test.ts` persists
  into every *other* test file in the same `bun test` run, and
  `mock.restore()` does NOT undo it. Symptom: `bar.test.ts` fails because
  its `import { thing } from "./path"` resolves to the stub `foo.test.ts`
  installed, not the real implementation. Reordering files (rename so
  `bar.test.ts` runs first) is fragile — a new sibling test will eventually
  sort in between and re-break it.

  **Mock at the system boundary instead** — `Bun.spawn`, `fetch`, etc.
  Boundary mocks are auto-scoped to the file via the standard
  `spyOn` / `jest.spyOn`-style API and exercise the real module body
  under test. See `tui/src/__tests__/bdShowStore.test.ts` for the
  pattern (mocks `Bun.spawn` to fake `bd show --json`, exercises the
  real `bdShow()` + store action).
