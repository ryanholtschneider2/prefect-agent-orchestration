# `po attach` — lurking on a remote agent session

`po attach <issue-id> [--role <role>]` figures out which worker pod (if
any) is hosting the tmux session for an issue's agent run and `exec`s
either:

- `kubectl --context <ctx> -n <ns> exec -it <pod> -- tmux attach -t <session>`
  when the bead has `po.k8s_pod` metadata, or
- `tmux attach -t <session>` directly when the run is on the host.

It replaces the manual three-step "find-pod, build-kubectl-cmd,
hope-it's-still-Running" dance.

## Quick start

```bash
po attach prefect-orchestration-8gc                # auto-pick role if only one
po attach prefect-orchestration-8gc --role builder # specific role
po attach prefect-orchestration-8gc --list         # list resolved targets, don't attach
po attach prefect-orchestration-8gc --print-argv   # debug: print the argv that would be exec'd
```

## How resolution works

1. Look up `(rig_path, run_dir)` from the bead via `bd show <id> --json`
   (same path `po logs` / `po sessions` / `po retry` use).
2. Read roles from `<run_dir>/metadata.json` (every key starting with
   `session_<role>` counts).
3. Disambiguate the role:
   - `--role X` → use it (must match a known role).
   - omitted, exactly one role → use it.
   - omitted, multiple roles, **TTY** → numbered prompt.
   - omitted, multiple roles, **non-TTY** → exit non-zero with the list
     (no surprise blocks in CI / scripts).
4. Read the bead's `po.k8s_pod` / `po.k8s_namespace` / `po.k8s_context`
   metadata. If `po.k8s_pod` is set, build a kubectl-exec target;
   otherwise build a local-tmux target.
5. For k8s targets, probe the pod with `kubectl get pod` first:
   - `Running` → proceed and `os.execvp` into `kubectl exec -it`.
   - `NotFound` / not-Running → fail with `pod gone, run was on
     <pod-name> — try 'po retry <issue-id>'`.
   - `forbidden` → fail with `RBAC: caller needs pods/exec in <ns>`.

The pure logic lives in `prefect_orchestration/attach.py`; the Typer
command is a thin wrapper that handles the TTY handoff.

## Wiring it up on Kubernetes

The worker Pod needs three env vars surfaced to bead metadata at flow
entry by `attach.stamp_runtime_location`:

| env var | source | purpose |
|---|---|---|
| `POD_NAME` | downward API (`metadata.name`) | which pod to `kubectl exec` into |
| `POD_NAMESPACE` | downward API (`metadata.namespace`) | which namespace |
| `PO_KUBE_CONTEXT` | operator-supplied static value | user-side kubeconfig context label |

`POD_NAME` / `POD_NAMESPACE` are wired in
`k8s/po-worker-deployment.yaml`. The operator must set
`PO_KUBE_CONTEXT` on the Deployment because the pod has no way to know
the user's local kubeconfig context label — the in-cluster API server
URL is not the same string `kubectl --context` accepts.

```yaml
env:
  - name: POD_NAME
    valueFrom: { fieldRef: { fieldPath: metadata.name } }
  - name: POD_NAMESPACE
    valueFrom: { fieldRef: { fieldPath: metadata.namespace } }
  - name: PO_KUBE_CONTEXT
    value: my-cluster-context
```

When `PO_KUBE_CONTEXT` is missing, `po attach` falls back to the user's
**current** kubeconfig context with a warning — usable but a footgun if
multiple clusters exist on the user's machine.

## RBAC

The user (or service account) running `po attach` needs `pods/get` and
`pods/exec` in the worker pod's namespace:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: { name: po-attach-user }
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
  - apiGroups: [""]
    resources: ["pods/exec"]
    verbs: ["create"]
```

403s surface as `RBAC: caller needs pods/exec in <namespace>`.

## Stale pods

Worker pods can be evicted/restarted; the tmux session goes with them.
`po attach` probes the pod before exec'ing, so you'll see:

```
pod gone, run was on 'po-worker-7c5f8d-abcde' (pod NotFound). Try `po retry prefect-orchestration-8gc` to relaunch.
```

…rather than a confusing kubectl exec error.

## Out of scope

- **Web-terminal lurking** — there's a separate bead for a ttyd sidecar.
- **Cross-cluster federation** — one kubeconfig context per attach call.
- **Attaching to a different rig's session** — names are tied to the
  bead's run_dir; sessions outside that contract aren't discoverable.

## See also

- `engdocs/work-pools.md` — k8s/docker deployment playbook.
- `prefect_orchestration/attach.py` — the resolution + argv builders.
