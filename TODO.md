# prefect-orchestration TODO

Status as of 2026-04-29. Most of the older items on this list shipped
weeks ago; see `git log` and the closed beads under `bd list --status=closed`.

## Shipped recently

- ✅ Verdicts as file artifacts (`$RUN_DIR/verdicts/<step>.json`) —
  the orchestrator no longer parses prose for step results.
- ✅ `po run epic` — Beads dependencies map to Prefect `wait_for=`.
- ✅ Claim-on-enter / close-on-exit (`po-{flow_run_id}` assignee).
- ✅ `agent_step` primitive — single-turn agent dispatch with bead-
  stamping, session affinity, convergence ladder, resumability cache.
- ✅ Simple-mode `software_dev_full` — replaces nested-loop legacy +
  short-lived graph-mode body. ~150 LOC vs ~600.
- ✅ 4-tier complexity gating in the triager (trivial / simple /
  moderate / complex). Trivial completes in <2 min via self-execute.
- ✅ Cache fast-path — skip-on-closed-bead in 1 shellout (was 4).
- ✅ Resumed-session short prompt — saves tokens on iter2+ turns.
- ✅ Typed `RateLimitError` propagated from `agent_session.py`.
- ✅ `tests-changed.txt` wired in `software_dev_full` so regression-gate
  + tester run scoped tests instead of full suite (eliminated 30-min
  pytest-thrash timeouts under multi-flow rig contention).
- ✅ Ralph + full-test-gate are opt-in (`--enable-ralph`,
  `--enable-full-test-gate`); off by default even at complex tier.
- ✅ Skill flattened to `skills/SKILL.md` (single-skill pack layout).
- ✅ One-line install: `make install` + `scripts/install.sh`
  (curl-installable; detects Claude Code / Cursor / Aider and symlinks
  the skill into each).
- ✅ `engdocs/` decoupled from any specific deployment vocabulary.
- ✅ Tmux backend with lurk-able sessions (`po-<issue>-<role>`).
- ✅ Repo published to GitHub at
  `ryanholtschneider2/prefect-agent-orchestration`.

## Open beads — see `bd ready`

| ID | P | What |
|---|---|---|
| `xhb` | P3 | RemoteComputer backend abstraction (SSH / Modal / K8s pod / Daytona workers). The next big architectural move. |
| `god` | P4 | `po tui`: right panel shows `bd show <id>` for selected issue. (May already be closing as this commit lands.) |
| `7vs.6` | P3 | Delete legacy verdicts/ + prompt_for_verdict + loop scaffolding. **Deferred** until ~1 week of green PO runs in production. |
| `7vs` | P2 | Parent epic for the bead-graph collapse work; mostly closed. |

## Sketch — RemoteComputer (`xhb`) shape

The single-largest follow-on. Today's `SessionBackend` Protocol abstracts
"how do I talk to an agent runtime" into three impls (`ClaudeCliBackend`,
`TmuxClaudeBackend`, `StubBackend`), all running the Claude CLI as a
child process of the Prefect worker on the orchestrator host. That
couples worker concurrency to host resources and gives no story for:

- Persistent worker filesystems across turns (hibernate between agent
  iters; resume with full state).
- Spawning workers on remote hosts (SSH, Modal sandbox, Daytona, K8s
  pod, Cloudflared tunnel).
- Mixed-runtime fan-out (one rig, two roles on different machines).

Sketch: a `RemoteComputer` Protocol that wraps a `SessionBackend` plus
a `persistent: bool` flag. Implementations:

- `LocalRemoteComputer` (default; what we have today)
- `SSHRemoteComputer` (transient by default; persistent via `tmux new-session -dDP` reuse)
- `ModalRemoteComputer` (Modal sandbox per agent iter; persistent via volume snapshot)
- `K8sPodRemoteComputer` (one pod per role; persistent = StatefulSet, transient = Job)
- `DaytonaRemoteComputer` (Daytona workspace; persistent built-in)

`agent_step` takes an optional `computer:` kwarg; defaults to the
local one. Per-role `metadata.json` records which computer the role
uses so resume picks the right host.

## Open product/architecture questions

- Should approval/budget decorators (`@require_human_approval`,
  `@budget`) live in core, or in a `po-policy` pack? Today we'd put
  them in core; revisit if rule surface grows past ~20 rules.
- How does memory scale past `bd remember` + `$RUN_DIR/lessons-learned.md`?
  Vector store (mem0 / letta) ships as `po-integrations-mem0` if/when
  load-bearing — not as a core Protocol.
- Per-pack CLAUDE.md fragments — convention for assembly into a
  rig-level CLAUDE.md? The starter meta-pack ships a consolidated
  fragment, but the assembly story for à-la-carte installs is open.

## Out of scope

- Reinventing Linear / Stripe / Gmail as the source of truth.
- Generic event-bus primitives in core (NATS / Kafka belongs in an
  integration pack).
- Per-flow plugin systems — entry points are sufficient; everything
  composes through `po.formulas`, `po.deployments`, `po.commands`,
  `po.doctor_checks`.
