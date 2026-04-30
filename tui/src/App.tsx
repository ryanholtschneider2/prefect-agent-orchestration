import fs from "node:fs";
import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useApp, useInput, useStdout } from "ink";
import Gradient from "ink-gradient";
import TextInput from "ink-text-input";

import { buildLines } from "./components/BdShow.js";
import { DetailTabs } from "./components/DetailTabs.js";
import { IssueList } from "./components/IssueList.js";
import { useTicker } from "./hooks/useTicker.js";
import { activitySort, useStore, type TabName } from "./state/store.js";
import { openAttachInNewWindow } from "./data/tmux.js";

const ATTACH_SENTINEL = `${process.env.TMPDIR ?? "/tmp"}/po-tui-attach.${process.pid}`;

export interface AppProps {
  prefectUrl?: string;
  epicFilter?: string;
  refreshMs: number;
  mobile?: boolean;
}

type DispatchStep = "issue" | "flow" | "rig" | "rigPath" | "confirm";

interface DispatchForm {
  step: DispatchStep;
  values: Partial<Record<"issue" | "flow" | "rig" | "rigPath", string>>;
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
  const drillIntoIssueId = useStore((s) => s.drillIntoIssueId);
  const setDrill = useStore((s) => s.setDrill);
  const bdShowCache = useStore((s) => s.bdShowCache);
  const bdShowError = useStore((s) => s.bdShowError);
  const selectedTab = useStore((s) => s.selectedTab);
  const setSelectedTab = useStore((s) => s.setSelectedTab);
  const showDone = useStore((s) => s.showDone);
  const toggleShowDone = useStore((s) => s.toggleShowDone);
  const pendingConfirm = useStore((s) => s.pendingConfirm);
  const setPendingConfirm = useStore((s) => s.setPendingConfirm);
  const hideCompleted = useStore((s) => s.hideCompleted);

  useMemo(() => {
    useStore.setState({
      prefectUrl: props.prefectUrl,
      epicFilter: props.epicFilter,
      refreshMs: props.refreshMs,
    });
  }, [props.prefectUrl, props.epicFilter, props.refreshMs]);

  useTicker(tick, props.refreshMs);
  useTicker(refreshPane, 500);

  const [filterMode, setFilterMode] = useState(false);
  const [filterDraft, setFilterDraft] = useState("");
  const [attachStatus, setAttachStatus] = useState<string | null>(null);
  const [bdScrollOffset, setBdScrollOffset] = useState(0);
  const [dispatchForm, setDispatchForm] = useState<DispatchForm | null>(null);
  const [dispatchDraft, setDispatchDraft] = useState("");

  useEffect(() => {
    setBdScrollOffset(0);
  }, [selectedId, selectedTab]);

  useEffect(() => {
    if (!attachStatus) return;
    const t = setTimeout(() => setAttachStatus(null), 3000);
    return () => clearTimeout(t);
  }, [attachStatus]);

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
      const TERMINAL = new Set(["COMPLETED", "FAILED", "CRASHED", "CANCELLED"]);
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

  const totalRows = stdout?.rows ?? 40;
  const totalCols = stdout?.columns ?? 120;
  const mobile = !!props.mobile;
  const tailHeight = mobile
    ? Math.max(6, Math.floor(totalRows / 3))
    : Math.max(8, Math.floor(totalRows / 2) - 4);
  const leftWidth = mobile
    ? undefined
    : Math.min(Math.max(54, Math.floor(totalCols * 0.42)), 96);

  const bdShowIssue = selectedId ? bdShowCache[selectedId] ?? null : null;
  const bdShowLineCount = useMemo(
    () => (bdShowIssue ? buildLines(bdShowIssue).length : 0),
    [bdShowIssue],
  );
  const maxBdOffset = Math.max(0, bdShowLineCount - tailHeight);

  const TAB_ORDER: TabName[] = ["LIVE", "TRACE", "BD", "ACTIONS"];

