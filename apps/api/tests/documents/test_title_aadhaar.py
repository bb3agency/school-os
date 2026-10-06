"""A document's title and issuer never hold a full Aadhaar number (audit 2026-10-06 R-09;
CLAUDE.md invariant 4, PRV-015).

Titles and issuers are listed to every reader of the document and copied into the full export.
Other free text (exam names, notes, certificate inputs, notices, memories) already refused a
Verhoeff-valid 12-digit number; document titles and issuers did not.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.redaction import verhoeff_check_digit
from app.documents.schemas import DocumentUpdate, UploadCreate

BODY = "34567890123"
AADHAAR_LIKE = BODY + verhoeff_check_digit(BODY)  # synthetic
SPACED = f"{AADHAAR_LIKE[:4]} {AADHAAR_LIKE[4:8]} {AADHAAR_LIKE[8:]}"


@pytest.mark.parametrize("field", ["title", "issuer"])
@pytest.mark.parametrize("number", [AADHAAR_LIKE, SPACED])
def test_R_09_title_and_issuer_refuse_a_full_aadhaar_number(field: str, number: str) -> None:
    with pytest.raises(ValidationError) as err:
        DocumentUpdate.model_validate({field: f"Aadhaar card {number}"})
    assert "aadhaar" in str(err.value).lower()


def test_R_09_upload_file_names_refuse_a_full_aadhaar_number() -> None:
    base = {"content_type": "application/pdf", "size_bytes": 10, "purpose": "evidence"}
    assert UploadCreate.model_validate({**base, "filename": "card.pdf"}).filename == "card.pdf"
    with pytest.raises(ValidationError):
        UploadCreate.model_validate({**base, "filename": f"{AADHAAR_LIKE}.pdf"})


def test_R_09_ordinary_titles_pass() -> None:
    assert DocumentUpdate.model_validate({"title": "Aadhaar card (last 4: 0123)"}).title
