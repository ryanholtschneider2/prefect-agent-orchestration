import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";

import { findRunningDescendant, type IssueRow, type RoleSlot } from "../state/store.js";

interface Props {
  issue: IssueRow | undefined;
  allIssues: IssueRow[];
}

export function RoleTimeline({ issue, allIssues }: Props): React.ReactElement {
  if (!issue) {
    return (
      <Box paddingX={1}>
        <Text color="gray">no issue selected</Text>
      </Box>
    );
  }

  const byId = new Map(allIssues.map((i) => [i.issueId, i]));
  const running = !issue.activeRole ? findRunningDescendant(issue, byId) : null;
  // If the selected row is a parent without its own active task, render the
  // descendant's timeline so the user sees real progress instead of a wall
  // of grey roles.
  const display = running ? running.issue : issue;
  const isPivoted = !!running;

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold color="white">
        ROLE TIMELINE — <Text color="cyan">{issue.issueId}</Text>{" "}
        <Text color="gray">
          {issue.flowStateName ?? issue.flowState ?? ""}
          {issue.epicId ? `  ·  epic ${issue.epicId}` : ""}
        </Text>
      </Text>
      {isPivoted ? (
        <Text color="yellow">
          → active subtask: <Text color="cyan">{display.issueId}</Text>{" "}
          <Text color="gray">({display.flowStateName ?? display.flowState})</Text>
        </Text>
      ) : null}
      {display.title ? (
        <Text color="gray" wrap="truncate-end">
          {display.title}
        </Text>
      ) : null}
      <Box flexDirection="row" marginTop={1} flexWrap="wrap">
        {display.roles.map((slot) => (
          <RoleCell key={slot.role} slot={slot} />
        ))}
      </Box>
      {!isPivoted && display.roles.length === 0 ? (
        <Box marginTop={1}>
          <Text color="gray">
            (no task runs yet
            {issue.childIssueIds.length
              ? ` — ${issue.childIssueIds.length} child flow(s) dispatched`
              : ""}
            )
          </Text>
        </Box>
      ) : null}
    </Box>
  );
}

function RoleCell({ slot }: { slot: RoleSlot }): React.ReactElement {
  const { color, glyph } = visualFor(slot);
  return (
    <Box marginRight={2}>
      <Text color={color}>
        {slot.role} {glyph}
      </Text>
    </Box>
  );
}

function visualFor(slot: RoleSlot): {
  color: string;
  glyph: React.ReactNode;
} {
  switch (slot.state) {
    case "succeeded":
      return { color: "green", glyph: "✓" };
    case "running":
      return {
        color: "cyan",
        glyph: (
          <Text color="cyan">
            <Spinner type="dots" />
          </Text>
        ),
      };
    case "looping":
      return { color: "yellow", glyph: `⟲${slot.iterations}` };
    case "failed":
      return { color: "red", glyph: "✗" };
    case "paused":
      return { color: "yellow", glyph: "⏸" };
    case "cancelled":
      return { color: "magenta", glyph: "⊘" };
    case "not_started":
    default:
      return { color: "gray", glyph: "·" };
  }
}
