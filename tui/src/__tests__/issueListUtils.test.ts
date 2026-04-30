import { describe, expect, it } from "bun:test";

import { humanWall } from "../components/IssueList.js";
import { computeStuck, selectLatestRunPerIssue } from "../state/store.js";
import type { IssueRow } from "../state/store.js";
import type { PrefectFlowRun } from "../data/prefect.js";

describe("humanWall()", () => {
  it("returns 0m for 0ms", () => {
    expect(humanWall(0)).toBe("0m");
  });

  it("returns minutes for < 1h", () => {
    expect(humanWall(4 * 60_000)).toBe("4m");
    expect(humanWall(52 * 60_000)).toBe("52m");
    expect(humanWall(59 * 60_000)).toBe("59m");
  });

  it("returns Xh for exact hours", () => {
    expect(humanWall(60 * 60_000)).toBe("1h");
    expect(humanWall(2 * 60 * 60_000)).toBe("2h");
  });

  it("returns XhYm for hours + minutes", () => {
    expect(humanWall((1 * 60 + 7) * 60_000)).toBe("1h7m");
    expect(humanWall((5 * 60 + 30) * 60_000)).toBe("5h30m");
  });
});

function makeRow(overrides: Partial<IssueRow> = {}): IssueRow {
  return {
    issueId: "test-1",
    roles: [],
    childIssueIds: [],
    updatedAt: Date.now(),
    stuck: false,
    flowMode: "fast",
    wallMs: 0,
    stepLabel: "",
    flowState: "RUNNING",
    activeRole: "builder",
    ...overrides,
  };
}

describe("computeStuck()", () => {
  const now = Date.now();

  it("returns false when flow is not RUNNING", () => {
    const row = makeRow({ flowState: "COMPLETED", activeRole: "builder" });
    expect(computeStuck(row, now)).toBe(false);
  });

  it("returns false when no activeRole", () => {
    const row = makeRow({ flowState: "RUNNING", activeRole: undefined });
    expect(computeStuck(row, now)).toBe(false);
  });

  it("returns false when elapsed < 2× typical (healthy 4m builder)", () => {
    // builder typical = 30m; 2× = 60m; 4m is well under
    const startedAt = new Date(now - 4 * 60_000).toISOString();
    const row = makeRow({
      activeRole: "builder",
      roles: [{ role: "builder", state: "running", iterations: 1, startedAt }],
    });
    expect(computeStuck(row, now)).toBe(false);
  });

  it("returns true when elapsed > 2× typical (5h builder = stuck)", () => {
    // builder typical = 30m; 2× = 60m; 5h is way over
    const startedAt = new Date(now - 5 * 60 * 60_000).toISOString();
    const row = makeRow({
      activeRole: "builder",
      roles: [{ role: "builder", state: "running", iterations: 1, startedAt }],
    });
    expect(computeStuck(row, now)).toBe(true);
  });

  it("falls back to flow startTime when role has no startedAt", () => {
    // builder typical = 30m; 2× = 60m; 90m triggers stuck
    const startTime = new Date(now - 90 * 60_000).toISOString();
    const row = makeRow({
      activeRole: "builder",
      startTime,
      roles: [{ role: "builder", state: "running", iterations: 1 }],
    });
    expect(computeStuck(row, now)).toBe(true);
  });

  it("uses DEFAULT_TYPICAL_MS for unknown roles", () => {
    // default typical = 20m; 2× = 40m; 39m should be fine
    const startedAt = new Date(now - 39 * 60_000).toISOString();
    const row = makeRow({
      activeRole: "mystery-role",
      roles: [{ role: "mystery-role", state: "running", iterations: 1, startedAt }],
    });
    expect(computeStuck(row, now)).toBe(false);

    // 41m: stuck
    const startedAt2 = new Date(now - 41 * 60_000).toISOString();
    const row2 = makeRow({
      activeRole: "mystery-role",
      roles: [{ role: "mystery-role", state: "running", iterations: 1, startedAt: startedAt2 }],
    });
    expect(computeStuck(row2, now)).toBe(true);
  });
});

// ─── selectLatestRunPerIssue ─────────────────────────────────────────

function makeFr(
  overrides: Partial<PrefectFlowRun> & { id: string },
): PrefectFlowRun {
  return {
    id: overrides.id,
    name: overrides.id,
    flow_id: "flow-1",
    state_type: "COMPLETED",
    state_name: "Completed",
    tags: [`issue_id:${overrides.id}`],
    start_time: null,
    end_time: null,
    ...overrides,
  };
}

describe("selectLatestRunPerIssue()", () => {
  it("RUNNING with null start_time beats COMPLETED with old start_time", () => {
    // Regression: newly dispatched RUNNING runs were invisible because
    // "" > "2026-04-28T..." is false, so the old COMPLETED run won.
    const completed = makeFr({
      id: "sb-595",
      state_type: "COMPLETED",
      state_name: "Completed",
      tags: ["issue_id:sb-595"],
      start_time: "2026-04-28T10:00:00+00:00",
    });
    const running = makeFr({
      id: "sb-595-new",
      state_type: "RUNNING",
      state_name: "Running",
      tags: ["issue_id:sb-595"],
      start_time: null, // freshly dispatched — no start_time yet
    });
    const result = selectLatestRunPerIssue([completed, running]);
    expect(result.get("sb-595")?.id).toBe("sb-595-new");
  });

  it("COMPLETED with newer start_time beats COMPLETED with older start_time", () => {
    const older = makeFr({
      id: "run-old",
      state_type: "COMPLETED",
      tags: ["issue_id:po-1"],
      start_time: "2026-04-20T10:00:00+00:00",
    });
    const newer = makeFr({
      id: "run-new",
      state_type: "COMPLETED",
      tags: ["issue_id:po-1"],
      start_time: "2026-04-28T10:00:00+00:00",
    });
    const result = selectLatestRunPerIssue([older, newer]);
    expect(result.get("po-1")?.id).toBe("run-new");
  });

  it("RUNNING run beats CANCELLED run regardless of timestamps", () => {
    const cancelled = makeFr({
      id: "run-cancelled",
      state_type: "CANCELLED",
      tags: ["issue_id:po-2"],
      start_time: "2026-04-28T10:00:00+00:00",
    });
    const running = makeFr({
      id: "run-running",
      state_type: "RUNNING",
      tags: ["issue_id:po-2"],
      start_time: "2026-04-01T00:00:00+00:00", // older timestamp
    });
    const result = selectLatestRunPerIssue([cancelled, running]);
    expect(result.get("po-2")?.id).toBe("run-running");
  });

  it("PENDING run beats CANCELLED run", () => {
    const cancelled = makeFr({
      id: "run-done",
      state_type: "CANCELLED",
      tags: ["issue_id:po-3"],
      start_time: "2026-04-28T10:00:00+00:00",
    });
    const pending = makeFr({
      id: "run-pending",
      state_type: "PENDING",
      tags: ["issue_id:po-3"],
      start_time: null,
    });
    const result = selectLatestRunPerIssue([cancelled, pending]);
    expect(result.get("po-3")?.id).toBe("run-pending");
  });

  it("runs without issue_id tag are ignored", () => {
    const untagged = makeFr({ id: "no-tag", tags: ["other:tag"] });
    const result = selectLatestRunPerIssue([untagged]);
    expect(result.size).toBe(0);
  });
});
