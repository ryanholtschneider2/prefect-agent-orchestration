import { describe, expect, it } from "bun:test";

import { buildLines, CHILDREN_CAP } from "../components/BdShow.js";
import type { BdIssue } from "../data/beads.js";
import {
  closedIssue,
  makeIssueWithChildren,
  openIssue,
} from "./fixtures/issues.js";

describe("buildLines()", () => {
  it("open issue: no CLOSE REASON line", () => {
    const lines = buildLines(openIssue);
    expect(lines.some((l) => l.startsWith("CLOSE REASON"))).toBe(false);
  });

  it("closed issue: CLOSE REASON line above DESCRIPTION", () => {
    const lines = buildLines(closedIssue);
    const closeIdx = lines.findIndex((l) => l.startsWith("CLOSE REASON"));
    const descIdx = lines.indexOf("DESCRIPTION");
    expect(closeIdx).toBeGreaterThan(-1);
    expect(descIdx).toBeGreaterThan(-1);
    expect(closeIdx).toBeLessThan(descIdx);
    expect(lines[closeIdx]).toBe(
      "CLOSE REASON: complete: triage routed to standard build path",
    );
  });

  it("metadata keys render in alphabetical order", () => {
    const lines = buildLines(openIssue);
    const metaIdx = lines.indexOf("METADATA");
    expect(metaIdx).toBeGreaterThan(-1);
    // Pull lines after METADATA up to the first blank line.
    const metaSection: string[] = [];
    for (let i = metaIdx + 1; i < lines.length && lines[i] !== ""; i++) {
      metaSection.push(lines[i]!);
    }
    const sorted = [...metaSection].sort();
    expect(metaSection).toEqual(sorted);
    // Sanity: known keys present.
    expect(metaSection).toContain(
      "  po.rig_path: /home/ryan-24/Desktop/Code/personal/prefect-orchestration",
    );
    expect(metaSection.some((l) => l.startsWith("  session_planner:"))).toBe(
      true,
    );
  });

  it("only parent-child dependents render under CHILDREN", () => {
    const lines = buildLines(openIssue);
    const childrenIdx = lines.findIndex((l) => l.startsWith("CHILDREN"));
    expect(childrenIdx).toBeGreaterThan(-1);
    // The "blocks" dep should NOT show up in CHILDREN.
    expect(lines.some((l) => l.includes("some-other-blocker"))).toBe(false);
    // The four parent-child kids SHOULD.
    expect(lines.some((l) => l.includes("god.triage.iter1"))).toBe(true);
    expect(lines.some((l) => l.includes("god.plan.iter1"))).toBe(true);
    expect(lines.some((l) => l.includes("god.plan.iter2"))).toBe(true);
    expect(lines.some((l) => l.includes("god.build.iter1"))).toBe(true);
    // CHILDREN header reflects parent-child count (4), not total deps (5).
    expect(lines[childrenIdx]).toBe("CHILDREN (4)");
  });

  it("more than CHILDREN_CAP children: caps + emits overflow line", () => {
    const issue = makeIssueWithChildren(25);
    const lines = buildLines(issue);
    const childrenIdx = lines.findIndex((l) => l.startsWith("CHILDREN"));
    expect(childrenIdx).toBeGreaterThan(-1);
    // Header still shows the FULL count.
    expect(lines[childrenIdx]).toBe(`CHILDREN (25)`);
    // Count rendered child rows by id prefix.
    const childRows = lines.filter((l) =>
      l.startsWith("  epic-with-many-kids."),
    );
    expect(childRows.length).toBe(CHILDREN_CAP);
    // Overflow line tells the user about the rest.
    expect(lines).toContain(
      `  (+${25 - CHILDREN_CAP} more)`,
    );
  });

  it("closed-bead shape (no metadata, no dependents): no METADATA/CHILDREN sections", () => {
    const lines = buildLines(closedIssue);
    expect(lines.some((l) => l === "METADATA")).toBe(false);
    expect(lines.some((l) => l.startsWith("CHILDREN"))).toBe(false);
    // No exception thrown — covered by reaching this assertion.
  });

  it("empty description: no DESCRIPTION section", () => {
    const issue: BdIssue = {
      id: "x",
      title: "x",
      status: "open",
      description: "",
    };
    const lines = buildLines(issue);
    expect(lines).not.toContain("DESCRIPTION");
  });

  it("renders close_reason on a parent-child child", () => {
    const lines = buildLines(openIssue);
    // The triage iter1 child has a close_reason; it should render with the ↳.
    const idx = lines.findIndex((l) => l.includes("god.triage.iter1"));
    expect(idx).toBeGreaterThan(-1);
    expect(lines[idx + 1]).toContain("↳");
    expect(lines[idx + 1]).toContain("complete: triage");
  });
});
