"""Release guard for system-role changes (ADR-0022; FR-IAM-011).

Existing schools receive a roles.yaml change only when an operator runs
``python -m app.identity.sync_system_roles --apply`` after the release's migrations
(docs/10 §8). This test pins a fingerprint of what that command reconciles (role keys, display
names and grants), so a change to the system roles cannot land unnoticed: when it fails, update
``PINNED`` below AND say in the PR description / release notes that the release needs the
post-migration system-role sync (and whether ``--prune`` is intended).
"""

from __future__ import annotations

import hashlib
import json

from app.authz.catalog import system_roles

# R2-1C + R2-1B + R2-2D + R2-2E (fee.reconcile) + R2-3G (message.send) grants
# + R3-1D (readiness.calendar.manage) + R3-1C (disclosure.edit, disclosure.publish)
# + R3-1A (privacy.read, privacy.request.manage, privacy.request.decide)
# + R3-1B (consent.read, consent.record, privacy.breach.read, privacy.breach.manage)
# + FC-12 (privacy.hold.manage: owner, principal; ADR-0059)
# + FT-01 (exam.unlock for the principal; D84)
# + FT-08 (year_result.read: principal, office admin, exam coordinator; year_result.confirm:
#   principal; D85)
# + FP-02 (message.closure.send for the principal; D85)
PINNED = "a2fbbcbc782b0eab7e4cda0b760bc0ea7027111062961ae5b1ebb6acb31e9a78"


def fingerprint() -> str:
    canonical = {
        key: {
            "name_en": role.name_en,
            "name_te": role.name_te,
            "grants": sorted(role.permission_keys),
        }
        for key, role in sorted(system_roles().items())
    }
    raw = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_ADR_0022_system_role_changes_are_flagged_for_the_post_migration_sync() -> None:
    assert fingerprint() == PINNED, (
        "roles.yaml system roles changed: update PINNED to "
        f"{fingerprint()} and note in the release that operators must run "
        "`python -m app.identity.sync_system_roles --apply` after migrations (ADR-0022, "
        "docs/10 §8)"
    )
