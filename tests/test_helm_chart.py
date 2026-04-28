"""Helm chart smoke tests for charts/po — prefect-orchestration-tyf.4.

These do not require a real cluster. They invoke `helm lint` + `helm
template` on the in-tree chart and assert the multi-doc YAML output
contains the expected kinds / names. CI without `helm` on PATH skips.
"""

from __future__ import annotations

import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CHART_DIR = REPO_ROOT / "charts" / "po"


def _have_helm() -> bool:
    return shutil.which("helm") is not None


def _helm_template(*set_flags: str) -> str:
    cmd = ["helm", "template", "po", str(CHART_DIR), *set_flags]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def _docs(rendered: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(rendered) if d]


def test_chart_yaml_parses() -> None:
    chart_yaml = yaml.safe_load((CHART_DIR / "Chart.yaml").read_text())
    assert chart_yaml["name"] == "po"
    assert chart_yaml["apiVersion"] == "v2"
    assert chart_yaml["version"]
    assert chart_yaml["appVersion"]


def test_values_schema_parses() -> None:
    import json

    schema = json.loads((CHART_DIR / "values.schema.json").read_text())
    assert schema["title"]
    assert "auth" in schema["properties"]


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_helm_lint_clean() -> None:
    res = subprocess.run(
        ["helm", "lint", str(CHART_DIR)], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "0 chart(s) failed" in res.stdout


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_helm_template_default_renders_core_kinds() -> None:
    docs = _docs(_helm_template())
    kinds = Counter(d.get("kind") for d in docs)
    # Defaults: prefect-server enabled, ingress disabled, OAuth PVC off.
    assert kinds["Deployment"] == 2
    assert kinds["Service"] == 1
    assert kinds["Job"] == 1  # pool-register hook
    assert kinds["ServiceAccount"] == 1
    assert kinds["Role"] == 1
    assert kinds["RoleBinding"] == 1
    assert kinds["PersistentVolumeClaim"] == 1  # rig only
    assert "Ingress" not in kinds
    # The helm-test Pod is a hook resource and only manifests on `helm test`,
    # but `helm template` includes hook-annotated docs too.
    assert kinds["Pod"] == 1


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_helm_template_ingress_enabled() -> None:
    docs = _docs(_helm_template("--set", "ingress.enabled=true"))
    assert any(d["kind"] == "Ingress" for d in docs)


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_helm_template_oauth_mode_emits_credentials_env() -> None:
    rendered = _helm_template("--set", "auth.mode=oauth")
    # Worker Deployment should reference the oauth Secret
    assert "CLAUDE_CREDENTIALS" in rendered
    assert "claude-oauth" in rendered
    assert "ANTHROPIC_API_KEY" not in rendered


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_helm_template_oauth_persistence_adds_pvc() -> None:
    rendered = _helm_template(
        "--set",
        "auth.mode=oauth",
        "--set",
        "auth.oauth.persistence.enabled=true",
    )
    docs = _docs(rendered)
    pvc_names = [
        d["metadata"]["name"] for d in docs if d["kind"] == "PersistentVolumeClaim"
    ]
    assert any("claude-home" in n for n in pvc_names)


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_helm_template_pool_register_is_hook() -> None:
    docs = _docs(_helm_template())
    jobs = [d for d in docs if d["kind"] == "Job"]
    assert len(jobs) == 1
    annotations = jobs[0]["metadata"].get("annotations", {})
    assert annotations.get("helm.sh/hook") == "pre-install,pre-upgrade"


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_helm_template_disable_prefect_server() -> None:
    docs = _docs(_helm_template("--set", "prefectServer.enabled=false"))
    deployments = [d["metadata"]["name"] for d in docs if d["kind"] == "Deployment"]
    assert any("worker" in n for n in deployments)
    assert not any("prefect-server" in n for n in deployments)


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_helm_template_apikey_create_secret_requires_value() -> None:
    res = subprocess.run(
        [
            "helm",
            "template",
            "po",
            str(CHART_DIR),
            "--set",
            "auth.apikey.createSecret=true",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "auth.apikey.apiKey" in (res.stdout + res.stderr)


# --------------------------------------------------------------------------
# Multi-account credential pool (5wk.3)
# --------------------------------------------------------------------------


def _worker_env(docs: list[dict]) -> list[dict]:
    workers = [
        d
        for d in docs
        if d["kind"] == "Deployment" and "worker" in d["metadata"]["name"]
    ]
    assert len(workers) == 1
    return workers[0]["spec"]["template"]["spec"]["containers"][0]["env"]


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_oauth_pool_wires_pool_env() -> None:
    rendered = _helm_template(
        "--set",
        "auth.mode=oauth",
        "--set",
        "auth.oauth.pool.enabled=true",
        "--set",
        "auth.oauth.pool.secretName=claude-oauth-pool",
        "--set",
        "auth.oauth.pool.secretKey=pool",
    )
    docs = _docs(rendered)
    env = _worker_env(docs)
    names = {e["name"] for e in env}
    assert "CLAUDE_CREDENTIALS_POOL" in names
    assert "CLAUDE_CREDENTIALS" not in names
    pool_env = next(e for e in env if e["name"] == "CLAUDE_CREDENTIALS_POOL")
    ref = pool_env["valueFrom"]["secretKeyRef"]
    assert ref["name"] == "claude-oauth-pool"
    assert ref["key"] == "pool"


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_apikey_pool_wires_pool_env() -> None:
    rendered = _helm_template(
        "--set",
        "auth.mode=apikey",
        "--set",
        "auth.apikey.pool.enabled=true",
    )
    docs = _docs(rendered)
    env = _worker_env(docs)
    names = {e["name"] for e in env}
    assert "ANTHROPIC_API_KEY_POOL" in names
    assert "ANTHROPIC_API_KEY" not in names
    pool_env = next(e for e in env if e["name"] == "ANTHROPIC_API_KEY_POOL")
    ref = pool_env["valueFrom"]["secretKeyRef"]
    assert ref["name"] == "anthropic-api-key-pool"
    assert ref["key"] == "pool"


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_oauth_pool_create_secret_renders_pool_secret() -> None:
    rendered = _helm_template(
        "--set",
        "auth.mode=oauth",
        "--set",
        "auth.oauth.pool.enabled=true",
        "--set",
        "auth.oauth.pool.createSecret=true",
        "--set",
        "auth.oauth.pool.size=2",
        "--set-json",
        'auth.oauth.pool.credentials=[{"access_token":"a"},{"access_token":"b"}]',
    )
    docs = _docs(rendered)
    secrets = [d for d in docs if d["kind"] == "Secret"]
    pool_secrets = [s for s in secrets if s["metadata"]["name"] == "claude-oauth-pool"]
    assert len(pool_secrets) == 1
    payload = pool_secrets[0]["stringData"]["pool"]
    arr = yaml.safe_load(payload)  # JSON is valid YAML
    assert isinstance(arr, list) and len(arr) == 2
    assert arr[0] == {"access_token": "a"}


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_apikey_pool_size_mismatch_fails() -> None:
    res = subprocess.run(
        [
            "helm",
            "template",
            "po",
            str(CHART_DIR),
            "--set",
            "auth.mode=apikey",
            "--set",
            "auth.apikey.pool.enabled=true",
            "--set",
            "auth.apikey.pool.createSecret=true",
            "--set",
            "auth.apikey.pool.size=5",
            "--set",
            "auth.apikey.pool.apiKeys={sk-aa,sk-bb}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    output = res.stdout + res.stderr
    assert "must match" in output or "size=5" in output


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_oauth_pool_create_secret_requires_credentials() -> None:
    res = subprocess.run(
        [
            "helm",
            "template",
            "po",
            str(CHART_DIR),
            "--set",
            "auth.mode=oauth",
            "--set",
            "auth.oauth.pool.enabled=true",
            "--set",
            "auth.oauth.pool.createSecret=true",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "credentials" in (res.stdout + res.stderr)


def test_auth_md_documents_pool() -> None:
    doc = (REPO_ROOT / "engdocs" / "auth.md").read_text()
    assert "CLAUDE_CREDENTIALS_POOL" in doc
    assert "ANTHROPIC_API_KEY_POOL" in doc


def test_workpools_doc_mentions_helm_path() -> None:
    doc = (REPO_ROOT / "engdocs" / "work-pools.md").read_text()
    assert "helm install po ./charts/po" in doc
    assert "## Helm install" in doc


# --------------------------------------------------------------------------
# HPA + pool concurrency for fanout demos (5wk.2)
# --------------------------------------------------------------------------


def _helm_template_with_files(*flags: str) -> str:
    cmd = ["helm", "template", "po", str(CHART_DIR), *flags]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_default_profile_has_no_hpa_and_static_replicas() -> None:
    docs = _docs(_helm_template())
    assert not any(d["kind"] == "HorizontalPodAutoscaler" for d in docs)
    workers = [
        d
        for d in docs
        if d["kind"] == "Deployment" and "worker" in d["metadata"]["name"]
    ]
    assert len(workers) == 1
    assert workers[0]["spec"].get("replicas") == 1


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_default_profile_pool_concurrency_env_present() -> None:
    docs = _docs(_helm_template())
    jobs = [d for d in docs if d["kind"] == "Job"]
    assert len(jobs) == 1
    env = jobs[0]["spec"]["template"]["spec"]["containers"][0]["env"]
    pc = next(e for e in env if e["name"] == "POOL_CONCURRENCY")
    assert pc["value"] == "5"
    script = jobs[0]["spec"]["template"]["spec"]["containers"][0]["command"][-1]
    assert "set-concurrency-limit" in script
    assert "POOL_CONCURRENCY" in script


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_demo_profile_renders_hpa_and_pool_concurrency() -> None:
    rendered = _helm_template_with_files("-f", str(CHART_DIR / "values-demo.yaml"))
    docs = _docs(rendered)
    hpas = [d for d in docs if d["kind"] == "HorizontalPodAutoscaler"]
    assert len(hpas) == 1
    hpa = hpas[0]
    assert hpa["spec"]["minReplicas"] == 20
    assert hpa["spec"]["maxReplicas"] == 100
    assert hpa["spec"]["scaleTargetRef"]["kind"] == "Deployment"
    assert "worker" in hpa["spec"]["scaleTargetRef"]["name"]
    assert hpa["spec"]["scaleTargetRef"]["apiVersion"] == "apps/v1"
    cpu_metric = next(m for m in hpa["spec"]["metrics"] if m["type"] == "Resource")
    assert cpu_metric["resource"]["name"] == "cpu"
    assert cpu_metric["resource"]["target"]["averageUtilization"] == 70

    # When HPA owns scaling, the Deployment must omit spec.replicas.
    workers = [
        d
        for d in docs
        if d["kind"] == "Deployment" and "worker" in d["metadata"]["name"]
    ]
    assert len(workers) == 1
    assert "replicas" not in workers[0]["spec"]

    # Pool concurrency wired through to register Job.
    jobs = [d for d in docs if d["kind"] == "Job"]
    env = jobs[0]["spec"]["template"]["spec"]["containers"][0]["env"]
    pc = next(e for e in env if e["name"] == "POOL_CONCURRENCY")
    assert pc["value"] == "100"


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_demo_profile_helm_lints_clean() -> None:
    res = subprocess.run(
        [
            "helm",
            "lint",
            str(CHART_DIR),
            "-f",
            str(CHART_DIR / "values-demo.yaml"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_autoscaling_enabled_omits_replicas_via_set_flag() -> None:
    rendered = _helm_template("--set", "worker.autoscaling.enabled=true")
    docs = _docs(rendered)
    workers = [
        d
        for d in docs
        if d["kind"] == "Deployment" and "worker" in d["metadata"]["name"]
    ]
    assert "replicas" not in workers[0]["spec"]
    assert any(d["kind"] == "HorizontalPodAutoscaler" for d in docs)


@pytest.mark.skipif(not _have_helm(), reason="helm not on PATH")
def test_pool_concurrency_zero_skips_set_call_in_script() -> None:
    rendered = _helm_template("--set", "pool.concurrencyLimit=0")
    docs = _docs(rendered)
    jobs = [d for d in docs if d["kind"] == "Job"]
    env = jobs[0]["spec"]["template"]["spec"]["containers"][0]["env"]
    pc = next(e for e in env if e["name"] == "POOL_CONCURRENCY")
    assert pc["value"] == "0"


def test_workpools_doc_documents_demo_profile() -> None:
    doc = (REPO_ROOT / "engdocs" / "work-pools.md").read_text()
    assert "values-demo.yaml" in doc
    assert "Demo profile" in doc
