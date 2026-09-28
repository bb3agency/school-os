"""Worker image contract (FR-EXP-002..004, docs/04 §6 queue ``pdf``, docs/10 §6).

The ``worker`` target of apps/api/Dockerfile bakes headless Chromium for the PDF renderer
(app/core/pdf.py never downloads a browser). These checks read the Dockerfile and the installed
playwright package, so a playwright bump without a matching browser, a root worker or a worker that
misses a queue fails CI before an image is built. The built image itself renders a PDF in the CI
``images (worker)`` job (smoke step).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import playwright

REPO = Path(__file__).resolve().parents[4]
DOCKERFILE = REPO / "apps" / "api" / "Dockerfile"


def _stage(name: str) -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    m = re.search(rf"(?ms)^FROM \S+ AS {name}\n(.*?)(?=^FROM |\Z)", text)
    assert m, f"Dockerfile has no stage {name}"
    return m.group(1)


def _browsers() -> dict[str, str]:
    path = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(b["name"]): str(b["revision"]) for b in data["browsers"]}


def test_FR_EXP_002_worker_bakes_the_headless_shell_playwright_expects() -> None:
    worker = _stage("worker")
    m = re.search(r"(?m)^ARG CHROMIUM_REVISION=(\d+)$", worker)
    assert m, "worker stage pins ARG CHROMIUM_REVISION"
    assert m.group(1) == _browsers()["chromium-headless-shell"], (
        "bump CHROMIUM_REVISION together with playwright (its browsers.json)"
    )
    assert "playwright install --with-deps --only-shell chromium" in worker
    assert "chromium_headless_shell-${CHROMIUM_REVISION}" in worker
    assert re.search(r"(?m)^ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright$", worker)


def test_SEC_030_images_run_as_non_root() -> None:
    users = re.findall(r"(?m)^USER (\S+)$", _stage("worker"))
    assert users[-1] == "10001:10001", "the worker drops back to the app user after installing"
    assert re.findall(r"(?m)^USER (\S+)$", _stage("base"))[-1] == "10001:10001"
    assert not re.search(r"(?m)^USER ", _stage("api")), "api inherits the app user from base"


def test_NFR_AVL_002_worker_default_command_consumes_every_queue() -> None:
    from sos_worker.celery_app import QUEUES

    cmd = re.search(r"(?m)^CMD (\[.*\])$", _stage("worker"))
    assert cmd
    argv = json.loads(cmd.group(1))
    assert set(argv[argv.index("-Q") + 1].split(",")) == set(QUEUES)


def test_api_is_the_default_target() -> None:
    """Builds without --target (local compose) keep getting the api image."""
    froms = re.findall(r"(?m)^FROM \S+ AS (\w+)$", DOCKERFILE.read_text(encoding="utf-8"))
    assert froms[-1] == "api"
