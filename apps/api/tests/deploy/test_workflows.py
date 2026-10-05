"""GitHub Actions guard rails (audit W3-04, owner decision 2026-10-04; docs/10 §7).

A workflow that runs pull-request code must never run with the base repository's secrets
(``pull_request_target``), and every external action is pinned by a full commit SHA, so a moved tag
cannot change what runs. Local actions (``./.github/actions/...``) are part of the repository.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
GITHUB = ROOT / ".github"
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)
PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _yaml_files() -> list[Path]:
    files = sorted([*GITHUB.rglob("*.yml"), *GITHUB.rglob("*.yaml")])
    assert files, "no workflow files found"
    return files


def test_W3_04_no_workflow_runs_on_pull_request_target() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for p in _yaml_files()
        if re.search(r"\bpull_request_target\b", p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_W3_04_every_external_action_is_pinned_by_sha() -> None:
    unpinned = [
        f"{p.relative_to(ROOT)}: {ref}"
        for p in _yaml_files()
        for ref in USES.findall(p.read_text(encoding="utf-8"))
        if not ref.startswith("./") and not ref.startswith("docker://") and not PINNED.match(ref)
    ]
    assert unpinned == []
