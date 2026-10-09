"""Release provenance and Scorecard checks (audit 2026-10-05 P2-08 and hardening "Scorecard").

- P2-08 (a): a release workflow on CalVer tags builds and pushes images by digest, attests build
  provenance and the SBOM, runs ``deploy/dedicated/scripts/package.sh`` and attests the bundle; a
  dedicated host verifies the bundle against a SHA-256 that does not come from the bundle's own S3
  prefix (``upgrade.sh --sha256``, passed by the fleet workflow from the release).
- P2-08 (b): staging deploys only after CI passed on the same commit (CI is a reusable workflow
  called with ``needs:``, never ``workflow_run``), and the deploy roles pin ``job_workflow_ref``
  to the deploy workflow on main / release tags.
- Scorecard: CodeQL, dependency review, semgrep SARIF upload, hash-pinned tools, no unhashed
  ``pip install``/``uvx``, Dependabot only for directories with a Dockerfile, uv cooldown.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
WORKFLOWS = REPO / ".github" / "workflows"
TOOLS = REPO / ".github" / "tools"
CI_OIDC = REPO / "infra" / "terraform" / "modules" / "ci_oidc" / "main.tf"


def _wf(name: str) -> dict[str, Any]:
    doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    # PyYAML reads the key `on` as True.
    doc["on"] = doc.pop(True, doc.get("on"))
    return doc


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps", []))


def _uses(job: dict[str, Any]) -> list[str]:
    return [str(s["uses"]).split("@")[0] for s in _steps(job) if "uses" in s]


def _run(job: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) for s in _steps(job))


# --- P2-08 (a): release workflow ---------------------------------------------------------------


def test_P2_08_release_workflow_runs_on_calver_tags_only() -> None:
    wf = _wf("release.yml")
    assert set(wf["on"]) == {"push"}
    assert wf["on"]["push"] == {"tags": ["20[0-9][0-9].*.*"]}
    assert wf["permissions"] == {}


def test_P2_08_release_pushes_by_digest_and_attests_provenance_and_sbom() -> None:
    jobs = _wf("release.yml")["jobs"]
    images = jobs["images"]
    assert images["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    uses = _uses(images)
    assert "actions/attest-build-provenance" in uses
    assert "actions/attest-sbom" in uses
    attest = next(s for s in _steps(images) if "attest-build-provenance" in str(s.get("uses")))
    assert attest["with"]["subject-digest"] == "${{ steps.push.outputs.digest }}"
    assert attest["with"]["push-to-registry"] is True
    run = _run(images)
    assert "trivy image --exit-code 1" in run, "scan gate before push"
    assert "RepoDigests" in run, "the pushed digest is what gets attested and packaged"


def test_P2_08_release_packages_the_bundle_from_digests_and_attests_it() -> None:
    job = _wf("release.yml")["jobs"]["bundle"]
    assert "images" in job["needs"]
    run = _run(job)
    assert "deploy/dedicated/scripts/package.sh" in run
    assert "@${digest}" in run, "images by the digest pushed in the images job"
    attest = next(s for s in _steps(job) if "attest-build-provenance" in str(s.get("uses")))
    assert attest["with"]["subject-path"].endswith("schoolos-dedicated.tar.gz")
    # The checksum hosts verify is published with the GitHub release, not only next to the bundle.
    assert "gh release" in run
    assert "schoolos-dedicated.tar.gz.sha256" in run


def test_P2_08_fleet_deploy_passes_a_checksum_from_the_github_release() -> None:
    wf = _wf("deploy-dedicated.yml")
    text = (WORKFLOWS / "deploy-dedicated.yml").read_text(encoding="utf-8")
    assert "gh release download" in text or "releases/tags" in text
    assert "--sha256" in text
    # verified provenance of the bundle before the hosts are told to install it
    assert "gh attestation verify" in text
    assert wf["jobs"]["preflight"]["permissions"]["attestations"] == "read"
    assert "--signer-workflow" in text
    assert "release.yml" in text


# --- P2-08 (b): staging needs green CI; deploy roles pin the workflow -----------------------------


def test_P2_08_ci_is_reusable_and_staging_deploy_needs_it() -> None:
    ci = _wf("ci.yml")
    assert "workflow_call" in ci["on"]
    staging = _wf("deploy-staging.yml")
    jobs = staging["jobs"]
    assert jobs["ci"]["uses"] == "./.github/workflows/ci.yml"
    assert "ci" in jobs["build"]["needs"]
    assert "ci" in jobs["deploy"]["needs"]
    text = (WORKFLOWS / "deploy-staging.yml").read_text(encoding="utf-8")
    assert "workflow_run" not in text


def test_P2_08_deploy_roles_pin_job_workflow_ref() -> None:
    text = CI_OIDC.read_text(encoding="utf-8")
    trust = text[text.index('data "aws_iam_policy_document" "deploy_trust"') :]
    trust = trust[: trust.index('resource "aws_iam_role" "deploy"')]
    assert "token.actions.githubusercontent.com:job_workflow_ref" in trust
    assert "local.deploy_workflow_refs" in trust


# --- Scorecard ---------------------------------------------------------------------------------


def test_scorecard_codeql_runs_on_every_language() -> None:
    wf = _wf("codeql.yml")
    assert {"pull_request", "push", "schedule"} <= set(wf["on"])
    job = wf["jobs"]["analyze"]
    assert job["permissions"]["security-events"] == "write"
    languages = {m["language"] for m in job["strategy"]["matrix"]["include"]}
    assert languages == {"python", "javascript-typescript", "actions"}
    assert "github/codeql-action/init" in _uses(job)
    assert "github/codeql-action/analyze" in _uses(job)


def test_scorecard_dependency_review_on_pull_requests() -> None:
    wf = _wf("dependency-review.yml")
    assert set(wf["on"]) == {"pull_request"}
    job = wf["jobs"]["dependency-review"]
    assert "actions/dependency-review-action" in _uses(job)
    step = next(s for s in _steps(job) if "dependency-review-action" in str(s.get("uses")))
    assert step["with"]["fail-on-severity"] == "high"
    assert "AGPL-3.0-only" in step["with"]["deny-licenses"]


def test_scorecard_semgrep_sarif_is_uploaded() -> None:
    job = _wf("ci.yml")["jobs"]["security"]
    assert job["permissions"]["security-events"] == "write"
    assert "--sarif-output=semgrep.sarif" in _run(job)
    assert "github/codeql-action/upload-sarif" in _uses(job)


@pytest.mark.parametrize("name", ["semgrep", "zizmor"])
def test_scorecard_tools_are_hash_pinned(name: str) -> None:
    lines = (TOOLS / f"{name}-requirements.txt").read_text(encoding="utf-8").splitlines()
    packages = [ln for ln in lines if re.match(r"^[a-z0-9]", ln, re.IGNORECASE)]
    assert any(ln.startswith(f"{name}==") for ln in packages)
    assert all(re.match(r"^[\w.-]+==[\w.]+ \\$", ln) for ln in packages), "exact pins"
    text = "\n".join(lines)
    assert text.count("--hash=sha256:") >= len(packages)


def test_scorecard_no_unhashed_tool_installs() -> None:
    files = [*WORKFLOWS.glob("*.yml"), REPO / "Makefile", *REPO.glob("apps/*/Dockerfile")]
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if re.search(r"\buvx\s+\w", text) or re.search(r"\bpip install\b", text):
            offenders.append(path.name)
        if re.search(r"--with\s+semgrep==", text):
            offenders.append(path.name)
    assert offenders == []


def test_scorecard_dependabot_docker_directories_have_a_dockerfile() -> None:
    doc = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    docker = next(u for u in doc["updates"] if u["package-ecosystem"] == "docker")
    for directory in docker["directories"]:
        folder = REPO / directory.lstrip("/")
        has = list(folder.glob("Dockerfile*")) or list(folder.glob("compose*.y*ml"))
        assert has, f"{directory} has no Dockerfile"


def test_scorecard_uv_dependency_cooldown() -> None:
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert cfg["tool"]["uv"]["exclude-newer"] == "7 days"
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "--exclude-rule package_managers.uv.uv-missing-dependency-cooldown" not in makefile
