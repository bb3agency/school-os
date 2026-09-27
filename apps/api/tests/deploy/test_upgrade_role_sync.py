"""Dedicated upgrades sync the system roles after migrations (ADR-0022 amendment 2026-09-27,
FR-IAM-010, FR-IAM-011, SEC-007; docs/10 §15.5).

``deploy/dedicated/scripts/upgrade.sh`` runs ``sync-system-roles.sh --apply`` (never ``--prune``)
right after ``migrate`` and before the services restart; any exit code other than 0 fails the
upgrade (the ERR trap rolls the host back). The helper is exercised with bash and a fake
sync script, so no Docker, database or AWS is needed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
SCRIPTS = REPO / "deploy" / "dedicated" / "scripts"
UPGRADE = SCRIPTS / "upgrade.sh"
LIB = SCRIPTS / "lib.sh"


def _bash() -> str | None:
    if sys.platform == "win32":
        # Git for Windows' bash (System32\bash.exe would start WSL instead).
        git = shutil.which("git")
        for parent in Path(git).resolve().parents if git else ():
            candidate = parent / "bin" / "bash.exe"
            if candidate.exists():
                return str(candidate)
        return None
    return shutil.which("bash")


def test_FR_IAM_011_upgrade_applies_the_sync_after_migrations_and_never_prunes() -> None:
    lines = [ln.strip() for ln in UPGRADE.read_text(encoding="utf-8").splitlines()]
    code = [ln for ln in lines if ln and not ln.startswith("#")]
    migrate = code.index("sos_compose run --rm migrate")
    sync = code.index('upgrade_sync_system_roles "$(active_release_dir)/scripts"')
    restart = next(i for i, ln in enumerate(code) if ln.startswith("for svc in "))
    trap = code.index("trap rollback ERR")
    assert trap < migrate < sync < restart, "sync runs after migrate, under the rollback trap"
    for path in (UPGRADE, LIB):
        body = "\n".join(
            ln
            for ln in path.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")
        )
        assert "--prune" not in body, f"{path.name} must never prune grants automatically"


def _run_helper(tmp_path: Path, fake_rc: int) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not installed")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    calls = tmp_path / "calls.txt"
    fake = scripts / "sync-system-roles.sh"
    fake.write_text(
        f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "{calls.as_posix()}"\nexit {fake_rc}\n',
        encoding="utf-8",
        newline="\n",
    )
    fake.chmod(0o755)
    harness = (
        "set -Eeuo pipefail\n"
        f'. "{LIB.as_posix()}"\n'
        f'upgrade_sync_system_roles "{scripts.as_posix()}"\n'
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("SOS_")}
    return subprocess.run(
        [bash, "-c", harness], capture_output=True, text=True, env=env, timeout=60, check=False
    )


def test_FR_IAM_011_sync_passes_only_apply(tmp_path: Path) -> None:
    res = _run_helper(tmp_path, 0)
    assert res.returncode == 0, res.stderr
    assert (tmp_path / "calls.txt").read_text(encoding="utf-8").splitlines() == ["--apply"]
    assert "in line or applied" in res.stderr


@pytest.mark.parametrize(
    ("rc", "message"),
    [
        (4, "exit 4"),  # a school failed or a custom role holds a system key
        (1, "refused"),  # wrong role / migrations missing
        (2, "exit code 2"),
        (3, "exit code 3"),  # cannot happen with --apply; still a failure
    ],
)
def test_SEC_007_sync_failure_fails_the_upgrade_loudly(
    tmp_path: Path, rc: int, message: str
) -> None:
    res = _run_helper(tmp_path, rc)
    assert res.returncode == 1
    assert "[ERR]" in res.stderr
    assert message in res.stderr
