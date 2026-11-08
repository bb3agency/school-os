"""A staff member's display name and email never hold a full Aadhaar number (web audit
2026-11-08 round 2, XS-04; SEC-013, FR-STU-012; CLAUDE.md invariant 4).

Names and emails of staff are shown on every audit line, assignment and notification. The web
invite and edit forms refuse a full number; ``InviteIn`` and ``UserUpdateIn`` did not.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import install_error_handlers
from app.core.redaction import verhoeff_check_digit
from app.identity.schemas import InviteIn, UserUpdateIn

BODY = "34567890123"
AADHAAR_LIKE = BODY + verhoeff_check_digit(BODY)  # synthetic
SPACED = f"{AADHAAR_LIKE[:4]} {AADHAAR_LIKE[4:8]} {AADHAAR_LIKE[8:]}"
DOTTED = f"{AADHAAR_LIKE[:4]}.{AADHAAR_LIKE[4:8]}.{AADHAAR_LIKE[8:]}"

INVITE: dict[str, Any] = {
    "idp_subject": "lakshmi.k",
    "display_name": "Lakshmi K",
    "email": "lakshmi@school.example",
    "roles": ["clerk"],
}


def _value(field: str, number: str) -> str:
    return f"{number}@school.example" if field == "email" else f"Lakshmi {number}"


# An email cannot hold spaces (its pattern refuses those first), so it is tried without them.
@pytest.mark.parametrize(
    ("field", "number"),
    [
        ("display_name", AADHAAR_LIKE),
        ("display_name", SPACED),
        ("display_name", DOTTED),
        ("email", AADHAAR_LIKE),
        ("email", DOTTED),
    ],
)
def test_XS_04_SEC_013_invite_and_update_refuse_a_full_aadhaar_number(
    field: str, number: str
) -> None:
    for model, base in ((InviteIn, INVITE), (UserUpdateIn, {})):
        with pytest.raises(ValidationError) as err:
            model.model_validate({**base, field: _value(field, number)})
        errors = err.value.errors()
        assert [(e["loc"], e["type"]) for e in errors] == [
            ((field,), "aadhaar_full_number_rejected")
        ]


def test_XS_04_ordinary_names_and_emails_pass() -> None:
    assert InviteIn.model_validate(INVITE).display_name == "Lakshmi K"
    update = UserUpdateIn.model_validate(
        {"display_name": "Clerk 2 (last 4: 0123)", "email": "office9876543210@school.example"}
    )
    assert update.display_name == "Clerk 2 (last 4: 0123)"


def test_XS_04_the_api_answers_422_with_the_field_and_code() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/users")
    def _invite(body: InviteIn) -> dict[str, str]:  # pragma: no cover - refused before
        return {"ok": body.display_name}

    res = TestClient(app).post("/users", json={**INVITE, "display_name": f"L {SPACED}"})
    assert res.status_code == 422
    problem = res.json()
    assert problem["code"] == "validation_error"
    assert problem["errors"] == [
        {
            "field": "display_name",
            "code": "aadhaar_full_number_rejected",
            "message_key": "errors.aadhaar_full_number_rejected",
        }
    ]
    assert SPACED not in res.text
    assert AADHAAR_LIKE not in res.text
