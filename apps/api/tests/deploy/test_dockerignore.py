"""The image build context stays small (docs/10 §6): local-only trees never reach `docker build`.

Agent worktrees (`.claude/worktrees/*`, each a full checkout with its own `node_modules` and
`.venv`), nested virtualenvs and local data made every build send gigabytes to the daemon.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
REQUIRED = {".claude", "**/.venv", "**/node_modules", "**/.next", ".data", "evals/reports"}


def _patterns() -> set[str]:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return {line.strip().rstrip("/") for line in lines if line.strip() and not line.startswith("#")}


def test_NFR_MNT_002_build_context_excludes_local_only_trees() -> None:
    missing = REQUIRED - _patterns()
    assert not missing, f".dockerignore must exclude {sorted(missing)}"
