"""Unit tests for `prefect_orchestration.attach` — pure logic.

CLI-level tests live in `tests/test_cli_attach.py`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


from prefect_orchestration import agent_session, attach


def test_session_name_sanitizes_dots():
    assert attach.session_name("po-foo-4ja.1", "builder") == "po-po-foo-4ja_1-builder"
    assert attach.session_name("simple", "plan-critic") == "po-simple-plan-critic"


def test_session_name_parity_with_tmux_backend():
    """Naming rule must stay byte-identical between attach + TmuxClaudeBackend."""
    cases = [
        ("simple", "builder"),
        ("po-foo-4ja.1", "builder"),
        ("issue.with.many.dots", "role.dotted"),
        ("UPPER", "MiXeD"),
    ]
    for issue, role in cases:
        backend = agent_session.TmuxClaudeBackend(issue=issue, role=role)
        assert attach.session_name(issue, role) == backend._session_name()


def test_resolve_target_k8s():
    target = attach.resolve_attach_target(
        issue="issue",
        role="builder",
        bead_metadata={
            attach.META_K8S_POD: "po-worker-abc",
            attach.META_K8S_NAMESPACE: "ns",
            attach.META_K8S_CONTEXT: "ctx",
        },
    )
    assert isinstance(target, attach.K8sTarget)
    assert target.context == "ctx"
    assert target.namespace == "ns"
    assert target.pod == "po-worker-abc"
    assert target.session == "po-issue-builder"


def test_resolve_target_k8s_defaults_namespace_when_missing():
    target = attach.resolve_attach_target(
        issue="issue",
        role="builder",
        bead_metadata={attach.META_K8S_POD: "po-worker-abc"},
    )
    assert isinstance(target, attach.K8sTarget)
    assert target.namespace == "default"
    assert target.context is None


def test_resolve_target_local_when_no_k8s_meta():
    target = attach.resolve_attach_target(
        issue="issue", role="builder", bead_metadata={}
    )
    assert isinstance(target, attach.LocalTarget)
    assert target.session == "po-issue-builder"


def test_build_kubectl_argv_with_context():
    target = attach.K8sTarget(
        context="ctx", namespace="ns", pod="pod", session="po-issue-builder"
    )
    assert attach.build_kubectl_argv(target) == [
        "kubectl",
        "--context",
        "ctx",
        "-n",
        "ns",
        "exec",
        "-it",
        "pod",
        "--",
        "tmux",
        "attach",
        "-t",
        "po-issue-builder",
    ]


def test_build_kubectl_argv_without_context():
    target = attach.K8sTarget(
        context=None, namespace="ns", pod="pod", session="po-issue-builder"
    )
    argv = attach.build_kubectl_argv(target)
    assert "--context" not in argv
    assert argv[:5] == ["kubectl", "-n", "ns", "exec", "-it"]


def test_build_local_argv():
    assert attach.build_local_argv(attach.LocalTarget(session="po-x-y")) == [
        "tmux",
        "attach",
        "-t",
        "po-x-y",
    ]


def _fake_runner(returncode: int, stdout: str = "", stderr: str = ""):
    def run(argv, *, capture_output, text, check):
        return subprocess.CompletedProcess(
            argv, returncode, stdout=stdout, stderr=stderr
        )

    return run


def test_probe_pod_running():
    target = attach.K8sTarget(context=None, namespace="ns", pod="p", session="s")
    runner = _fake_runner(0, stdout=json.dumps({"status": {"phase": "Running"}}))
    status, detail = attach.probe_pod(target, runner=runner)
    assert status == "running"


def test_probe_pod_not_running_phase():
    target = attach.K8sTarget(context=None, namespace="ns", pod="p", session="s")
    runner = _fake_runner(0, stdout=json.dumps({"status": {"phase": "Failed"}}))
    status, _ = attach.probe_pod(target, runner=runner)
    assert status == "gone"


def test_probe_pod_not_found():
    target = attach.K8sTarget(context=None, namespace="ns", pod="p", session="s")
    runner = _fake_runner(1, stderr='Error from server (NotFound): pods "p" not found')
    status, _ = attach.probe_pod(target, runner=runner)
    assert status == "gone"


def test_probe_pod_forbidden():
    target = attach.K8sTarget(context=None, namespace="ns", pod="p", session="s")
    runner = _fake_runner(1, stderr='Error: pods "p" is forbidden')
    status, _ = attach.probe_pod(target, runner=runner)
    assert status == "forbidden"


def test_probe_pod_unknown():
    target = attach.K8sTarget(context=None, namespace="ns", pod="p", session="s")
    runner = _fake_runner(1, stderr="connection refused")
    status, _ = attach.probe_pod(target, runner=runner)
    assert status == "unknown"


def test_discover_roles(tmp_path: Path):
    (tmp_path / "metadata.json").write_text(
        json.dumps(
            {
                "session_builder": "u1",
                "session_critic": "u2",
                "session_": "skip-empty-role",
                "not_a_session": "u3",
            }
        )
    )
    assert attach.discover_roles(tmp_path) == ["builder", "critic"]


def test_discover_roles_missing_metadata(tmp_path: Path):
    assert attach.discover_roles(tmp_path) == []


def test_stamp_runtime_location_no_op_when_no_pod_name():
    class FakeStore:
        def __init__(self):
            self.writes: dict[str, str] = {}

        def get(self, k, default=None):
            return self.writes.get(k, default)

        def set(self, k, v):
            self.writes[k] = v

        def all(self):
            return dict(self.writes)

    store = FakeStore()
    written = attach.stamp_runtime_location(store, env={})
    assert written == {}
    assert store.writes == {}


def test_stamp_runtime_location_writes_three_keys():
    writes: dict[str, str] = {}

    class S:
        def get(self, k, default=None):
            return writes.get(k, default)

        def set(self, k, v):
            writes[k] = v

        def all(self):
            return dict(writes)

    written = attach.stamp_runtime_location(
        S(),
        env={
            "POD_NAME": "po-worker-7c5",
            "POD_NAMESPACE": "po-system",
            "PO_KUBE_CONTEXT": "prod-east",
        },
    )
    assert writes == {
        attach.META_K8S_POD: "po-worker-7c5",
        attach.META_K8S_NAMESPACE: "po-system",
        attach.META_K8S_CONTEXT: "prod-east",
    }
    assert written == writes


def test_stamp_runtime_location_omits_context_when_unset():
    writes: dict[str, str] = {}

    class S:
        def get(self, k, default=None):
            return writes.get(k, default)

        def set(self, k, v):
            writes[k] = v

        def all(self):
            return dict(writes)

    attach.stamp_runtime_location(
        S(),
        env={"POD_NAME": "p", "POD_NAMESPACE": "ns"},
    )
    assert attach.META_K8S_CONTEXT not in writes
    assert writes[attach.META_K8S_POD] == "p"
    assert writes[attach.META_K8S_NAMESPACE] == "ns"
