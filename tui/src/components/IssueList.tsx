import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";

import { activitySort, type IssueRow } from "../state/store.js";

const STATE_COLORS: Record<string, string> = {
  RUNNING: "cyan",
  COMPLETED: "green",
  FAILED: "red",
  CRASHED: "red",
  CANCELLED: "yellow",
  PAUSED: "yellow",
  PENDING: "gray",
  SCHEDULED: "gray",
};

interface Props {
  issues: IssueRow[];
  selectedId: string | null;
  filter: string;
  hideCompleted: boolean;
  drillIntoIssueId: string | null;
  mobile?: boolean;
  width?: number;
}

interface DisplayRow {
  issue: IssueRow;
  depth: number;
  /** Stem rendered for this row (id minus per-group prefix). */
  stem: string;
}

interface GroupBlock {
  /** Root row for this group (a flow run with no parent in scope). */
  root?: IssueRow;
  /** All rows in this group, ordered for display (root first, then children DFS). */
  rows: DisplayRow[];
  /** The shared prefix that was stripped, if any. */
  prefix: string;
}

function commonIdPrefix(ids: string[]): string {
  if (ids.length < 2) return "";
  let prefix = ids[0]!;
  for (const id of ids.slice(1)) {
    let i = 0;
    const max = Math.min(prefix.length, id.length);
    while (i < max && prefix[i] === id[i]) i++;
    prefix = prefix.slice(0, i);
    if (!prefix) return "";
  }
  const lastBoundary = Math.max(prefix.lastIndexOf("-"), prefix.lastIndexOf("."));
  if (lastBoundary <= 0) return "";
  const trimmed = prefix.slice(0, lastBoundary + 1);
  if (ids.some((id) => id.slice(trimmed.length).length < 2)) return "";
  return trimmed;
}

/** Build groups by Prefect-native parent linkage; rows whose parent is missing
 *  from the visible set are treated as roots. */
function buildGroups(issues: IssueRow[]): GroupBlock[] {
  const visible = new Set(issues.map((i) => i.issueId));
  const childrenByParent = new Map<string, IssueRow[]>();
  const roots: IssueRow[] = [];
  for (const iss of issues) {
    if (iss.parentIssueId && visible.has(iss.parentIssueId)) {
      const arr = childrenByParent.get(iss.parentIssueId) ?? [];
      arr.push(iss);
      childrenByParent.set(iss.parentIssueId, arr);
    } else {
      roots.push(iss);
    }
  }

  roots.sort(activitySort);
  for (const arr of childrenByParent.values()) arr.sort(activitySort);

  const groups: GroupBlock[] = [];
  for (const root of roots) {
    const kids = childrenByParent.get(root.issueId) ?? [];
    const allInGroup = [root, ...kids];
    const stripPrefix = kids.length >= 1
      ? commonIdPrefix(allInGroup.map((i) => i.issueId))
      : "";
    const rows: DisplayRow[] = [
      { issue: root, depth: 0, stem: root.issueId.slice(stripPrefix.length) || root.issueId },
      ...kids.map((k) => ({
        issue: k,
        depth: 1,
        stem: k.issueId.slice(stripPrefix.length) || k.issueId,
      })),
    ];
    groups.push({ root, rows, prefix: stripPrefix });
  }
  return groups;
}

