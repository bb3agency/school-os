"""The release job that builds the Tally edge agent's MSI (D69, ADR-0032; docs/10 §13).

Windows runner, actions pinned by SHA (also test_workflows.py), WiX v4 at the version in
``apps/edge-agent/installer/pins.toml``, Authenticode signing only when the signing secrets are
present (otherwise the file is renamed ``*-unsigned.msi``), and no secret is echoed.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[4]
RELEASE = REPO / ".github" / "workflows" / "release.yml"
PINS = REPO / "apps" / "edge-agent" / "installer" / "pins.toml"
PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _job() -> dict[str, Any]:
    workflow = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    job: dict[str, Any] = workflow["jobs"]["edge-agent-msi"]
    return job


def _steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _job()["steps"]
    return steps


def _step(name_part: str) -> dict[str, Any]:
    found = [s for s in _steps() if name_part in s.get("name", "")]
    assert len(found) == 1, name_part
    return found[0]


def test_D69_msi_job_runs_on_windows_after_the_tag_check_with_read_only_token() -> None:
    job = _job()
    assert job["runs-on"] == "windows-latest"
    assert job["needs"] == "preflight"
    assert job["permissions"] == {"contents": "read"}


def test_D69_msi_job_actions_are_pinned_by_sha() -> None:
    uses = [s["uses"] for s in _steps() if "uses" in s]
    assert uses
    assert all(PINNED.match(ref) for ref in uses), uses
    assert any(ref.startswith("actions/upload-artifact@") for ref in uses)


def test_D69_wix_v4_is_installed_as_a_pinned_dotnet_tool() -> None:
    pins = tomllib.loads(PINS.read_text(encoding="utf-8"))
    assert _job()["env"]["WIX_VERSION"] == pins["wix"]["version"]
    assert pins["wix"]["version"].startswith("4.")
    run = _step("Install WiX")["run"]
    assert "dotnet tool install --global wix --version $env:WIX_VERSION" in run
    assert "WixToolset.Util.wixext/$env:WIX_VERSION" in run


def test_D69_build_runs_build_ps1_under_windows_powershell() -> None:
    step = _step("Build the MSI")
    assert step["shell"] == "powershell"  # Windows PowerShell 5.1, not pwsh
    assert r".\apps\edge-agent\installer\build.ps1 -Version $env:VERSION" in step["run"]


def test_D69_signing_only_with_the_secrets_otherwise_named_unsigned() -> None:
    enabled = _job()["env"]["SIGNING_ENABLED"]
    assert "secrets.WINDOWS_SIGNING_CERT_PFX_BASE64 != ''" in enabled
    assert "secrets.WINDOWS_SIGNING_CERT_PASSWORD != ''" in enabled
    sign = _step("Sign")
    assert sign["if"] == "env.SIGNING_ENABLED == 'true'"
    assert "signtool" in sign["run"]
    assert "verify /pa" in sign["run"]
    assert "Remove-Item -LiteralPath $pfx" in sign["run"]  # the key never stays on the runner
    unsigned = _step("unsigned")
    assert unsigned["if"] == "env.SIGNING_ENABLED != 'true'"
    assert "-x64-unsigned.msi" in unsigned["run"]
    # Only the signing step sees the secrets, through its env (never inline in a script).
    for step in _steps():
        assert "secrets." not in step.get("run", "")
        if step.get("name") != sign["name"]:
            assert "secrets." not in str(step.get("env", {}))


def test_D69_no_secret_is_echoed() -> None:
    run = _step("Sign")["run"]
    for line in run.splitlines():
        if re.search(r"\b(echo|Write-(Output|Host))\b", line):
            assert "CERT_" not in line, line
    assert "Out-Null" in next(line for line in run.splitlines() if "signtool.FullName sign" in line)
