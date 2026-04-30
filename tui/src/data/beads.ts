/**
 * Wrapper around the `bd` CLI. Read-only: only `list` and `show`.
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

async function runBd(args: string[]): Promise<string> {
  const proc = Bun.spawn(["bd", ...args], {
    stdout: "pipe",
    stderr: "pipe",
  });
  const [stdout, stderr, code] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
    proc.exited,
  ]);
  if (code !== 0) {
    throw new Error(`bd ${args.join(" ")} exited ${code}: ${stderr.trim()}`);
  }
  return stdout;
}

export async function bdList(
  opts: { status?: string; epicId?: string } = {},
): Promise<BdIssue[]> {
  const args = ["list", "--json"];
  if (opts.status) args.push(`--status=${opts.status}`);
  if (opts.epicId) args.push(`--epic=${opts.epicId}`);
  try {
    const out = await runBd(args);
    const parsed = JSON.parse(out);
    // bd may return either a bare array or {issues: [...]} depending on version.
    if (Array.isArray(parsed)) return parsed as BdIssue[];
    if (parsed && Array.isArray(parsed.issues)) return parsed.issues as BdIssue[];
    return [];
  } catch (err) {
    throw err instanceof Error ? err : new Error(String(err));
  }
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
  const out = await runBd(["show", id, "--json"]);
  const parsed = JSON.parse(out);
  if (!Array.isArray(parsed)) {
    throw new Error(`bd show ${id}: expected array, got ${typeof parsed}`);
  }
  return (parsed[0] as BdIssue | undefined) ?? null;
}