export function IssueList({
  issues,
  selectedId,
  filter,
  hideCompleted,
  drillIntoIssueId,
  mobile,
  width,
}: Props): React.ReactElement {
  let visible = issues;

  // Drill-in: only show the targeted issue and its descendants.
  if (drillIntoIssueId) {
    const keep = new Set<string>();
    const addDescendants = (id: string): void => {
      if (keep.has(id)) return;
      keep.add(id);
      for (const i of issues) {
        if (i.parentIssueId === id) addDescendants(i.issueId);
      }
    };
    addDescendants(drillIntoIssueId);
    visible = visible.filter((i) => keep.has(i.issueId));
  }

  if (hideCompleted) {
    // Keep parents of any visible active descendant so the tree stays legible.
    // "Active" = anything not in a terminal state.
    const TERMINAL = new Set(["COMPLETED", "FAILED", "CRASHED", "CANCELLED"]);
    const keepers = new Set(
      visible
        .filter((i) => !TERMINAL.has(i.flowState ?? ""))
        .map((i) => i.issueId),
    );
    let grew = true;
    while (grew) {
      grew = false;
      for (const i of visible) {
        if (!keepers.has(i.issueId)) continue;
        if (i.parentIssueId && !keepers.has(i.parentIssueId)) {
          keepers.add(i.parentIssueId);
          grew = true;
        }
      }
    }
    visible = visible.filter((i) => keepers.has(i.issueId));
  }

  if (filter) {
    const f = filter.toLowerCase();
    visible = visible.filter(
      (i) =>
        i.issueId.toLowerCase().includes(f) ||
        (i.title ?? "").toLowerCase().includes(f),
    );
  }

  const groups = buildGroups(visible);
  const allStems = groups.flatMap((g) => g.rows.map((r) => r.stem));
  const stemMaxLen = allStems.reduce((m, s) => Math.max(m, s.length), 0);
  // Panel width drives column sizing. Reserve ~6 cols for the cursor +
  // padding/borders, ~16 for active-role + glyph. Whatever's left goes to
  // the id stem so longer ids don't truncate as the terminal grows.
  const panelWidth = mobile ? 0 : Math.max(40, width ?? 54);
  const reserved = 6 + 11 + 3; // cursor(2) + paddingX(2) + border(2) ≈ 6, active col 11, glyph 3
  const idCapByPanel = Math.max(8, panelWidth - reserved);
  const idColWidth = mobile
    ? Math.min(Math.max(stemMaxLen + 1, 6), 28)
    : Math.min(Math.max(stemMaxLen + 1, 6), idCapByPanel);
  // Active-role column expands a bit on wider panels so role names like
  // "regression-gate" stop truncating. Cap at 18.
  const activeColWidth = mobile
    ? 11
    : Math.min(18, Math.max(11, panelWidth - reserved - idColWidth + 11));

  const totalRows = visible.length;

  return (
    <Box
      flexDirection="column"
      width={mobile ? undefined : panelWidth}
      flexGrow={mobile ? 1 : undefined}
      flexShrink={0}
      borderStyle="round"
      borderColor="gray"
      paddingX={1}
    >
      <Box flexDirection="row" justifyContent="space-between">
        <Text bold color="white">
          ISSUES <Text color="gray">({totalRows})</Text>
        </Text>
        <Text color="gray">
          {hideCompleted ? "·active-only " : ""}
          {drillIntoIssueId ? "·drilled" : ""}
        </Text>
      </Box>
      <Box flexDirection="column" marginTop={1}>
        {totalRows === 0 ? (
          <Text color="gray">no flow runs found</Text>
        ) : (
          groups.flatMap((group, gi) => {
            const blocks: React.ReactElement[] = [];
            if (group.prefix) {
              blocks.push(
                <Box key={`hdr-${gi}`}>
                  <Text color="gray">
                    ── <Text color="cyan">{group.prefix}</Text>
                    <Text color="gray">…</Text>
                  </Text>
                </Box>,
              );
            }
            for (const dr of group.rows) {
              const isSel = dr.issue.issueId === selectedId;
              const color = STATE_COLORS[dr.issue.flowState ?? ""] ?? "white";
              const active = dr.issue.activeRole ?? "—";
              const indent = dr.depth > 0 ? "  └ " : "";
              blocks.push(
                <Box key={dr.issue.issueId} flexDirection="row">
                  <Text color={isSel ? "cyan" : "gray"}>{isSel ? "▶ " : "  "}</Text>
                  <Box width={idColWidth}>
                    <Text color={isSel ? "white" : "gray"} bold={isSel} wrap="truncate-end">
                      {indent}
                      {dr.stem}
                    </Text>
                  </Box>
                  <Box width={activeColWidth}>
                    <Text color={color} wrap="truncate-end">
                      {active}
                    </Text>
                  </Box>
                  <Box width={3}>
                    {dr.issue.flowState === "RUNNING" ? (
                      <Text color="cyan">
                        <Spinner type="dots" />
                      </Text>
                    ) : (
                      <Text color={color}>{glyphFor(dr.issue.flowState)}</Text>
                    )}
                  </Box>
                </Box>,
              );
            }
            return blocks;
          })
        )}
      </Box>
    </Box>
  );
}

function glyphFor(state: string | undefined): string {
  switch (state) {
    case "COMPLETED":
      return "✓";
    case "FAILED":
    case "CRASHED":
      return "✗";
    case "CANCELLED":
      return "⊘";
    case "PAUSED":
      return "⏸";
    case "PENDING":
    case "SCHEDULED":
      return "·";
    default:
      return "·";
  }
}
