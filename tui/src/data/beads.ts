/**
 * Wrapper around the `bd`/`br` CLI. Read-only: only `list` and `show`.
 *
 * We shell out via Bun.spawn and JSON-decode stdout. If the binary isn't
 * on PATH or returns non-zero, we surface an empty result + record the
 * error so the UI can show a banner instead of crashing.
 */

export interface BdDependent {
  id: string;
  title?: string;
  status: string;
  close_reason?: string;
  /** "parent-child" | "blocks" | "tracks" | ... */
  dependency_type: string;
  // permissive — ignore other fields
  [key: string]: unknown;
}

export interface BdIssue {
  id: string;
  title: string;
  status: string;
  priority?: number;
  issue_type?: string;
  assignee?: string;
  parent_id?: string;
  dependencies?: string[];
  // ── fields populated by `bd show <id> --json` (not by `bd list`) ──
  description?: string;
  metadata?: Record<string, string>;
  dependents?: BdDependent[];
  close_reason?: string;
  closed_at?: string;
  /** Closed beads only; the parent id under `bd show`. */
  parent?: string;
  // bd schema is permissive; keep the rest opaque
  [key: string]: unknown;
}

type TrackerBinary = "bd" | "br";

interface TrackerError extends Error {
  binary: TrackerBinary;
  args: string[];
  stderr: string;
  code: number;
}

function isTrackerError(err: unknown): err is TrackerError {
  return err instanceof Error && "binary" in err && "stderr" in err && "code" in err;
}

async function runTracker(binary: TrackerBinary, args: string[]): Promise<string> {
  const proc = Bun.spawn([binary, ...args], {
    stdout: "pipe",
    stderr: "pipe",
  });
  const [stdout, stderr, code] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  if (code !== 0) {
    const message = `${binary} ${args.join(" ")} exited ${code}: ${stderr.trim()}`;
    const err = new Error(message) as TrackerError;
    err.binary = binary;
    err.args = args;
    err.stderr = stderr;
    err.code = code;
    throw err;
  }
  return stdout;
}

async function runWithFallback(
  primaryArgs: string[],
  fallbackArgs: string[],
  shouldFallback: (err: unknown) => boolean = () => true,
): Promise<string> {
  try {
    return await runTracker("bd", primaryArgs);
  } catch (err) {
    if (!shouldFallback(err)) throw err;
    try {
      return await runTracker("br", fallbackArgs);
    } catch {
      throw err;
    }
  }
}

export async function bdList(
  opts: { status?: string; epicId?: string } = {},
): Promise<BdIssue[]> {
  const args = ["list", "--json"];
  if (opts.status) args.push(`--status=${opts.status}`);
  if (opts.epicId) args.push(`--epic=${opts.epicId}`);
  const fallbackArgs = ["--json", "list"];
  if (opts.status) fallbackArgs.push(`--status=${opts.status}`);
  if (opts.epicId) fallbackArgs.push(`--epic=${opts.epicId}`);
  const out = await runWithFallback(args, fallbackArgs);
  const parsed = JSON.parse(out);
  // bd may return either a bare array or {issues: [...]} depending on version.
  if (Array.isArray(parsed)) return parsed as BdIssue[];
  if (parsed && Array.isArray(parsed.issues)) return parsed.issues as BdIssue[];
  return [];
}

/**
 * Throws on shellout failure (exit ≠ 0), JSON parse failure, or unexpected
 * shape (non-array root). Returns null only when bd legitimately returned
 * an empty array (shouldn't happen for a valid id, but cheaper to handle
 * than to throw).
 *
 * `bd show <id> --json` returns an array of length 1; we unwrap it here so
 * callers see a single issue (the natural mental model for a `show`).
 */
export async function bdShow(id: string): Promise<BdIssue | null> {
  const out = await runWithFallback(
    ["show", id, "--json"],
    ["--json", "show", id],
    (err) => {
      if (!isTrackerError(err)) return true;
      if (err.code === 127) return true;
      const haystack = `${err.message}\n${err.stderr}`;
      return /ISSUE_NOT_FOUND|Issue not found|Run `br list`|br list/i.test(haystack);
    },
  );
  const parsed = JSON.parse(out);
  if (!Array.isArray(parsed)) {
    throw new Error(`bd show ${id}: expected array, got ${typeof parsed}`);
  }
  return (parsed[0] as BdIssue | undefined) ?? null;
}
