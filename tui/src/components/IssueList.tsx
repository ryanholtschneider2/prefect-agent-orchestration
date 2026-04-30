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

const TERMINAL = new Set(["COMPLETED", "FAILED", "CRASHED", "CANCELLED"]);

interface Props {
  issues: IssueRow[];
  selectedId: string | null;
  filter: string;
  hideCompleted: boolean;
  drillIntoIssueId: string | null;
  showDone: boolean;
  mobile?: boolean;
  width?: number;
}

interface DisplayRow {
  issue: IssueRow;
  depth: number;
  stem: string;
}

interface GroupBlock {
  root?: IssueRow;
  rows: DisplayRow[];
  prefix: string;
}

export function humanWall(ms: number): string {
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem > 0 ? `${h}h${rem}m` : `${h}h`;
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
    const stripPrefix =
      kids.length >= 1 ? commonIdPrefix(allInGroup.map((i) => i.issueId)) : "";
    const rows: DisplayRow[] = [
      {
        issue: root,
        depth: 0,
        stem: root.issueId.slice(stripPrefix.length) || root.issueId,
      },
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
  showDone,
  mobile,
  width,
}: Props): React.ReactElement {
  let visible = issues;

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

  if (filter) {
    const f = filter.toLowerCase();
    visible = visible.filter(
      (i) =>
        i.issueId.toLowerCase().includes(f) ||
        (i.title ?? "").toLowerCase().includes(f),
    );
  }

  // Split into running (non-terminal) and done (terminal).
  const running = visible.filter((i) => !TERMINAL.has(i.flowState ?? "")).sort(activitySort);
  const done = visible.filter((i) => TERMINAL.has(i.flowState ?? "")).sort((a, b) => {
    const sa = a.startTime ?? "";
    const sb = b.startTime ?? "";
    return sb.localeCompare(sa); // newest first
  });

  const stuckCount = running.filter((i) => i.stuck).length;

  // When hideCompleted is on, also hide done rows unless showDone is set.
  const showDoneRows = !hideCompleted && (showDone || done.length <= 5);

  // Split groups so each row renders exactly once: runningGroups owns the
  // top block, doneGroups owns the block below the separator.
  const runningGroups = buildGroups(running);
  const doneGroups = showDoneRows ? buildGroups(done) : [];

  const panelWidth = mobile ? 0 : Math.max(40, width ?? 54);

  const allStems = [...runningGroups, ...doneGroups].flatMap((g) =>
    g.rows.map((r) => r.stem),
  );
  const stemMaxLen = allStems.reduce((m, s) => Math.max(m, s.length), 0);
  // Reserve: cursor(2) + paddingX(2) + border(2) + flowMode(5) + wall(6) + step(16) + stuck(2) = 35
  const reserved = 6 + 5 + 6 + 16 + 2;
  const idCapByPanel = Math.max(8, panelWidth - reserved);
  const idColWidth = mobile
    ? Math.min(Math.max(stemMaxLen + 1, 6), 28)
    : Math.min(Math.max(stemMaxLen + 1, 6), idCapByPanel);

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
          ISSUES
        </Text>
        <Text color="gray">
          {" "}
          {running.length} running
          {stuckCount > 0 ? <Text color="red">, {stuckCount} stuck</Text> : null},{" "}
          {done.length} done
        </Text>
      </Box>
      <Box flexDirection="column" marginTop={1}>
        {running.length === 0 && done.length === 0 ? (
          <Text color="gray">no flow runs found</Text>
        ) : (
          <>
            {runningGroups.flatMap((group, gi) =>
              renderGroupRows(group, gi, running, selectedId, idColWidth, mobile),
            )}
            {done.length > 0 && (
              <Box key="done-sep">
                <Text color="gray">
                  ─ {done.length} done{!showDoneRows ? <Text> [D to expand]</Text> : null}
                </Text>
              </Box>
            )}
            {doneGroups.flatMap((group, gi) =>
              renderGroupRows(group, 1000 + gi, done, selectedId, idColWidth, mobile),
            )}
          </>
        )}
      </Box>
    </Box>
  );
}

function renderGroupRows(
  group: GroupBlock,
  gi: number,
  _ctx: IssueRow[],
  selectedId: string | null,
  idColWidth: number,
  mobile: boolean | undefined,
): React.ReactElement[] {
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
    const indent = dr.depth > 0 ? "  └ " : "";
    const wall = dr.issue.wallMs > 0 ? humanWall(dr.issue.wallMs) : "";
    const wallColor = dr.issue.wallMs > 3_600_000 ? "red" : "gray";

    blocks.push(
      <Box key={dr.issue.issueId} flexDirection="row">
        <Text color={isSel ? "cyan" : "gray"}>{isSel ? "▶ " : "  "}</Text>
        <Box width={idColWidth}>
          <Text color={isSel ? "white" : "gray"} bold={isSel} wrap="truncate-end">
            {indent}
            {dr.stem}
          </Text>
        </Box>
        {!mobile && (
          <>
            <Box width={5}>
              <Text color="gray" wrap="truncate-end">
                {dr.issue.flowMode}
              </Text>
            </Box>
            <Box width={6}>
              <Text color={wallColor} wrap="truncate-end">
                {wall}
              </Text>
            </Box>
            <Box width={16}>
              <Text color={color} wrap="truncate-end">
                {dr.issue.stepLabel}
              </Text>
            </Box>
            <Box width={2}>
              {dr.issue.stuck ? <Text color="red">⚠</Text> : <Text>  </Text>}
            </Box>
          </>
        )}
        <Box flexGrow={1}>
          <Text color={isSel ? "white" : "gray"} wrap="truncate-end">
            {dr.issue.title ?? ""}
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
