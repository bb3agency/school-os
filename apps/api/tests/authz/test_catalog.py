"""Permission catalog and system roles match docs/07 §6.2 and §6.5 (FR-IAM-010, FR-IAM-011)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.authz import catalog

DOC = Path(__file__).resolve().parents[4] / "docs" / "07-security-architecture.md"
DOC_ROLE_KEYS = {"exam_coord": "exam_coordinator", "auditor_ro": "auditor_readonly"}
CELL = {"✓": "school", "S": "scoped", "✓ᴿ": "school_step_up"}
# Permissions granted in roles.yaml that the 07 §6.2 table does not list yet (with reason).
SEC_6_2 = "### 6.2 Roles \u00d7 permissions (defaults)"
SEC_6_5 = "### 6.5 Platform roles \u00d7 permissions"
NOT_IN_DOC_MATRIX = {
    "support.ticket.create": "proposed in 16 §19 Q6 / contract §5; all staff except auditor",
}
TENANT_ROLES = (
    "owner",
    "principal",
    "office_admin",
    "office_staff",
    "accountant",
    "exam_coordinator",
    "class_teacher",
    "teacher",
    "auditor_readonly",
)


def _table(section: str) -> tuple[list[str], dict[str, list[str]]]:
    text = DOC.read_text("utf-8")
    body = text.split(section, 1)[1]
    lines: list[str] = []
    for ln in body.splitlines():
        if ln.startswith("|"):
            lines.append(ln)
        elif lines:
            break  # end of the first table after the heading
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    rows: dict[str, list[str]] = {}
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows[cells[0].split()[0]] = cells[1:]
    return header[1:], rows


def doc_tenant_matrix() -> dict[str, dict[str, str]]:
    header, rows = _table(SEC_6_2)
    roles = [DOC_ROLE_KEYS.get(h, h) for h in header]
    matrix: dict[str, dict[str, str]] = {r: {} for r in roles}
    for perm, cells in rows.items():
        for role, cell in zip(roles, cells, strict=True):
            if cell != "—":
                matrix[role][perm] = CELL[cell]
    return matrix


def test_FR_IAM_010_roles_are_exactly_the_tenant_role_keys() -> None:
    assert tuple(catalog.system_roles()) == TENANT_ROLES
    assert set(doc_tenant_matrix()) == set(TENANT_ROLES)


def test_FR_IAM_011_roles_yaml_matches_docs_07_matrix_exactly() -> None:
    doc = doc_tenant_matrix()
    for key, role in catalog.system_roles().items():
        yaml_grants = {
            g.permission: ("scoped" if g.scoped else "school_step_up" if g.step_up else "school")
            for g in role.grants
            if g.permission not in NOT_IN_DOC_MATRIX
        }
        assert yaml_grants == doc[key], key


def test_FR_IAM_011_doc_matrix_permissions_all_exist_in_catalog() -> None:
    _, rows = _table(SEC_6_2)
    perms = catalog.permission_catalog()
    for perm in rows:
        assert perm in perms
        assert not perms[perm].is_platform
        assert not perms[perm].implicit


def test_FR_IAM_011_extra_grants_are_explained() -> None:
    _, rows = _table(SEC_6_2)
    granted = {g.permission for r in catalog.system_roles().values() for g in r.grants}
    assert granted - set(rows) == set(NOT_IN_DOC_MATRIX)
    support = [k for k, r in catalog.system_roles().items() if r.grant("support.ticket.create")]
    assert set(support) == set(TENANT_ROLES) - {"auditor_readonly"}


def test_FR_IAM_011_step_up_flags_match_doc_superscript() -> None:
    _, rows = _table(SEC_6_2)
    for perm, cells in rows.items():
        assert catalog.permission_catalog()[perm].step_up == any("ᴿ" in c for c in cells), perm


def test_FR_PLT_028_platform_permissions_match_docs_07_6_5() -> None:
    header, rows = _table(SEC_6_5)
    assert header[0] == "platform_owner"
    platform = {k: p for k, p in catalog.permission_catalog().items() if p.is_platform}
    assert set(platform) == set(rows)
    for key, raw in (
        (k, [ln for ln in DOC.read_text("utf-8").splitlines() if ln.startswith(f"| {k} ")])
        for k in rows
    ):
        assert platform[key].step_up == ("ᴿ" in raw[0].split("|")[1]), key


def test_SEC_003_platform_permissions_are_platform_prefixed_and_not_grantable() -> None:
    for key, pdef in catalog.permission_catalog().items():
        assert pdef.is_platform == key.startswith("platform.")
        assert re.match(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", key)
    for role in catalog.system_roles().values():
        assert not any(g.permission.startswith("platform.") for g in role.grants)
    with pytest.raises(catalog.CatalogError):
        catalog.tenant_permission("platform.tenants.read")
    with pytest.raises(catalog.CatalogError):
        catalog.tenant_permission("student.teleport")


def test_FR_IAM_002_privileged_roles_require_mfa() -> None:
    assert catalog.mfa_roles() == {"owner", "principal", "office_admin"}


def test_FR_IAM_010_auditor_membership_is_time_bound_14_days() -> None:
    ttl = catalog.system_roles()["auditor_readonly"].membership_ttl
    assert ttl is not None
    assert ttl.days == 14
    assert all(
        r.membership_ttl is None
        for k, r in catalog.system_roles().items()
        if k != "auditor_readonly"
    )


def test_FR_IAM_010_role_names_are_bilingual() -> None:
    for role in catalog.system_roles().values():
        assert role.name_en
        assert role.name_te
        assert any("ఀ" <= ch <= "౿" for ch in role.name_te), role.key


def test_catalog_rejects_inconsistent_yaml() -> None:
    perms = dict(catalog.permission_catalog())
    with pytest.raises(catalog.CatalogError):
        catalog._parse_roles(
            {
                "roles": {
                    "x_role": {"name_en": "X", "name_te": "X", "grants": {"user.manage": "school"}}
                }
            },
            perms,
        )
    with pytest.raises(catalog.CatalogError):
        catalog._parse_roles(
            {
                "roles": {
                    "x_role": {
                        "name_en": "X",
                        "name_te": "X",
                        "grants": {"platform.audit.read": "school"},
                    }
                }
            },
            perms,
        )
    with pytest.raises(catalog.CatalogError):
        catalog._parse_permissions(
            {"permissions": {"platform.x": {"description": "d", "is_platform": False}}}
        )
