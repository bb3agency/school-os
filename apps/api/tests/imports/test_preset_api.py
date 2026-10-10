"""Import template library and the refresh from the school's current ERP over HTTP
(FR-IMP-030..033, FR-DQ-033; ADR-0041). Synthetic data only. Role coverage is in
tests/security/test_authz_matrix.py; other-school import ids in tests/security/test_bola.py."""

from __future__ import annotations

import io
import sys
import uuid
from typing import Any

import pytest
from openpyxl import load_workbook
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.dq import service as dq
from app.students import service as students

pytestmark = pytest.mark.db
S = sys.modules["sos_test_imports_support"]


def test_FR_IMP_020_presets_are_listed_and_templates_download(world: Any, api: Any) -> None:
    staff = world.person("office_staff")
    listed = api.call(staff, "GET", "/api/v1/import-presets")
    assert listed.status_code == 200
    presets = {p["key"]: p for p in listed.json()}
    assert set(presets) == {
        "schoolos-blank",
        "register-excel",
        "udise-plus-student-list",
        "erp-student-export",
    }
    assert presets["udise-plus-student-list"]["verified"] is False
    assert presets["erp-student-export"]["import_source"] == "manual_entry"

    got = api.call(
        staff, "GET", "/api/v1/import-presets/template", params={"preset": "schoolos-blank"}
    )
    assert got.status_code == 200
    assert got.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "schoolos-schoolos-blank-v1.xlsx" in got.headers["content-disposition"]
    sheet = load_workbook(io.BytesIO(got.content)).active
    assert sheet is not None
    assert [c.value for c in sheet[1]][:2] == ["Admission number", "Full name"]
    assert sheet.max_row == 1
    missing = api.call(staff, "GET", "/api/v1/import-presets/template", params={"preset": "nope"})
    assert missing.status_code == 404
    bad = api.call(staff, "GET", "/api/v1/import-presets/template", params={"preset": "../x"})
    assert bad.status_code == 422
    # A teacher cannot import (docs/07 §6.2): no template library either.
    assert api.call(world.person("teacher"), "GET", "/api/v1/import-presets").status_code == 403


def _parsed(world: Any, admin: Engine, rows: list[list[Any]], source: str) -> uuid.UUID:
    batch: uuid.UUID = S.start(admin, world.a, S.xlsx_bytes(rows), source=source)
    return batch


def test_FR_IMP_023_erp_refresh_keeps_register_values_and_lists_differences(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    number = S.adm("ERP")
    name = f"Synthetica Kommuri {uuid.uuid4().hex[:4].upper()}"
    S.imported(
        admin_engine,
        world.a,
        S.xlsx_bytes(
            [
                ["Admission number", "Full name", "Date of birth", "Gender"],
                [number, name, "14/03/2012", "Male"],
            ]
        ),
    )
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, number)
    assert sid is not None

    # The ERP export: other headings and one different spelling.
    erp_name = name + " K"
    batch_id = _parsed(
        world,
        admin_engine,
        [
            ["Enrollment No", "Student Name", "Sex", "Remarks"],
            [number, erp_name, "Male", "ok"],
        ],
        "manual_entry",
    )
    admin = world.person("office_admin")
    mapped = api.call(
        admin,
        "GET",
        f"/api/v1/imports/{batch_id}/preset-mapping",
        params={"preset": "erp-student-export"},
    )
    assert mapped.status_code == 200, mapped.text
    body = mapped.json()
    assert body["source_matches"] is True
    assert [c["target"] for c in body["columns"]] == ["admission_no", "full_name", "gender", None]
    assert "Father Name" in body["missing"]
    assert (
        api.call(
            admin,
            "GET",
            f"/api/v1/imports/{batch_id}/preset-mapping",
            params={"preset": "no-such-preset"},
        ).status_code
        == 404
    )

    got = api.call(admin, "GET", f"/api/v1/imports/{batch_id}")
    columns = [{"index": c["index"], "target": c["target"]} for c in body["columns"] if c["target"]]
    put = api.call(
        admin,
        "PUT",
        f"/api/v1/imports/{batch_id}/mapping",
        json={"columns": columns},
        headers={"If-Match": got.headers["ETag"]},
    )
    assert put.status_code == 200, put.text
    assert api.call(admin, "POST", f"/api/v1/imports/{batch_id}/validate").status_code == 202
    assert S.run_validate(world.a, batch_id) == "validated"
    assert S.commit(world.a, batch_id) == "committed"

    owner = world.a.people["owner"]
    with tenant_session(world.a.tenant_id, owner.user_id) as db:
        canonical = students.canonical_values(db, [sid], ["full_name"])[sid]
    assert canonical["full_name"].value == name, "the register keeps its value (BR-01)"
    with tenant_session(world.a.tenant_id, admin.user_id) as db:
        dq.run_checks(db, S.ctx(world.a), student_ids=[sid])
    with admin_engine.connect() as c:
        rule_ids = {
            r[0]
            for r in c.execute(
                text(
                    "SELECT rule_id FROM sis.dq_findings WHERE student_id = :s "
                    "AND attribute_key = 'full_name' AND status = 'open'"
                ),
                {"s": sid},
            )
        }
    assert "DQ-030" in rule_ids


def test_FR_IMP_021_preset_mapping_respects_the_import_source(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """A UDISE+ import may not record the date of admission: the preset leaves it unmapped."""
    batch_id = _parsed(
        world,
        admin_engine,
        [
            ["Adm. No.", "Name of the Pupil", "Date of Admission"],
            [S.adm(), "Synthetica U", "01/06/2020"],
        ],
        "udise_plus",
    )
    res = api.call(
        world.person("office_admin"),
        "GET",
        f"/api/v1/imports/{batch_id}/preset-mapping",
        params={"preset": "register-excel"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["source_matches"] is False
    assert [c["target"] for c in body["columns"]] == ["admission_no", "full_name", None]
