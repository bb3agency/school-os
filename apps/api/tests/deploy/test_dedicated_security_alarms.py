"""Dedicated hosts run the shared tier's security log filters (audit 2026-10-05 hardening
"Dedicated hosts have no security alarms"; SEC-007, docs/07 §15).

``infra/terraform/modules/dedicated_host`` repeats the ``modules/observability`` metric filters on the
host log group. ``tests/deploy/test_security_log_events.py`` pins every observability event to the
code that writes it; this test pins the dedicated copy to the observability one, so renaming an
event breaks a test on both tiers instead of disarming a dedicated alarm.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MODULES = ROOT / "infra" / "terraform" / "modules"
OBSERVABILITY = MODULES / "observability" / "main.tf"
DEDICATED = MODULES / "dedicated_host" / "main.tf"
FILTER = re.compile(r'pattern\s*=\s*"(\{.*\})"')
EVENT = re.compile(r'\$\.event = \\"([a-z0-9_.]+)\\"')


def _patterns(path: Path) -> set[str]:
    return set(FILTER.findall(path.read_text(encoding="utf-8")))


def _events(path: Path) -> set[str]:
    return set(EVENT.findall(path.read_text(encoding="utf-8")))


def test_SEC_007_dedicated_filters_are_copies_of_the_shared_tier_filters() -> None:
    dedicated = _patterns(DEDICATED)
    assert dedicated, "the dedicated host defines security metric filters"
    assert dedicated <= _patterns(OBSERVABILITY)


def test_SEC_007_dedicated_hosts_watch_audit_chains_sessions_and_sign_ins() -> None:
    assert {
        "audit.chain.broken",
        "audit.chain.verified",
        "refresh_token_reuse_detected",
        "security.auth.failed",
        "signin_failed",
        "step_up_failed",
        "security.rate_limited",
    } <= _events(DEDICATED)
    # Control-plane events never happen on a dedicated host.
    assert not {"fleet.heartbeat.rejected", "platform.operator.denied"} & _events(DEDICATED)
