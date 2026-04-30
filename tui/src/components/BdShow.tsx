import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";

import type { BdIssue } from "../data/beads.js";

interface Props {
  /** Last cached value (may be null on first load). */
  issue: BdIssue | null;
  /** Id we're trying to show. */
  issueId: string | null;
  /** Fetch error, if any. Rendered as a red banner when no cache, or a yellow
   *  "(stale: …)" overlay when a cached value is also present. */
  error: string | null;
  /** Row budget from parent. */
  height: number;
  /** Line-index of the first visible line (lifted to App). */
  scrollOffset: number;
}

const STATUS_COLORS: Record<string, string> = {
  open: "yellow",
  in_progress: "cyan",
  closed: "green",
  blocked: "red",
};

export function BdShow({
  issue,
  issueId,
  error,
  height,
  scrollOffset,
}: Props): React.ReactElement {
  // No cached issue: render either the red error banner or the loading spinner.
  if (!issue) {
    return (
      <Box paddingX={1}>
        {error ? (
          <Text color="red">! bd show: {error}</Text>
        ) : (
          <Text color="gray">
            <Spinner type="dots" />{" "}
            {issueId ? `bd show ${issueId}…` : "no issue selected"}
          </Text>
        )}
      </Box>
    );
  }

  // We have a cached issue; render it (possibly with a "stale" banner).
  const lines = buildLines(issue);
  const visible = lines.slice(scrollOffset, scrollOffset + height);
  const totalRest = Math.max(0, lines.length - (scrollOffset + height));
  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold color="white">
        BD SHOW — <Text color="cyan">{issue.id}</Text>{" "}
        <Text color={STATUS_COLORS[issue.status ?? ""] ?? "gray"}>
          {issue.status}
        </Text>{" "}
        <Text color="gray">
          P{issue.priority} · {issue.issue_type}
        </Text>
        {error ? <Text color="yellow"> (stale: {error})</Text> : null}
      </Text>
      <Box flexDirection="column" marginTop={1}>
        {visible.map((line, i) => (
          <Text key={i} wrap="wrap">
            {line || " "}
          </Text>
        ))}
        {totalRest > 0 ? (
          <Text color="gray">
            — {totalRest} more line(s) — j/k to scroll
          </Text>
        ) : null}
      </Box>
    </Box>
  );
}

/**
 * Pure helper — easy to unit-test. Builds the line list rendered inside
 * <BdShow>. Order: title, close_reason (if closed), description, metadata,
 * children. Sections that lack data are simply omitted.
 *
 * Children list is filtered to `dependency_type === "parent-child"` and
 * capped at CHILDREN_CAP with an `(+N more)` overflow line.
 */
export const CHILDREN_CAP = 20;

export function buildLines(issue: BdIssue): string[] {
  const out: string[] = [];
  if (issue.title) {
    out.push(issue.title, "");
  }
  if (issue.close_reason) {
    out.push(`CLOSE REASON: ${issue.close_reason}`, "");
  }
  if (issue.description) {
    out.push("DESCRIPTION", ...issue.description.split("\n"), "");
  }
  const meta = issue.metadata ?? {};
  const metaKeys = Object.keys(meta).sort();
  if (metaKeys.length > 0) {
    out.push("METADATA");
    for (const k of metaKeys) out.push(`  ${k}: ${meta[k]}`);
    out.push("");
  }
  const children = (issue.dependents ?? []).filter(
    (d) => d.dependency_type === "parent-child",
  );
  if (children.length > 0) {
    out.push(`CHILDREN (${children.length})`);
    for (const c of children.slice(0, CHILDREN_CAP)) {
      out.push(`  ${c.id} [${c.status}] ${c.title ?? ""}`);
      if (c.close_reason) out.push(`    ↳ ${c.close_reason}`);
    }
    if (children.length > CHILDREN_CAP) {
      out.push(`  (+${children.length - CHILDREN_CAP} more)`);
    }
  }
  return out;
}
