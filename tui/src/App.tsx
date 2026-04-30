import fs from "node:fs";
import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useApp, useInput, useStdout } from "ink";
import Gradient from "ink-gradient";
import TextInput from "ink-text-input";

import { BdShow, buildLines } from "./components/BdShow.js";
import { FlowOverview } from "./components/FlowOverview.js";
import { IssueList } from "./components/IssueList.js";
import { RoleTimeline } from "./components/RoleTimeline.js";
import { TmuxTail } from "./components/TmuxTail.js";
import { useTicker } from "./hooks/useTicker.js";
import { activitySort, useStore } from "./state/store.js";
import { openAttachInNewWindow } from "./data/tmux.js";

/**
 * Sentinel file the wrapper (cli.tsx) reads after Ink exits. If present and
 * we exited cleanly, the wrapper execvp's `tmux attach -t <session>`.
 */
const ATTACH_SENTINEL = `${process.env.TMPDIR ?? "/tmp"}/po-tui-attach.${process.pid}`;

export interface AppProps {
  prefectUrl?: string;
  epicFilter?: string;
  refreshMs: number;
  mobile?: boolean;
}

export function App(props: AppProps): React.ReactElement {
  const { exit } = useApp();
  const { stdout } = useStdout();

  const issues = useStore((s) => s.issues);
  const selectedId = useStore((s) => s.selectedId);
  const setSelected = useStore((s) => s.setSelected);
  const filter = useStore((s) => s.filter);
  const setFilter = useStore((s) => s.setFilter);
  const lastError = useStore((s) => s.lastError);
  const tick = useStore((s) => s.tick);
  const refreshPane = useStore((s) => s.refreshPane);
  const paneText = useStore((s) => s.paneText);
  const paneSession = useStore((s) => s.paneSession);
  const hideCompleted = useStore((s) => s.hideCompleted);
  const toggleHideCompleted = useStore((s) => s.toggleHideCompleted);
  const drillIntoIssueId = useStore((s) => s.drillIntoIssueId);
  const setDrill = useStore((s) => s.setDrill);
  const bdShowVisible = useStore((s) => s.bdShowVisible);
  const bdShowCache = useStore((s) => s.bdShowCache);
  const bdShowError = useStore((s) => s.bdShowError);
  const setBdShowVisible = useStore((s) => s.setBdShowVisible);

  // Inject CLI args into the store on first render.
  useMemo(() => {
    useStore.setState({
      prefectUrl: props.prefectUrl,
      epicFilter: props.epicFilter,
      refreshMs: props.refreshMs,
    });
  }, [props.prefectUrl, props.epicFilter, props.refreshMs]);

  useTicker(tick, props.refreshMs);
  // Decoupled fast ticker for the tmux pane only — feels live without
  // hammering the Prefect API.
  useTicker(refreshPane, 500);

  const [filterMode, setFilterMode] = useState(false);
  const [filterDraft, setFilterDraft] = useState("");
  const [attachStatus, setAttachStatus] = useState<string | null>(null);
  const [bdScrollOffset, setBdScrollOffset] = useState(0);

  // Reset bd-show scroll on selection change so a new bead always opens
  // at the top.
  useEffect(() => {
    setBdScrollOffset(0);
  }, [selectedId]);

  // Auto-clear the attach toast after a few seconds.
  useEffect(() => {
    if (!attachStatus) return;
    const t = setTimeout(() => setAttachStatus(null), 3000);
    return () => clearTimeout(t);
  }, [attachStatus]);

  // Pre-filter for the nav order. The visual list does its own
  // hide/drill/filter so what you see and what you arrow through stay aligned.
  const navIssues = useMemo(() => {
    let v = issues;
    if (drillIntoIssueId) {
      const keep = new Set<string>();
      const add = (id: string): void => {
        if (keep.has(id)) return;
        keep.add(id);
        for (const c of issues) if (c.parentIssueId === id) add(c.issueId);
      };
      add(drillIntoIssueId);
      v = v.filter((i) => keep.has(i.issueId));
    }
    if (hideCompleted) {
      // "t" hides terminal states — show only active work (running / queued
      // / paused). COMPLETED, FAILED, CRASHED, CANCELLED all drop out.
      const TERMINAL = new Set([
        "COMPLETED",
        "FAILED",
        "CRASHED",
        "CANCELLED",
      ]);
      v = v.filter((i) => !TERMINAL.has(i.flowState ?? ""));
    }
    if (filter) {
      const f = filter.toLowerCase();
      v = v.filter(
        (i) =>
          i.issueId.toLowerCase().includes(f) ||
          (i.title ?? "").toLowerCase().includes(f),
      );
    }
    return [...v].sort(activitySort);
  }, [issues, filter, hideCompleted, drillIntoIssueId]);

  const selected = navIssues.find((i) => i.issueId === selectedId)
    ?? issues.find((i) => i.issueId === selectedId);

  // Sizing — needed early so the bd-show scroll math can clamp against it.
  const totalRows = stdout?.rows ?? 40;
  const totalCols = stdout?.columns ?? 120;
  const mobile = !!props.mobile;
  // On mobile we stack vertically, so the tail must share rows with the
  // issue list + timeline. Give it a smaller fixed slice.
  const tailHeight = mobile
    ? Math.max(6, Math.floor(totalRows / 3))
    : Math.max(8, Math.floor(totalRows / 2) - 4);
  // Left panel grows with the terminal up to a cap; beyond that the right
  // pane absorbs the rest. Min 54 (legacy width), max 96 — wide enough to
  // show full issue stems + role names without truncation.
  const leftWidth = mobile
    ? undefined
    : Math.min(Math.max(54, Math.floor(totalCols * 0.42)), 96);

  // Bd-show scroll math: clamp `bdScrollOffset` so we can't scroll past the
  // last line. `maxBdOffset` is recomputed each render — the cached issue,
  // its description length, and the terminal height can all change.
  const bdShowIssue = selectedId ? bdShowCache[selectedId] ?? null : null;
  const bdShowLineCount = useMemo(
    () => (bdShowIssue ? buildLines(bdShowIssue).length : 0),
    [bdShowIssue],
  );
  const maxBdOffset = Math.max(0, bdShowLineCount - tailHeight);

  useInput((input, key) => {
    if (filterMode) return; // TextInput owns keys

    if (input === "q" || (key.ctrl && input === "c")) {
      exit();
      return;
    }
    if (input === "b") {
      setBdShowVisible(!bdShowVisible);
      return;
    }
    if (bdShowVisible) {
      if (input === "j") {
        setBdScrollOffset((o) => Math.min(o + 1, maxBdOffset));
        return;
      }
      if (input === "k") {
        setBdScrollOffset((o) => Math.max(o - 1, 0));
        return;
      }
      if (input === "g") {
        setBdScrollOffset(0);
        return;
      }
      if (input === "G") {
        setBdScrollOffset(maxBdOffset);
        return;
      }
    }
    if (input === "r") {
      void tick();
      return;
    }
    if (input === "/") {
      setFilterDraft(filter);
      setFilterMode(true);
      return;
    }
    if (input === "t") {
      toggleHideCompleted();
      return;
    }
    if (input === "e") {
      // Drill into the selected row's nearest parent (or the row itself if
      // it's already a parent of something). Pressing again drills further.
      // Pressing "E" pops back out.
      if (selected) {
        const target = selected.parentIssueId ?? selected.issueId;
        setDrill(target);
      }
      return;
    }
    if (input === "E") {
      setDrill(null);
      return;
    }
    if (input === "a") {
      if (!paneSession) return;
      // Try to spawn tmux attach in a new tab/window so po-tui keeps running.
      void (async () => {
        const method = await openAttachInNewWindow(paneSession);
        if (method) {
          setAttachStatus(`attached in: ${method}`);
        } else {
          setAttachStatus("no terminal found — set $PO_TUI_TERMINAL");
        }
      })();
      return;
    }
    if (input === "A") {
      // Capital A: legacy in-place attach (replaces the TUI, returns on detach).
      if (paneSession) {
        try {
          fs.writeFileSync(ATTACH_SENTINEL, paneSession, "utf8");
        } catch {
          /* best-effort */
        }
        exit();
      }
      return;
    }
    if (key.upArrow) {
      moveWithinSiblings(-1);
      return;
    }
    if (key.downArrow) {
      moveWithinSiblings(1);
      return;
    }
    if (key.rightArrow) {
      moveIntoChild();
      return;
    }
    if (key.leftArrow) {
      moveToParent();
      return;
    }
  });

  /** Siblings of `id` in the current navIssues view (same parent, sorted). */
  function siblingsOf(id: string | null): typeof navIssues {
    if (!id) return navIssues.filter((i) => !i.parentIssueId);
    const row = navIssues.find((i) => i.issueId === id);
    if (!row) return navIssues;
    if (!row.parentIssueId) return navIssues.filter((i) => !i.parentIssueId);
    const parent = navIssues.find((i) => i.issueId === row.parentIssueId);
    if (!parent) return navIssues.filter((i) => !i.parentIssueId);
    return navIssues.filter((i) => i.parentIssueId === parent.issueId);
  }

  function moveWithinSiblings(delta: number): void {
    const sibs = siblingsOf(selectedId);
    if (sibs.length === 0) return;
    const idx = sibs.findIndex((i) => i.issueId === selectedId);
    const next = (idx === -1 ? 0 : idx + delta + sibs.length) % sibs.length;
    const target = sibs[next];
    if (target) setSelected(target.issueId);
  }

  function moveIntoChild(): void {
    if (!selected) return;
    const visibleChildIds = selected.childIssueIds.filter((cid) =>
      navIssues.some((i) => i.issueId === cid),
    );
    if (visibleChildIds.length === 0) return;
    const childRows = navIssues.filter((i) => visibleChildIds.includes(i.issueId));
    const target = childRows[0]; // already sorted by activitySort within navIssues
    if (target) setSelected(target.issueId);
  }

  function moveToParent(): void {
    if (!selected?.parentIssueId) return;
    const parent = navIssues.find((i) => i.issueId === selected.parentIssueId);
    if (parent) setSelected(parent.issueId);
  }

  const summary = useMemo(() => {
    let running = 0;
    let failed = 0;
    let done = 0;
    for (const i of issues) {
      if (i.flowState === "RUNNING") running++;
      else if (i.flowState === "FAILED" || i.flowState === "CRASHED") failed++;
      else if (i.flowState === "COMPLETED") done++;
    }
    return { running, failed, done };
  }, [issues]);

  return (
    <Box flexDirection="column">
      {/* Header — collapse to two lines on mobile so counts don't overflow. */}
      <Box
        flexDirection={mobile ? "column" : "row"}
        justifyContent="space-between"
        paddingX={1}
      >
        <Box>
          <Gradient name="cristal">
            <Text bold>po · {props.epicFilter ?? "all"}</Text>
          </Gradient>
        </Box>
        <Box>
          <Text color="cyan">run:{summary.running}</Text>
          <Text> </Text>
          <Text color="green">ok:{summary.done}</Text>
          <Text> </Text>
          <Text color="red">fail:{summary.failed}</Text>
          {!mobile ? (
            <>
              <Text>  </Text>
              <Text color="gray">refresh {props.refreshMs}ms</Text>
            </>
          ) : null}
        </Box>
      </Box>

      {lastError ? (
        <Box paddingX={1}>
          <Text color="red">! {lastError}</Text>
        </Box>
      ) : null}

      {attachStatus ? (
        <Box paddingX={1}>
          <Text color="cyan">↗ {attachStatus}</Text>
        </Box>
      ) : null}

      {/* Main layout: 2-col on desktop, stacked on mobile.
          On mobile the right pane (timeline + tmux tail) is hidden by default
          and only renders once the user drills into a row with `e` — keeps
          the issue list usable on a phone-sized terminal. */}
      <Box flexDirection={mobile ? "column" : "row"}>
        <IssueList
          issues={issues}
          selectedId={selectedId}
          filter={filter}
          hideCompleted={hideCompleted}
          drillIntoIssueId={drillIntoIssueId}
          mobile={mobile}
          width={leftWidth}
        />
        {mobile && !drillIntoIssueId ? null : (
          <Box flexDirection="column" flexGrow={1} borderStyle="round" borderColor="gray">
            <FlowOverview flowName={selected?.flowName} />
            <RoleTimeline issue={selected} allIssues={issues} />
            <Box borderStyle="single" borderColor="gray" flexDirection="column">
              {bdShowVisible ? (
                <BdShow
                  issue={bdShowIssue}
                  issueId={selectedId}
                  error={bdShowError}
                  height={tailHeight}
                  scrollOffset={Math.min(bdScrollOffset, maxBdOffset)}
                />
              ) : (
                <TmuxTail text={paneText} session={paneSession} height={tailHeight} />
              )}
            </Box>
          </Box>
        )}
      </Box>

      {/* Footer / hotkeys */}
      <Box paddingX={1} flexDirection="row" justifyContent="space-between">
        {filterMode ? (
          <Box>
            <Text color="cyan">/ </Text>
            <TextInput
              value={filterDraft}
              onChange={setFilterDraft}
              onSubmit={(v) => {
                setFilter(v);
                setFilterMode(false);
              }}
            />
          </Box>
        ) : (
          <Text color="gray">
            {mobile
              ? "[↑↓←→] nav [a] attach [/] filt [e/E] drill [t] active-only [r] ref [q]"
              : "[↑↓] sibs  [←→] tree  [a] new-tab  [A] in-place  [r] refresh  [/] filter  [e] drill-in  [E] reset  [t] active-only  [b] bd-show  [q] quit"}
          </Text>
        )}
      </Box>
    </Box>
  );
}

export { ATTACH_SENTINEL };
