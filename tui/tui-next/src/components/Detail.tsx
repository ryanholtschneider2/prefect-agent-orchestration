import React from "react";
import {Box, Text} from "ink";
import type {Epic, Issue} from "../domain/model.js";
import {epicRollup} from "../domain/model.js";
import {age, truncateCells} from "../domain/text.js";
import type {ActivityRecord, UIState} from "../state/store.js";
import {stateGlyph, type Theme} from "../theme/theme.js";

const Label = ({children}: {children: React.ReactNode}) => <Text bold>{children}</Text>;
function EpicDetail({epic, width}: {epic: Epic; width: number}) {
  const roll = epicRollup(epic); const blockers = epic.children.filter((issue) => issue.state === "blocked"); const active = epic.children.filter((issue) => issue.state === "in_progress");
  return <Box flexDirection="column">
    <Text bold>{truncateCells(epic.title, width)}</Text><Text dimColor>{epic.id} · {epic.state} · updated {age(epic.updatedAt)} ago</Text>
    <Text> </Text><Label>Progress</Label><Text>{roll.complete}/{roll.total} complete · {roll.running} running · {roll.blocked} blocked · {roll.failed} failed</Text>
    <Text> </Text><Label>Dependencies</Label>{epic.dependencies.length ? epic.dependencies.map((dep) => <Text key={dep.id}> {dep.type} {dep.id}</Text>) : <Text dimColor> None declared</Text>}
    <Text> </Text><Label>Blockers & decisions</Label>{blockers.length ? blockers.map((issue) => <Text key={issue.id}> ! {issue.id} {truncateCells(issue.title, width - issue.id.length - 4)}</Text>) : <Text dimColor> None declared</Text>}
    <Text> </Text><Label>Active work</Label>{active.length ? active.map((issue) => <Text key={issue.id}> {stateGlyph(issue.state)} {issue.id} · {issue.attempts[0]?.roles.at(-1)?.role ?? "awaiting execution data"}</Text>) : <Text dimColor> No active children</Text>}
    <Text> </Text><Label>Recent outcomes</Label>{epic.children.slice().sort((a,b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")).slice(0, 5).map((issue) => <Text key={issue.id}> {stateGlyph(issue.state)} {issue.id} · {age(issue.updatedAt)}</Text>)}
  </Box>;
}
function IssueDetail({issue, width, liveOutput}: {issue: Issue; width: number; liveOutput?: string}) {
  const attempt = issue.attempts[0];
  return <Box flexDirection="column">
    <Text bold>{truncateCells(issue.title, width)}</Text><Text dimColor>{issue.id} · {issue.state}{issue.assignee ? ` · ${issue.assignee}` : ""}</Text>
    <Text> </Text><Label>Current attempt</Label>{attempt ? <><Text>{stateGlyph(attempt.state)} {attempt.state} · {attempt.formula ?? "unknown formula"} · {age(attempt.startedAt)}</Text><Text dimColor>{Object.entries(attempt.runtime).map(([k,v]) => `${k}=${v}`).join(" · ") || "runtime tuple unavailable"}</Text></> : <Text dimColor>No Prefect attempt linked by stable ID.</Text>}
    <Text> </Text><Label>Role timeline</Label>{attempt?.roles.length ? attempt.roles.map((role) => <Text key={role.id}> {stateGlyph(role.state)} {role.role} · {role.state} · iter {role.iteration}</Text>) : <Text dimColor>No task-run data available.</Text>}
    <Text> </Text><Label>Live agent output</Label>{liveOutput ? liveOutput.split("\n").slice(-6).map((line, index) => <Text key={index} dimColor>{truncateCells(line, width)}</Text>) : <Text dimColor>No active agent session.</Text>}
    <Text> </Text><Label>Attempt history</Label>{issue.attempts.length ? issue.attempts.map((item) => <Text key={item.id}> {stateGlyph(item.state)} {item.id.slice(0,8)} · {item.state} · {age(item.startedAt)}</Text>) : <Text dimColor>No attempts.</Text>}
    <Text> </Text><Label>Artifacts</Label>{issue.artifacts.length ? issue.artifacts.slice(0, 5).map((item) => <Text key={item.path}> {item.kind} · {item.name}</Text>) : <Text dimColor>No artifacts discovered.</Text>}
    <Text> </Text><Label>Dependencies</Label><Text dimColor>{issue.dependencies.map((dep) => `${dep.type} ${dep.id}`).join(" · ") || "None declared"}</Text>
  </Box>;
}
export function Detail({object, state, width, height, colors}: {object?: Epic | Issue; state: UIState; width: number; height: number; colors: Theme}) {
  if (!object) return <Text dimColor>Select an epic or issue.</Text>;
  const tmux = state.model.snapshots.tmux.data as {output?: string} | undefined;
  const artifacts = object.kind === "issue" ? object.artifacts : object.children.flatMap((issue) => issue.artifacts);
  return <Box width={width} height={height} flexDirection="column" overflow="hidden"><Text color={colors.accent} bold>{object.kind === "epic" ? "EPIC OVERVIEW" : "ISSUE EXECUTION"}</Text><Text dimColor>overview  activity  artifacts  description</Text><Text> </Text>{state.detailTab === "activity" ? <Activity records={state.activity} /> : state.detailTab === "artifacts" ? <Box flexDirection="column">{artifacts.length ? artifacts.map((artifact) => <Text key={artifact.path}>{artifact.kind.padEnd(6)} {artifact.name} <Text dimColor>{artifact.path}</Text></Text>) : <Text dimColor>No artifacts discovered for this selection.</Text>}</Box> : state.detailTab === "description" ? <Text>{object.kind === "issue" ? object.description || "No description." : "Epic detail is composed from declared child state."}</Text> : object.kind === "epic" ? <EpicDetail epic={object} width={width} /> : <IssueDetail issue={object} width={width} liveOutput={tmux?.output} />}</Box>;
}
function Activity({records}: {records: ActivityRecord[]}) { return <Box flexDirection="column">{records.length ? records.map((record, index) => <Text key={`${record.at}-${index}`}>{record.verification === "verified" ? "+" : record.verification === "failed" ? "x" : "~"} {record.operation} · {record.result}</Text>) : <Text dimColor>No local actions in this session.</Text>}</Box>; }
