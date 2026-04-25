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
        "--set", "auth.mode=oauth",
        "--set", "auth.oauth.persistence.enabled=true",
    )
    docs = _docs(rendered)
    pvc_names = [d["metadata"]["name"] for d in docs if d["kind"] == "PersistentVolumeClaim"]
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
            "helm", "template", "po", str(CHART_DIR),
            "--set", "auth.apikey.createSecret=true",
        ],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode != 0
    assert "auth.apikey.apiKey" in (res.stdout + res.stderr)


def test_workpools_doc_mentions_helm_path() -> None:
    doc = (REPO_ROOT / "engdocs" / "work-pools.md").read_text()
    assert "helm install po ./charts/po" in doc
    assert "## Helm install" in doc
