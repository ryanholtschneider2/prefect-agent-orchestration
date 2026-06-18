import { describe, expect, it } from "bun:test";

import {
  registryRoleFor,
  registryRoleForFlow,
  sessionForFlow,
} from "../data/tmux.js";

describe("software-dev-agentic tmux role mapping", () => {
  it("maps agentic flow task names to agentic tmux roles", () => {
    expect(registryRoleForFlow("software_dev_agentic", "agentic")).toBe(
      "agentic-worker",
    );
    expect(registryRoleForFlow("software_dev_agentic", "review")).toBe(
      "agentic-reviewer",
    );
  });

  it("does not change software-dev-full review mapping", () => {
    expect(registryRoleFor("review")).toBe("critic");
    expect(registryRoleForFlow("software_dev_full", "review")).toBe("critic");
  });

  it("builds the live session names used by agentic runs", () => {
    expect(sessionForFlow("bd-a38", "agentic", "software_dev_agentic")).toBe(
      "po-bd-a38-agentic-worker",
    );
    expect(sessionForFlow("bd-3oo", "review", "software_dev_agentic")).toBe(
      "po-bd-3oo-agentic-reviewer",
    );
  });
});
