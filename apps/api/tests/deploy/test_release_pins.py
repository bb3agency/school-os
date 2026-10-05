"""Dedicated hosts run only images pinned by digest (docs/07 §13 SEC-030; SLSA; audit 2026-10-05).

``render_compose_env`` (deploy/dedicated/scripts/lib.sh) writes the image references that
``docker compose`` pulls. It used to fall back, when a release had no ``release.env``, to bare
tags (``schoolos/api:<version>``), which Docker resolves on Docker Hub, outside our registry, and
it copied whatever ``release.env`` said without checking for a digest. It now refuses both.
Synthetic values only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
LIB = REPO / "deploy" / "dedicated" / "scripts" / "lib.sh"
DIGEST = "sha256:" + "ab" * 32
REGISTRY = "111122223333.dkr.ecr.ap-south-1.amazonaws.com/schoolos"


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


def _render(tmp_path: Path, release_env: str | None) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not installed")
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "host.env").write_text("SOS_DEPLOYMENT_MODE=dedicated\n", encoding="utf-8", newline="\n")
    (etc / "secrets.env").write_text("SOS_SYNTHETIC=1\n", encoding="utf-8", newline="\n")
    release = tmp_path / "release"
    release.mkdir()
    if release_env is not None:
        (release / "release.env").write_text(release_env, encoding="utf-8", newline="\n")
    harness = (
        "set -Eeuo pipefail\n"
        f'. "{LIB.as_posix()}"\n'
        "SOS_VERSION=2026.10.1\n"
        f'render_compose_env "{release.as_posix()}"\n'
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("SOS_")}
    env["SOS_ETC"] = etc.as_posix()
    env["SOS_HOST_ENV"] = (etc / "host.env").as_posix()
    return subprocess.run(
        [bash, "-c", harness], capture_output=True, text=True, env=env, timeout=60, check=False
    )


def _pins(**images: str) -> str:
    return "SOS_RELEASE=2026.10.1\n" + "".join(f"{k}={v}\n" for k, v in images.items())


def test_SEC_030_a_release_without_release_env_is_refused(tmp_path: Path) -> None:
    result = _render(tmp_path, None)
    assert result.returncode != 0
    assert not (tmp_path / "etc" / "compose.env").exists()


def test_SEC_030_an_image_without_a_digest_is_refused(tmp_path: Path) -> None:
    result = _render(
        tmp_path,
        _pins(
            SOS_API_IMAGE=f"{REGISTRY}/api:2026.10.1@{DIGEST}",
            SOS_WEB_IMAGE=f"{REGISTRY}/web:2026.10.1",
            SOS_WORKER_IMAGE=f"{REGISTRY}/worker:2026.10.1@{DIGEST}",
        ),
    )
    assert result.returncode != 0
    assert not (tmp_path / "etc" / "compose.env").exists()


def test_SEC_030_a_missing_image_pin_is_refused(tmp_path: Path) -> None:
    result = _render(
        tmp_path,
        _pins(
            SOS_API_IMAGE=f"{REGISTRY}/api:2026.10.1@{DIGEST}",
            SOS_WEB_IMAGE=f"{REGISTRY}/web:2026.10.1@{DIGEST}",
        ),
    )
    assert result.returncode != 0


def test_SEC_030_digest_pinned_images_are_rendered(tmp_path: Path) -> None:
    pins = {
        "SOS_API_IMAGE": f"{REGISTRY}/api:2026.10.1@{DIGEST}",
        "SOS_WEB_IMAGE": f"{REGISTRY}/web:2026.10.1@{DIGEST}",
        "SOS_WORKER_IMAGE": f"{REGISTRY}/worker:2026.10.1@{DIGEST}",
    }
    result = _render(tmp_path, _pins(**pins))
    assert result.returncode == 0, result.stderr
    rendered = (tmp_path / "etc" / "compose.env").read_text(encoding="utf-8")
    for name, value in pins.items():
        assert f"{name}={value}\n" in rendered
