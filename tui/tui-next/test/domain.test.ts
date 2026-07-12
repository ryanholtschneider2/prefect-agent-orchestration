import {describe, expect, test} from "bun:test";
import {epicRollup, lifecycleGroup, normalizeBeads, reconcile} from "../src/domain/model.js";
import {redact, truncateCells} from "../src/domain/text.js";
import {fixtureModel} from "./fixtures.js";

describe("normalized operations model", () => {
  test("joins only on declared parent and issue identifiers", () => {
    const rows = [{id: "e", title: "Epic", status: "open", issue_type: "epic"}, {id: "i", title: "Same title", status: "open", parent_id: "e"}, {id: "loose", title: "Same title", status: "open"}];
    const model = reconcile(rows, [{id: "a", issueId: "i", state: "RUNNING", runtime: {}, roles: []}, {id: "orphan", state: "RUNNING", runtime: {}, roles: []}], []);
    expect(model.epics[0]?.children[0]?.attempts).toHaveLength(1);
    expect(model.standalone[0]?.attempts).toHaveLength(0);
    expect(model.unattributedAttempts.map((attempt) => attempt.id)).toEqual(["orphan"]);
  });

  test("preserves standalone work and normalizes backend dependency shapes", () => {
    const result = normalizeBeads([{id: "e", issue_type: "epic", dependencies: [{depends_on_id: "x", dependency_type: "blocks"}]}, {id: "i", parent: "e"}, {id: "s"}]);
    expect(result.epics[0]?.dependencies).toEqual([{id: "x", type: "blocks"}]);
    expect(result.epics[0]?.children[0]?.id).toBe("i"); expect(result.standalone[0]?.id).toBe("s");
  });

  test("derives mechanical rollups and lifecycle groups", () => {
    expect(epicRollup(fixtureModel().epics[0]!)).toEqual({complete: 1, running: 1, blocked: 1, failed: 0, total: 3});
    expect(lifecycleGroup("closed")).toBe("completed"); expect(lifecycleGroup("in_progress")).toBe("active");
  });
});

describe("terminal-safe text", () => {
  test("truncates Unicode by terminal cells", () => { expect(truncateCells("矿业 alpha", 7)).toBe("矿业 a…"); expect(truncateCells("hello", 5)).toBe("hello"); });
  test("redacts common credentials", () => { expect(redact("token=secret123 api_key: abc ghp_123456789012345")).toBe("token=[redacted] api_key: [redacted] [redacted]"); });
});
