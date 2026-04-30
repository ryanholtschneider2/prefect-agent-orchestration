import { describe, expect, it } from "bun:test";

import { humanWall } from "../components/IssueList.js";
import { computeStuck } from "../state/store.js";
import type { IssueRow } from "../state/store.js";

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
