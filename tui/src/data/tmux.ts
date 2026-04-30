/**
 * Read-only tmux helpers. The TUI never sends keys; it only captures pane
 * contents to render the live tail. Actual `tmux attach` is performed by
 * the cli.tsx wrapper after Ink exits cleanly.
 */

export async function capturePane(
  session: string,
  lines = 200,
): Promise<string> {
  const proc = Bun.spawn(
    ["tmux", "capture-pane", "-t", session, "-p", "-S", `-${lines}`],
    { stdout: "pipe", stderr: "pipe" },
  );
  const [stdout, code] = await Promise.all([
    new Response(proc.stdout).text(),
    proc.exited,
  ]);
  if (code !== 0) {
    return ""; // session may not exist yet — caller renders "no pane".
  }
  return stdout;
}

export async function tmuxSessionExists(session: string): Promise<boolean> {
  const proc = Bun.spawn(["tmux", "has-session", "-t", session], {
    stdout: "ignore",
    stderr: "ignore",
  });
  const code = await proc.exited;
  return code === 0;
}

/** Roles displayed in the timeline, in canonical order. These are the
 *  display labels the user sees — not necessarily Prefect task names. */
export const ROLES = [
  "triage",
  "baseline",
  "plan",
  "critique",
  "build",
  "lint",
  "test",
  "regression",
  "review",
  "deploy-smoke",
  "verification",
  "ralph",
  "docs",
  "learn",
] as const;

export type Role = (typeof ROLES)[number];

/**
 * Map a Prefect `@task` name to its display role label.
 *
 * software-dev-full's tasks are `triage`, `baseline`, `plan`, `critique_plan`,
 * `build`, `lint`, `run_tests`, `regression_gate`, `review`, `deploy_smoke`,
 * `review_artifacts`, `verification`, `ralph`, `docs`, `demo_video`, `learn`.
 * Display labels collapse a few of those (e.g. `critique_plan` → `critique`).
 *
 * Returns `undefined` for task names we don't recognize so the timeline can
 * choose to render an "extra" column or ignore them.
 */
const TASK_TO_DISPLAY_ROLE: Record<string, string> = {
  triage: "triage",
  baseline: "baseline",
  plan: "plan",
  critique: "critique",
  critique_plan: "critique",
  "critique-plan": "critique",
  "plan-critique": "critique",
  build: "build",
  lint: "lint",
  test: "test",
  run_tests: "test",
  "run-tests": "test",
  regression: "regression",
  regression_gate: "regression",
  "regression-gate": "regression",
  review: "review",
  review_artifacts: "review",
  "review-artifacts": "review",
  "deploy-smoke": "deploy-smoke",
  deploy_smoke: "deploy-smoke",
  verification: "verification",
  ralph: "ralph",
  docs: "docs",
  demo_video: "docs",
  "demo-video": "docs",
  learn: "learn",
};

export function displayRoleFor(taskName: string): string | undefined {
  return TASK_TO_DISPLAY_ROLE[taskName];
}

/**
 * Strip Prefect's auto-generated suffixes from a task_run name to recover
 * the underlying @task name. Prefect appends `-<3-8 hex chars>` by default,
 * and our software_dev pack adds `-iter-N` for looping steps.
 *
 *   "triage-cc6"            → "triage"
 *   "run_tests-fcb"         → "run_tests"
 *   "plan-critique-iter-1"  → "plan-critique"
 *   "critique_plan-d58"     → "critique_plan"
 */
export function baseTaskName(runName: string): string {
  let base = runName.replace(/-iter-\d+$/i, "");
  base = base.replace(/-[a-f0-9]{3,8}$/i, "");
  return base;
}

/**
 * Map timeline/task names → tmux registry role names.
 *
 * The Prefect task names (and tags) in `software_dev.py` differ from the
 * `RoleRegistry` keys used to build tmux session names. tmux sessions follow
 * `po-<safe_issue>-<registry_role>(-<6hex>)?` where `safe_issue` replaces
 * `.` with `_`. This map closes that gap.
 */
const TASK_TO_REGISTRY_ROLE: Record<string, string> = {
  triage: "triager",
  baseline: "tester",
  plan: "builder",
  critique: "critic",
  "critique-plan": "critic",
  critique_plan: "critic",
  build: "builder",
  lint: "linter",
  test: "tester",
  run_tests: "tester",
  regression: "tester",
  regression_gate: "tester",
  review: "critic",
  review_artifacts: "releaser",
  "deploy-smoke": "releaser",
  deploy_smoke: "releaser",
  verification: "verifier",
  ralph: "cleaner",
  docs: "documenter",
  learn: "documenter",
  demo_video: "documenter",
};

