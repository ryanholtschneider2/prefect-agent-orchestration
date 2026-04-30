import type { BdIssue } from "../../data/beads.js";

/**
 * Fixtures shared across BdShow / store tests. Mirrors live shapes captured
 * from `bd show <id> --json` on this rig (per the iter-2 plan):
 *
 * - Open in_progress epic-shaped bead → has `metadata` and `dependents`,
 *   no `close_reason`.
 * - Closed role-step bead → has `close_reason` + `closed_at`, drops
 *   `metadata` and `dependents`.
 */

export const openIssue: BdIssue = {
  id: "prefect-orchestration-god",
  title: "po tui: show selected issue's bd description in right panel",
  status: "in_progress",
  priority: 4,
  issue_type: "task",
  owner: "Ryan",
  assignee: "po-3706138",
  description:
    "Feature request: when an issue is selected in the po tui issue list (left panel), the right panel should show that issue's bd metadata + description.\nUseful for reading the user's task description without dropping out of the TUI.",
  metadata: {
    "po.rig_path": "/home/ryan-24/Desktop/Code/personal/prefect-orchestration",
    "po.run_dir":
      "/home/ryan-24/Desktop/Code/personal/prefect-orchestration/.planning/software-dev-full/prefect-orchestration-god",
    session_planner: "5b2f4c93-5822-4c1c-b291-a26a085fc48d",
    session_plan_critic: "0fcaa608-1bd6-478e-b26e-afafb0a9747b",
  },
  dependents: [
    {
      id: "prefect-orchestration-god.triage.iter1",
      title: "triage iter 1 for prefect-orchestration-god",
      status: "closed",
      close_reason: "complete: triage routed to standard build path",
      dependency_type: "parent-child",
    },
    {
      id: "prefect-orchestration-god.plan.iter1",
      title: "plan iter 1 for prefect-orchestration-god",
      status: "closed",
      close_reason: "needs-revision: see iter-1 critic findings",
      dependency_type: "parent-child",
    },
    {
      id: "prefect-orchestration-god.plan.iter2",
      title: "plan iter 2 for prefect-orchestration-god",
      status: "closed",
      close_reason: "approved: addresses iter-1 critic blockers",
      dependency_type: "parent-child",
    },
    {
      id: "prefect-orchestration-god.build.iter1",
      title: "build iter 1 for prefect-orchestration-god",
      status: "open",
      dependency_type: "parent-child",
    },
    {
      id: "some-other-blocker",
      title: "blocks: external dep",
      status: "open",
      dependency_type: "blocks",
    },
  ],
};

export const closedIssue: BdIssue = {
  id: "prefect-orchestration-god.triage.iter1",
  title: "triage iter 1 for prefect-orchestration-god",
  status: "closed",
  priority: 2,
  issue_type: "task",
  owner: "Ryan",
  description: "Triage step output for prefect-orchestration-god.",
  close_reason: "complete: triage routed to standard build path",
  closed_at: "2026-04-30T00:18:04Z",
  parent: "prefect-orchestration-god",
  // No `metadata`, no `dependents` — verified live shape for closed beads.
};

/** Build a fixture with N parent-child children (used to test the cap). */
export function makeIssueWithChildren(n: number): BdIssue {
  return {
    id: "epic-with-many-kids",
    title: "an epic with many children",
    status: "in_progress",
    issue_type: "task",
    description: "epic description",
    dependents: Array.from({ length: n }, (_, i) => ({
      id: `epic-with-many-kids.${i + 1}`,
      title: `child ${i + 1}`,
      status: "open",
      dependency_type: "parent-child",
    })),
  };
}
