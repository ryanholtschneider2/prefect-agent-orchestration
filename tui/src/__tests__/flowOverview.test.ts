import { describe, expect, it } from "bun:test";

import { FLOW_OVERVIEWS, getFlowOverview } from "../components/FlowOverview.js";

describe("getFlowOverview()", () => {
  it("software_dev_full returns the full role sequence", () => {
    const s = getFlowOverview("software_dev_full");
    expect(s).not.toBeNull();
    expect(s).toContain("triage");
    expect(s).toContain("plan ⟲");
    expect(s).toContain("build");
    expect(s).toContain("lint+test");
    expect(s).toContain("regression");
    expect(s).toContain("review ⟲");
    expect(s).toContain("deploy-smoke");
    expect(s).toContain("verification ⟲");
    expect(s).toContain("ralph ⟲");
    expect(s).toContain("docs");
    expect(s).toContain("learn");
    // Stage separator is the unicode arrow, not "->".
    expect(s).toContain("→");
    // Snapshot the exact string so an unintentional reorder fails loud.
    expect(s).toBe(FLOW_OVERVIEWS.software_dev_full!);
  });

  it("epic returns the fan-out blurb", () => {
    expect(getFlowOverview("epic")).toBe(
      "epic fan-out: DAG-ordered parallel children",
    );
  });

  it("unknown flow returns null (component falls back to bare flow name)", () => {
    expect(getFlowOverview("not_a_real_flow")).toBeNull();
  });

  it("undefined flow name returns null", () => {
    expect(getFlowOverview(undefined)).toBeNull();
  });
});