export function registryRoleFor(taskName: string): string {
  return TASK_TO_REGISTRY_ROLE[taskName] ?? taskName;
}

export function sanitizeIssueId(issueId: string): string {
  // tmux treats '.' as a session.window.pane separator; po replaces with '_'.
  return issueId.replace(/\./g, "_");
}

/** Build the bare (un-suffixed) tmux session name. Forked tester sessions
 *  add a `-<6hex>` suffix; use `resolveSession` to find them. */
export function sessionFor(issueId: string, role: string): string {
  return `po-${sanitizeIssueId(issueId)}-${registryRoleFor(role)}`;
}

let cachedSessionList: { at: number; names: string[] } | null = null;
let cachedWindowList:
  | { at: number; rows: { session: string; window: string; id: string }[] }
  | null = null;

async function listTmuxSessions(): Promise<string[]> {
  // Tiny cache (1s) so `resolveSession` calls in a single tick don't
  // each shell out to tmux.
  const now = Date.now();
  if (cachedSessionList && now - cachedSessionList.at < 1000) {
    return cachedSessionList.names;
  }
  const proc = Bun.spawn(["tmux", "list-sessions", "-F", "#{session_name}"], {
    stdout: "pipe",
    stderr: "ignore",
  });
  const [stdout, code] = await Promise.all([
    new Response(proc.stdout).text(),
    proc.exited,
  ]);
  const names =
    code === 0 ? stdout.split("\n").map((l) => l.trim()).filter(Boolean) : [];
  cachedSessionList = { at: now, names };
  return names;
}

async function listTmuxWindows(): Promise<
  { session: string; window: string; id: string }[]
> {
  const now = Date.now();
  if (cachedWindowList && now - cachedWindowList.at < 1000) {
    return cachedWindowList.rows;
  }
  const proc = Bun.spawn(
    [
      "tmux",
      "list-windows",
      "-a",
      "-F",
      "#{session_name}\t#{window_name}\t#{window_id}",
    ],
    { stdout: "pipe", stderr: "ignore" },
  );
  const [stdout, code] = await Promise.all([
    new Response(proc.stdout).text(),
    proc.exited,
  ]);
  const rows: { session: string; window: string; id: string }[] = [];
  if (code === 0) {
    for (const line of stdout.split("\n")) {
      const [session, window, id] = line.split("\t");
      if (session && window && id) rows.push({ session, window, id });
    }
  }
  cachedWindowList = { at: now, rows };
  return rows;
}

/**
 * Resolve a live tmux target for `(issueId, role)`. Returns a tmux target
 * spec usable by `tmux capture-pane -t <target>` (and the display name to
 * surface in the UI), or `null` if nothing matches.
 *
 * Handles three layouts:
 *   1. Legacy unscoped: session `po-<safe_issue>-<role>` (whole-session target)
 *   2. Scoped: session `po-<scope>` with window `<safe_issue>-<safe_role>`
 *      → target `@<window_id>` (stable across renames)
 *   3. Either layout with `-<6hex>` fork suffix (parallel `run_tests`)
 */