  useInput((input, key) => {
    if (filterMode) return;

    // Dispatch form input handling
    if (dispatchForm !== null) {
      if (key.escape) {
        setDispatchForm(null);
        setDispatchDraft("");
        return;
      }
      // TextInput handles its own chars; only intercept Escape
      return;
    }

    // Confirmation overlay
    if (pendingConfirm) {
      if (input === "y") {
        void runConfirmedAction(pendingConfirm);
        setPendingConfirm(null);
      } else {
        setPendingConfirm(null);
      }
      return;
    }

    if (input === "q" || (key.ctrl && input === "c")) {
      exit();
      return;
    }

    // Tab navigation
    if (input === "t") {
      const next = TAB_ORDER[(TAB_ORDER.indexOf(selectedTab) + 1) % TAB_ORDER.length]!;
      setSelectedTab(next);
      return;
    }
    if (input === "1") { setSelectedTab("LIVE"); return; }
    if (input === "2") { setSelectedTab("TRACE"); return; }
    if (input === "3") { setSelectedTab("BD"); return; }
    if (input === "4") { setSelectedTab("ACTIONS"); return; }

    // BD tab scroll
    if (selectedTab === "BD") {
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

    if (input === "D") { toggleShowDone(); return; }

    if (input === "c" && selected) {
      setPendingConfirm({ action: "cancel", issueId: selected.issueId });
      return;
    }
    if (input === "r" && selected) {
      setPendingConfirm({ action: "retry", issueId: selected.issueId });
      return;
    }
    if (input === "d") {
      setDispatchForm({ step: "issue", values: {} });
      setDispatchDraft(selected?.issueId ?? "");
      return;
    }

    if (input === "o" && selected?.flowRunId) {
      const base = props.prefectUrl ?? "http://127.0.0.1:4200";
      const url = `${base}/flow-runs/flow-run/${selected.flowRunId}`;
      Bun.spawn(["xdg-open", url]);
      return;
    }

    if (input === "/") {
      setFilterDraft(filter);
      setFilterMode(true);
      return;
    }
    if (input === "e") {
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
    if (key.upArrow) { moveWithinSiblings(-1); return; }
    if (key.downArrow) { moveWithinSiblings(1); return; }
    if (key.rightArrow) { moveIntoChild(); return; }
    if (key.leftArrow) { moveToParent(); return; }
  });

  function runConfirmedAction(confirm: { action: "cancel" | "retry"; issueId: string }): void {
    if (confirm.action === "retry") {
      Bun.spawn(["po", "retry", confirm.issueId]);
    } else {
      // po cancel does not exist; use prefect flow-run cancel via flowRunId
      const row = issues.find((i) => i.issueId === confirm.issueId);
      if (row?.flowRunId) {
        Bun.spawn(["prefect", "flow-run", "cancel", row.flowRunId]);
      }
    }
  }

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
    const target = childRows[0];
    if (target) setSelected(target.issueId);
  }

  function moveToParent(): void {
    if (!selected?.parentIssueId) return;
    const parent = navIssues.find((i) => i.issueId === selected.parentIssueId);
    if (parent) setSelected(parent.issueId);
  }

  const summary = useMemo(() => {
    let running = 0, stuck = 0, failed = 0, done = 0;
    for (const i of issues) {
      if (i.flowState === "RUNNING") { running++; if (i.stuck) stuck++; }
      else if (i.flowState === "FAILED" || i.flowState === "CRASHED") failed++;
      else if (i.flowState === "COMPLETED") done++;
    }
    return { running, stuck, failed, done, total: issues.length };
  }, [issues]);

  // Dispatch form step label and placeholder
  const dispatchStepLabel: Record<DispatchStep, string> = {
    issue: "Issue ID",
    flow: "Flow (default: software-dev-full)",
    rig: "Rig name",
    rigPath: "Rig path",
    confirm: "",
  };

  function handleDispatchSubmit(value: string): void {
    if (!dispatchForm) return;
    const trimmed = value.trim();
    if (dispatchForm.step === "issue") {
      setDispatchForm({ step: "flow", values: { issue: trimmed } });
      setDispatchDraft("");
    } else if (dispatchForm.step === "flow") {
      setDispatchForm({
        step: "rig",
        values: { ...dispatchForm.values, flow: trimmed || "software-dev-full" },
      });
      setDispatchDraft("");
    } else if (dispatchForm.step === "rig") {
      setDispatchForm({ step: "rigPath", values: { ...dispatchForm.values, rig: trimmed } });
      setDispatchDraft("");
    } else if (dispatchForm.step === "rigPath") {
      setDispatchForm({ step: "confirm", values: { ...dispatchForm.values, rigPath: trimmed } });
      setDispatchDraft("");
    } else if (dispatchForm.step === "confirm") {
      if (trimmed.toLowerCase() === "y") {
        const v = dispatchForm.values;
        Bun.spawn([
          "po", "run", v.flow ?? "software-dev-full",
          "--issue-id", v.issue ?? "",
          "--rig", v.rig ?? "",
          "--rig-path", v.rigPath ?? "",
        ]);
      }
      setDispatchForm(null);
      setDispatchDraft("");
    }
  }

  return (
    <Box flexDirection="column">
      {/* Header */}
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
          {summary.stuck > 0 ? (
            <>
              <Text> </Text>
              <Text color="red">stuck:{summary.stuck}</Text>
            </>
          ) : null}
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

      {/* Main layout */}
      <Box flexDirection={mobile ? "column" : "row"}>
        <IssueList
          issues={issues}
          selectedId={selectedId}
          filter={filter}
          hideCompleted={hideCompleted}
          drillIntoIssueId={drillIntoIssueId}
          showDone={showDone}
          mobile={mobile}
          width={leftWidth}
        />
        {mobile && !drillIntoIssueId ? null : (
          <DetailTabs
            selectedTab={selectedTab}
            selected={selected}
            allIssues={issues}
            paneText={paneText}
            paneSession={paneSession}
            tailHeight={tailHeight}
            bdShowIssue={bdShowIssue}
            issueId={selectedId}
            bdShowError={bdShowError}
            bdScrollOffset={Math.min(bdScrollOffset, maxBdOffset)}
            prefectUrl={props.prefectUrl}
            mobile={mobile}
          />
        )}
      </Box>

      {/* Confirmation overlay */}
      {pendingConfirm ? (
        <Box paddingX={1} borderStyle="single" borderColor="yellow">
          <Text color="yellow">
            {pendingConfirm.action === "cancel" ? "Cancel" : "Retry"}{" "}
            <Text color="cyan">{pendingConfirm.issueId}</Text>? [y/n]
          </Text>
        </Box>
      ) : null}

      {/* Dispatch form */}
      {dispatchForm && dispatchForm.step !== "confirm" ? (
        <Box paddingX={1} borderStyle="single" borderColor="cyan" flexDirection="column">
          <Text color="cyan" bold>
            Dispatch new run
          </Text>
          {(["issue", "flow", "rig", "rigPath"] as const)
            .filter((s) => dispatchForm.values[s] !== undefined)
            .map((s) => (
              <Text key={s} color="gray">
                {dispatchStepLabel[s]}: {dispatchForm.values[s]}
              </Text>
            ))}
          <Box>
            <Text color="cyan">{dispatchStepLabel[dispatchForm.step]}: </Text>
            <TextInput
              value={dispatchDraft}
              onChange={setDispatchDraft}
              onSubmit={handleDispatchSubmit}
            />
          </Box>
          <Text color="gray">[Esc] cancel</Text>
        </Box>
      ) : null}

      {dispatchForm?.step === "confirm" ? (
        <Box paddingX={1} borderStyle="single" borderColor="yellow" flexDirection="column">
          <Text color="yellow">
            Dispatch: po run {dispatchForm.values.flow ?? "software-dev-full"} --issue-id{" "}
            {dispatchForm.values.issue} --rig {dispatchForm.values.rig} --rig-path{" "}
            {dispatchForm.values.rigPath}
          </Text>
          <Box>
            <Text color="yellow">Confirm? [y/n]: </Text>
            <TextInput
              value={dispatchDraft}
              onChange={setDispatchDraft}
              onSubmit={handleDispatchSubmit}
            />
          </Box>
        </Box>
      ) : null}

      {/* Footer */}
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
              ? "[↑↓←→] nav [a] attach [/] filt [e/E] drill [t] tab [c] cancel [r] retry [q]"
              : "[↑↓] sibs  [←→] tree  [t] tab  [1-4] jump  [c] cancel  [r] retry  [d] dispatch  [D] show-done  [a] attach  [A] in-place  [/] filter  [e/E] drill  [q] quit"}
          </Text>
        )}
      </Box>
    </Box>
  );
}

export { ATTACH_SENTINEL };
