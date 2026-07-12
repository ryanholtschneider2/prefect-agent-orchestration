import type {OperationsModel} from "../src/domain/model.js";
import {healthy} from "../src/sources/adapters.js";

export const fixtureModel = (): OperationsModel => ({
  epics: [{kind: "epic", id: "po-road", title: "Formula graph migration", state: "in_progress", updatedAt: "2026-07-12T00:00:00Z", dependencies: [{id: "po-platform", type: "blocks"}], children: [
    {kind: "issue", id: "po-child", epicId: "po-road", title: "Replace verdict channel safely", state: "in_progress", updatedAt: "2026-07-12T01:00:00Z", dependencies: [], artifacts: [{name: "gate.txt", kind: "txt", path: "/tmp/po-child/gate.txt"}], sessions: [], comments: [], attempts: [{id: "12345678-flow", issueId: "po-child", epicId: "po-road", formula: "software-dev-agentic", state: "RUNNING", startedAt: "2026-07-12T01:00:00Z", runtime: {model: "gpt-5.4", effort: "xhigh"}, roles: [{id: "role-1", role: "builder", state: "RUNNING", iteration: 1}]}]},
    {kind: "issue", id: "po-wait", epicId: "po-road", title: "Migrate role prompts", state: "blocked", updatedAt: "2026-07-11T01:00:00Z", dependencies: [{id: "po-child", type: "blocks"}], artifacts: [], sessions: [], comments: [{author: "operator", text: "human decision required"}], attempts: []},
    {kind: "issue", id: "po-done", epicId: "po-road", title: "Document the cutover", state: "closed", updatedAt: "2026-07-10T01:00:00Z", dependencies: [], artifacts: [], sessions: [], comments: [], attempts: []},
  ]}],
  standalone: [{kind: "issue", id: "po-loose", title: "Standalone diagnostics", state: "open", dependencies: [], artifacts: [], sessions: [], comments: [], attempts: []}],
  unattributedAttempts: [{id: "orphan", state: "RUNNING", runtime: {}, roles: []}],
  unresolved: [{source: "prefect", id: "orphan", reason: "missing issue_id tag"}],
  snapshots: {beads: healthy("beads", []), prefect: healthy("prefect", []), tmux: healthy("tmux", {available: false}), artifacts: healthy("artifacts", [])},
});
