"""Security alarms read structured log lines (docs/07 §15, SEC-007; audit 2026-10-05 P2-02).

``infra/terraform/modules/observability`` turns log lines into metrics with CloudWatch Logs metric
filters (``{ $.event = "..." }``), because the application publishes no security metrics itself.
These tests pin the contract from both sides: every event or route a filter matches is one the
services really write, in the JSON shape the filter expects. Renaming an event then breaks a test
instead of silently disarming an alarm.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.audit import verify_all
from app.audit.schemas import VerifyResult
from app.core.config import Environment, Settings
from app.core.logging import clear_context, setup_logging

ROOT = Path(__file__).resolve().parents[4]
API = ROOT / "apps" / "api"
OBSERVABILITY = ROOT / "infra" / "terraform" / "modules" / "observability" / "main.tf"
EVENT = re.compile(r'\$\.event = \\"([a-z0-9_.]+)\\"')
ROUTE = re.compile(r'\$\.route = \\"([A-Z]+) ([^\\"]+)\\"')


def _filter_events() -> set[str]:
    return set(EVENT.findall(OBSERVABILITY.read_text(encoding="utf-8")))


@pytest.fixture
def lines() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    setup_logging(Settings(env=Environment.CI, log_level="INFO"), stream=stream)
    clear_context()
    yield stream
    clear_context()
    setup_logging(Settings(env=Environment.CI, log_level="INFO"))


def _events(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_SEC_007_audit_chain_alarms_match_the_worker_log_lines(lines: io.StringIO) -> None:
    """The P1 alarm counts ``audit.chain.broken``; the missing-run alarm counts every chain."""
    verify_all._log_result(VerifyResult(ok=True, checked=3), chain="platform")
    verify_all._log_result(VerifyResult(ok=False, checked=3, first_bad_seq=2, reason="hash"))
    written = [line["event"] for line in _events(lines)]
    assert written == ["audit.chain.verified", "audit.chain.broken"]
    assert {"audit.chain.verified", "audit.chain.broken"} <= _filter_events()


@pytest.mark.parametrize(
    ("event", "source"),
    [
        ("fleet.heartbeat.rejected", API / "app" / "platform" / "fleet.py"),
        ("platform.operator.denied", API / "app" / "platform" / "auth.py"),
        ("http.request", API / "app" / "core" / "middleware.py"),
        (
            "refresh_token_reuse_detected",
            ROOT / "apps" / "web" / "src" / "server" / "auth" / "refresh.ts",
        ),
    ],
)
def test_docs07_s15_every_filtered_event_is_written_by_its_service(
    event: str, source: Path
) -> None:
    assert event in _filter_events()
    assert f'"{event}"' in source.read_text(encoding="utf-8")


def test_docs07_s15_every_filter_event_is_pinned_here() -> None:
    pinned = {
        "audit.chain.verified",
        "audit.chain.broken",
        "fleet.heartbeat.rejected",
        "platform.operator.denied",
        "http.request",
        "refresh_token_reuse_detected",
    }
    assert _filter_events() == pinned


def test_docs07_s15_filtered_routes_exist_in_the_api() -> None:
    """The access log labels a request ``"<METHOD> <route template>"`` (core/middleware.py)."""
    openapi = json.loads((API / "openapi.json").read_text(encoding="utf-8"))
    routes = ROUTE.findall(OBSERVABILITY.read_text(encoding="utf-8"))
    assert routes, "no route filter found"
    for method, path in routes:
        assert method.lower() in openapi["paths"].get(path, {}), f"{method} {path}"
