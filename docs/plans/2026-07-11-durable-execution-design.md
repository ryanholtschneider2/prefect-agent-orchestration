# Durable PO execution

## Contract

Registered PO formulas execute on the always-on Prefect worker by default. The
submitting shell only validates inputs, creates a deployment-backed flow run,
prints its ID, and exits. `--foreground` is an explicit debugging mode retaining
the old in-process behavior. Scratch flows remain foreground-only because they
have no stable importable deployment.

`po resume` follows the same rule: preserve the existing run directory and role
sessions, submit a new run with `PO_RESUME=1`, and let the formula skip completed
work. An interrupted submitter must never imply cancellation. Explicit cancel is
the only path allowed to terminate active agent sessions.

## Readiness and recovery

The local stack has separate liveness and readiness semantics. Readiness requires
Postgres `pg_isready` plus a database-backed Prefect API query. Service status
returns nonzero when any required unit, database, API, or worker is unhealthy.
The installer owns one canonical worker and pins server and worker to the Prefect
runtime bundled with PO.

A standing reconciler handles transport only: it finds stale Running flows with
no controller or agent process, marks them Failed, and schedules a resume when a
run directory proves resumable state exists. It never manufactures or changes a
model quality verdict. Reconciliation is idempotent through a durable marker and
Prefect in-flight checks.

## Verification

Tests cover immediate deployment submission, foreground compatibility, resume
submission, submitter termination, Postgres/server/worker restart behavior,
truthful status exits, `br` filesystem run-directory recovery, and idempotent
reconciliation. A live smoke verifies the installed user services and a formula
that continues after its submitting process exits.