export async function resolveSession(
  issueId: string,
  role: string,
): Promise<string | null> {
  // Layout 1: dedicated session per (issue, role).
  const base = sessionFor(issueId, role);
  const sessions = await listTmuxSessions();
  if (sessions.includes(base)) return base;
  const sessionFork = new RegExp(`^${escapeRe(base)}-[0-9a-f]{6}$`);
  const sessionMatch = sessions.find((n) => sessionFork.test(n));
  if (sessionMatch) return sessionMatch;

  // Layout 2: window inside a shared scoped session. Window name is built
  // identically by agent_session._scoped_names, modulo the `po-` prefix.
  // Return `<session>:<window>` so capture-pane targets the right window
  // *and* `tmux attach -t <session>:<window>` selects it on attach.
  const windowBase = base.replace(/^po-/, "");
  const windowFork = new RegExp(`^${escapeRe(windowBase)}-[0-9a-f]{6}$`);
  const windows = await listTmuxWindows();
  const exact = windows.find((w) => w.window === windowBase);
  if (exact) return `${exact.session}:${exact.window}`;
  const fork = windows.find((w) => windowFork.test(w.window));
  if (fork) return `${fork.session}:${fork.window}`;

  return null;
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Spawn `tmux attach -t <session>` in a new terminal tab/window, detached
 * from the current Ink process. Returns the method that worked, or null if
 * none did so the caller can fall back to the in-place attach.
 *
 * Detection order:
 *   1. $PO_TUI_TERMINAL — user override; treated as `<cmd> tmux attach -t <s>`
 *   2. kitty remote control (env $KITTY_LISTEN_ON) → new tab in same window
 *   3. wezterm CLI (env $WEZTERM_PANE) → new tab in same window
 *   4. gnome-terminal already running ($GNOME_TERMINAL_SERVICE) → --tab
 *   5. gnome-terminal on PATH → new window
 *   6. konsole / foot / alacritty / x-terminal-emulator / xterm → new window
 */
export async function openAttachInNewWindow(
  session: string,
): Promise<string | null> {
  const env = process.env;
  const candidates: { method: string; cmd: string[] }[] = [];

  if (env.PO_TUI_TERMINAL) {
    candidates.push({
      method: `PO_TUI_TERMINAL=${env.PO_TUI_TERMINAL}`,
      cmd: [env.PO_TUI_TERMINAL, "tmux", "attach", "-t", session],
    });
  }
  if (env.KITTY_LISTEN_ON) {
    candidates.push({
      method: "kitty @ launch --type=tab",
      cmd: [
        "kitty",
        "@",
        "launch",
        "--type=tab",
        "--tab-title",
        session,
        "tmux",
        "attach",
        "-t",
        session,
      ],
    });
  }
  if (env.WEZTERM_PANE) {
    candidates.push({
      method: "wezterm cli spawn --new-tab",
      cmd: [
        "wezterm",
        "cli",
        "spawn",
        "--new-tab",
        "--",
        "tmux",
        "attach",
        "-t",
        session,
      ],
    });
  }
  if (env.GNOME_TERMINAL_SERVICE) {
    candidates.push({
      method: "gnome-terminal --tab",
      cmd: ["gnome-terminal", "--tab", "--", "tmux", "attach", "-t", session],
    });
  }
  // Fallback: spawn whatever terminal emulator is installed, in a new window.
  for (const term of [
    "gnome-terminal",
    "konsole",
    "foot",
    "alacritty",
    "wezterm",
    "kitty",
    "x-terminal-emulator",
    "xterm",
  ]) {
    if (await onPath(term)) {
      candidates.push({
        method: `${term} (new window)`,
        cmd: termSpawnCmd(term, session),
      });
    }
  }

  for (const c of candidates) {
    try {
      const proc = Bun.spawn(c.cmd, {
        stdout: "ignore",
        stderr: "ignore",
        stdin: "ignore",
      });
      // Detach: don't await `exited`. If the binary fails fast (<100ms),
      // try the next candidate.
      const verdict = await Promise.race([
        proc.exited.then((code) => ({ early: true, code })),
        new Promise<{ early: false }>((resolve) =>
          setTimeout(() => resolve({ early: false }), 200),
        ),
      ]);
      if (verdict.early && "code" in verdict && verdict.code !== 0) {
        continue; // try next candidate
      }
      return c.method;
    } catch {
      continue;
    }
  }
  return null;
}

function termSpawnCmd(term: string, session: string): string[] {
  const inner = ["tmux", "attach", "-t", session];
  switch (term) {
    case "gnome-terminal":
      return ["gnome-terminal", "--", ...inner];
    case "konsole":
      return ["konsole", "-e", ...inner];
    case "foot":
      return ["foot", ...inner];
    case "alacritty":
      return ["alacritty", "-e", ...inner];
    case "wezterm":
      return ["wezterm", "start", "--", ...inner];
    case "kitty":
      return ["kitty", ...inner];
    case "x-terminal-emulator":
      // Debian alternative; -e expects a single string for some impls but
      // most accept exec form. Try exec form first.
      return ["x-terminal-emulator", "-e", ...inner];
    case "xterm":
      return ["xterm", "-e", ...inner];
    default:
      return [term, ...inner];
  }
}

async function onPath(bin: string): Promise<boolean> {
  const proc = Bun.spawn(["sh", "-c", `command -v ${bin}`], {
    stdout: "ignore",
    stderr: "ignore",
  });
  return (await proc.exited) === 0;
}
