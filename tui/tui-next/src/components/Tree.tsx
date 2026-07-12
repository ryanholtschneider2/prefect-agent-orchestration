import React from "react";
import {Box, Text} from "ink";
import type {Epic, Issue} from "../domain/model.js";
import {epicRollup, lifecycleGroup} from "../domain/model.js";
import {age, truncateCells} from "../domain/text.js";
import type {Scope, UIState} from "../state/store.js";
import {stateGlyph, type Theme} from "../theme/theme.js";

const groups: Scope[] = ["active", "blocked", "failed", "completed", "archived"];
export function WorkTree({state, width, height, colors, ascii}: {state: UIState; width: number; height: number; colors: Theme; ascii: boolean}) {
  const lines: React.ReactNode[] = []; let used = 0; let selectedLine = 0;
  for (const group of groups) {
    const epics = state.model.epics.filter((epic) => state.scope === "all" ? lifecycleGroup(epic.state) === group : lifecycleGroup(epic.state) === group || epic.children.some((child) => lifecycleGroup(child.state) === group));
    const standalone = state.model.standalone.filter((issue) => lifecycleGroup(issue.state) === group);
    if (state.scope !== "all" && state.scope !== group || (!epics.length && !standalone.length)) continue;
    lines.push(<Text key={`g-${group}`} bold dimColor color={colors.muted}>{group.toUpperCase()}  {epics.length + standalone.length}</Text>); used++;
    for (const epic of epics) {
      const selected = state.selectedId === epic.id; const roll = epicRollup(epic); const marker = state.expanded.has(epic.id) ? (ascii ? "v" : "▾") : (ascii ? ">" : "▸");
      const suffix = width >= 48 ? `${roll.complete}/${roll.total} · ${roll.running} run${roll.blocked ? ` · ${roll.blocked} block` : ""} · ${age(epic.updatedAt)}` : `${roll.complete}/${roll.total} · ${age(epic.updatedAt)}`;
      const prefix = `${marker} ${stateGlyph(epic.state, ascii)} `; const row = `${prefix}${truncateCells(epic.title, Math.max(4, width - prefix.length - suffix.length - 1))} ${suffix}`;
      if (selected) selectedLine = lines.length; lines.push(<Text key={epic.id} inverse={selected} bold={selected}>{truncateCells(row, width)}</Text>); used++;
      if (state.expanded.has(epic.id)) for (const child of epic.children.filter((issue) => state.scope === "all" || lifecycleGroup(issue.state) === state.scope)) {
        const active = state.selectedId === child.id; const attempt = child.attempts[0]; const suffix2 = attempt ? `${attempt.roles.at(-1)?.role ?? attempt.formula ?? "run"} ${age(attempt.startedAt)}` : age(child.updatedAt);
        const prefix2 = `  ${ascii ? "-" : "└"} ${stateGlyph(child.state, ascii)} `; const attempts = child.attempts.length > 1 ? ` ×${child.attempts.length}` : ""; const row2 = `${prefix2}${truncateCells(child.title, Math.max(4, width - prefix2.length - suffix2.length - attempts.length - 1))} ${suffix2}${attempts}`;
        if (active) selectedLine = lines.length; lines.push(<Text key={child.id} inverse={active} bold={active}>{truncateCells(row2, width)}</Text>); used++;
      }
    }
    if (standalone.length) {
      lines.push(<Text key={`s-${group}`} dimColor>  STANDALONE WORK</Text>); used++;
      for (const issue of standalone) { const active = state.selectedId === issue.id; const suffix = age(issue.updatedAt); const row = `  ${stateGlyph(issue.state, ascii)} ${truncateCells(issue.title, Math.max(4, width - suffix.length - 6))} ${suffix}`; if (active) selectedLine = lines.length; lines.push(<Text key={issue.id} inverse={active} bold={active}>{truncateCells(row, width)}</Text>); used++; }
    }
  }
  if (!lines.length) lines.push(<Text key="empty" dimColor>No work in this scope. Press : to change scope.</Text>);
  const start = Math.max(0, selectedLine - height + 1);
  return <Box flexDirection="column" width={width} height={height} overflow="hidden">{lines.slice(start, start + height)}</Box>;
}
