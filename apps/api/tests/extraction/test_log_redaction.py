"""No personal data from register pages in logs (SEC-008, invariant 5, docs/12 §4.6).

Names, dates of birth and parent names read from a page, and the values a reviewer types when
confirming, pass through the pipeline and the API while stdout/stderr are captured.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
X = sys.modules["sos_test_extraction_support"]

NAME = "Vemulapalli Synthetica Harini"
FATHER = "Vemulapalli Syntheticus Prasad"
DOB = "2011-02-17"


def test_SEC_008_extraction_does_not_log_personal_data(
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    a = world.a
    number = X.valid_aadhaar_like(99)
    row = X.register_row(NAME, dob=DOB)
    row["father_name"] = X.cell(FATHER)
    capsys.readouterr()
    # Page 1 shows a full number in its text (its image is withheld, PRV-016); page 2 is clean
    # and its row is the one confirmed below (rows of a withheld page wait for a redacted copy).
    withheld_page = X.page_png([X.register_row(f"{NAME} Withheld", dob=DOB)], raw_text=number)
    batch_id, items = X.processed_batch(admin_engine, a, [withheld_page, X.page_png([row])])
    pages = {i: X.row_of(admin_engine, "sis.extraction_items", i)["page_id"] for i in items}
    withheld = {
        i
        for i, p in pages.items()
        if X.row_of(admin_engine, "sis.extraction_pages", p)["image_withheld"]
    }
    assert len(withheld) == 1
    items = [i for i in items if i not in withheld]
    who = a.people["office_admin"]
    api.call(who, "GET", "/api/v1/extraction-items", params={"batch_id": str(batch_id)})
    api.call(who, "GET", f"/api/v1/extraction-items/{items[0]}")
    bad = api.call(
        who,
        "POST",
        f"/api/v1/extraction-items/{items[0]}/confirm",
        json={"fields": {"full_name": f"{NAME} {number}", "dob": "17/02/2011"}},
    )
    assert bad.status_code == 422
    ok = api.call(
        who,
        "POST",
        f"/api/v1/extraction-items/{items[0]}/confirm",
        json={"fields": {"full_name": NAME, "dob": DOB, "father_name": FATHER}},
    )
    assert ok.status_code == 200, ok.text
    out, err = capsys.readouterr()
    for stream in (out, err):
        for secret in (NAME, FATHER, DOB, "Vemulapalli", number, "17/02/2011"):
            assert secret not in stream, secret
