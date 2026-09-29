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

PINNED = "700e90be98eb12fe2a24b396ba7538f0d4e516008329b2599e5f7701df3ebe33"


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
