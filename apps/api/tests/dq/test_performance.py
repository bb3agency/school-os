"""DQ check of a 2,000-student school (US-501 AC3, FR-DQ-005, NFR-PERF-005: <= 2 minutes).

A synthetic school gets 2,000 students with register identity values, Aadhaar-as-printed values
(C3, encrypted with the school's key like the service does), parents, a UDISE+ observation for
some and enrolments in three sections, bulk-inserted with the admin engine for speed. A full
pre-check (all rules + the CISCE profile) runs through ``dq.service.run_checks``; then an
unchanged re-run (the reconcile path over existing findings). Both must stay well inside the
budget. Synthetic data only.
"""

from __future__ import annotations

import random
import sys
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.textnorm import comparison_key
from app.devtools.names import generate_name, variants
from app.dq import service as dq
from app.students import crypto

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]
SW = DS.SW
W = SW.W
N = 2000
BUDGET_S = 120.0
TARGET_S = 40.0  # "well under": a third of the budget on a CI runner


def _value(
    tenant_id: uuid.UUID, student_id: uuid.UUID, key: str, source: str, **columns: Any
) -> dict[str, Any]:
    base = {
        "by": None,
        "id": uuid.uuid4(),
        "t": tenant_id,
        "s": student_id,
        "k": key,
        "src": source,
        "txt": None,
        "dt": None,
        "norm": None,
        "ct": None,
        "kv": None,
    }
    return base | columns


@pytest.fixture(scope="module")
def big(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    SW.configure_keyring()
    school = W.School(W.provision_school())
    owner = W.add_member(admin_engine, school.tenant_id, ["owner"])
    school.people["owner"] = owner
    W.build_structure(school, owner)
    sections = [school.ids[k] for k in ("section_9a", "section_9c", "section_10a")]
    rng = random.Random(20260927)
    tid = school.tenant_id
    students_rows, values, enrolments = [], [], []
    with tenant_session(tid) as db:
        for i in range(N):
            sid = uuid.uuid4()
            name = generate_name(rng)
            father = generate_name(rng, gender="m").canonical
            dob = f"{rng.randint(2010, 2013)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            gender = "female" if name.gender == "f" else "male"
            students_rows.append({"id": sid, "t": tid, "adm": f"2019/{i:05d}"})
            reg = "admission_register"
            values += [
                _value(
                    tid,
                    sid,
                    "full_name",
                    reg,
                    txt=name.canonical,
                    norm=comparison_key(name.canonical),
                ),
                _value(tid, sid, "dob", reg, dt=dob),
                _value(tid, sid, "gender", reg, txt=gender),
                _value(tid, sid, "father_name", reg, txt=father, norm=comparison_key(father)),
                _value(tid, sid, "admission_no", reg, txt=f"2019/{i:05d}"),
            ]
            roll = rng.random()
            aadhaar_name = (
                name.canonical
                if roll < 0.6
                else rng.choice(variants(name)).text
                if roll < 0.9
                else generate_name(rng).canonical
            )
            for key, plain in (
                ("aadhaar_name_as_printed", aadhaar_name),
                ("aadhaar_dob_as_printed", dob if rng.random() < 0.9 else "2009-01-01"),
                ("aadhaar_gender_as_printed", gender),
                ("aadhaar_last4", f"{rng.randint(0, 9999):04d}"),
            ):
                value_id = uuid.uuid4()
                blob, version = crypto.encrypt_value(
                    db,
                    plain,
                    table="sis.attribute_values",
                    column="value_ciphertext",
                    row_id=value_id,
                )
                values.append(
                    _value(tid, sid, key, "aadhaar_as_printed", id=value_id, ct=blob, kv=version)
                )
            if rng.random() < 0.3:
                udise = name.canonical if rng.random() < 0.7 else name.initials_form
                values.append(
                    _value(
                        tid, sid, "full_name", "udise_plus", txt=udise, norm=comparison_key(udise)
                    )
                )
            enrolments.append(
                {
                    "id": uuid.uuid4(),
                    "t": tid,
                    "s": sid,
                    "sec": sections[i % 3],
                    "y": school.ids["year"],
                }
            )
    with admin_engine.begin() as c:
        c.execute(
            text("INSERT INTO sis.students (id, tenant_id, admission_no) VALUES (:id, :t, :adm)"),
            students_rows,
        )
        c.execute(
            text(
                "INSERT INTO sis.attribute_values (id, tenant_id, student_id, attribute_key, "
                "source, value_text, value_date, value_norm, value_ciphertext, key_version, "
                "verification_status, recorded_by) VALUES (:id, :t, :s, :k, :src, :txt, "
                "CAST(:dt AS date), :norm, :ct, :kv, 'unverified', :by)"
            ),
            [v | {"by": owner.user_id} for v in values],
        )
        c.execute(
            text(
                "INSERT INTO sis.enrollments (id, tenant_id, student_id, section_id, "
                "academic_year_id) VALUES (:id, :t, :s, :sec, :y)"
            ),
            enrolments,
        )
        for table in ("sis.students", "sis.attribute_values", "sis.enrollments"):
            c.execute(text(f"ANALYZE {table}"))
    return school


def test_NFR_PERF_005_two_thousand_students_well_under_two_minutes(big: Any) -> None:
    ctx = SW.ctx_for(big.tenant_id, big.people["owner"], "office_admin")

    def run() -> tuple[float, Any]:
        started = time.monotonic()
        with tenant_session(big.tenant_id, ctx.user_id) as db:
            out = dq.run_checks(db, ctx, profile_key="cisce-registration-2026")
        return time.monotonic() - started, out

    first_s, first = run()
    assert first.stats["students"] == N
    assert first.stats["new"] > N  # every student has at least the unverified-identity findings
    assert first.stats["by_severity"]["blocker"] > 0
    second_s, second = run()
    assert second.stats["new"] == 0
    assert second.stats["unchanged"] == first.stats["findings"]
    print(f"dq 2000 students: first {first_s:.1f}s, re-run {second_s:.1f}s")  # noqa: T201
    assert first_s < TARGET_S, first_s
    assert second_s < TARGET_S, second_s
    assert max(first_s, second_s) < BUDGET_S
