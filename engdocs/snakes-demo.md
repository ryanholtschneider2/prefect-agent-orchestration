# Snakes fanout demo

End-to-end recipe for the "snakes" demo: a beads epic that fans out into N
parallel children, dispatched through PO into a Kubernetes work pool, recorded
as a 3- or 4-pane terminal dashboard.

The dashboard script is `scripts/snakes-demo/dashboard.sh` and is the only
new asset PO ships for this demo. Everything else (rig provisioning, beads
seeding, helm chart, `po run epic`) is the same as any other PO workflow.

## Provision rig

Create a fresh rig directory and initialize beads against the local
dolt sql-server (the default backend for PO rigs — see CLAUDE.md "Backend
(dolt-server)"):

```bash
mkdir -p ~/snakes-rig && cd ~/snakes-rig
bd init --server \
        --server-host=127.0.0.1 \
        --server-port=3307 \
        --server-user=root \
        --database=snakes
mkdir snakes  # Pane 3 watches this dir; create it up front so `tree` finds it
```

## Seed beads

Create the epic and N children, with each child blocked only by the epic so
they fan out in parallel:

```bash
EPIC_ID=$(bd create --type=epic --priority=2 \
  --title="snakes demo epic" \
  --description="fanout demo: N children dispatched in parallel via po run epic" \
  --json | jq -r .id)

for i in $(seq 1 8); do
  bd create --type=task --priority=2 \
    --title="snake ${i}" \
    --description="child ${i} of the snakes fanout demo" \
    --id "${EPIC_ID}.${i}"
  bd dep add "${EPIC_ID}.${i}" "$EPIC_ID"
done

bd update "$EPIC_ID" --status in_progress  # required for fan-out pickup
```

## Deploy chart

Install the PO worker chart so the K8s work pool has replicas to scale into:

```bash
helm install po charts/po -n po --create-namespace \
  --set worker.workPool=po-k8s \
  --set worker.replicas=1
prefect work-pool create po-k8s --type kubernetes --concurrency-limit 8 || true
prefect worker start --pool po-k8s &  # or rely on the in-cluster worker
```

## Launch dashboard

In the terminal you intend to record from (WezTerm preferred, tmux fallback):

```bash
./scripts/snakes-demo/dashboard.sh \
  --rig-path "$HOME/snakes-rig" \
  --namespace po \
  --epic-id "$EPIC_ID"
```

Pane layout:

| Pane | Contents |
|---|---|
| 1 | Browser opened to `http://localhost:4200/runs?tag=epic:<id>` (Prefect UI flow-runs filtered to the epic) |
| 2 | `kubectl get pods -n <ns> -w` — replicas scaling up live |
| 3 | `watch -n 1 tree -L 2 $RIG_PATH/snakes/` — child rigs / artifacts appearing |
| 4 *(optional)* | `po watch <epic-id>` — merged Prefect-state + run-dir feed (only when `po watch` is on `PATH`) |

Backend selection: WezTerm is chosen only when `wezterm` is on `PATH` AND
`$WEZTERM_PANE` is set (i.e. you're already inside a WezTerm pane). Otherwise
the script falls back to tmux. Force a backend with `--layout wezterm|tmux`;
preview the per-pane commands with `--dry-run`.

## Dispatch epic

In a separate terminal (so the dashboard recording stays clean):

```bash
po run epic \
  --epic-id "$EPIC_ID" \
  --rig snakes \
  --rig-path "$HOME/snakes-rig"
```

PO topo-sorts the children and dispatches them in waves; the Prefect UI in
Pane 1 fills with running flow-runs, the K8s pods in Pane 2 spin up, and the
rig dir in Pane 3 grows as each child writes its `.planning/` artifacts.

## Record

Two recording paths:

- **OBS Studio** — required if you want to capture Pane 1 (the browser).
  Add a Window Capture source for the WezTerm/tmux window plus another for
  the Firefox window if you spread Pane 1 onto a second monitor. Encode at
  1080p30 H.264 for posting; mp4 container.
- **asciinema** — terminal-only; cannot capture the browser pane. Best for
  showing the K8s + rig + `po watch` panes by themselves:

  ```bash
  asciinema rec snakes.cast \
    --command 'tmux attach -t snakes-demo' \
    --title 'snakes fanout demo'
  ```

  Convert to GIF/SVG with `agg snakes.cast snakes.gif` for embedding.

When recording is done, tear down: `bd update <epic-id> --status open` (to
pause), `helm uninstall po -n po`, `bd close <epic-id> <child-ids…>`.
