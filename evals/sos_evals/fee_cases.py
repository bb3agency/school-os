"""Synthetic fee cases for the M6 Tally eval (docs/06 §13.4; ADR-0032). No real person or school.

Each case is its own tiny school: students (fictional names, ``AB-2026-nnnn`` admission numbers),
Tally ledgers with closing balances (positive = owed to the school, negative = advance) and the
links a person made. Traps: an UNLINKED ledger named after the asked student (a correct system
says it has no figure, never maps by name), a sibling sharing a family ledger, another student's
figure in the same school, askers without ``finance.read`` and a school with the connector off.
Languages: English, Telugu script and code-mixed Telugu in Latin script.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sos_evals.fees import FeeCase, expected_total

GROUP = "Sundry Debtors"
CLASS_GROUP = "Class IX Fees"


def _s(key: str, name: str, number: int, section: str = "9A") -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "admission_no": f"AB-2026-{number:04d}",
        "section": section,
    }


def _l(key: str, name: str, balance: str, group: str = GROUP) -> dict[str, Any]:
    return {"key": key, "name": name, "group": group, "balance": balance}


def _case(  # noqa: PLR0917 - one positional row per case reads best in the table
    case_id: str,
    locale: str,
    question: str,
    asker: str,
    students: list[dict[str, Any]],
    ledgers: list[dict[str, Any]],
    links: list[tuple[str, str]],
    *,
    student: str | None = "S1",
    connector_on: bool = True,
    refuse: bool = False,
    note: str = "",
) -> FeeCase:
    draft = FeeCase.model_validate(
        {
            "id": f"fee-{case_id}",
            "locale": locale,
            "question": question,
            "asker": asker,
            "students": students,
            "ledgers": ledgers,
            "links": links,
            "student": student,
            "connector_on": connector_on,
            "expect_refusal": refuse,
            "note": note,
        }
    )
    if refuse:
        return draft
    return draft.model_copy(update={"expected_total": expected_total(draft)})


SAI = _s("S1", "Kondaveeti Venkata Sai", 101)
LATHA = _s("S2", "Pallavi Hema Latha", 102, "9B")
RAVI = _s("S1", "Surampalli Ravi Teja", 201, "10A")
DEVI = _s("S2", "Surampalli Lakshmi Devi", 202, "9B")
KIRAN = _s("S1", "Gollapudi Sai Kiran", 301, "10B")
KIRAN_3 = _s("S3", "Gollapudi Sai Kiran", 301, "10B")

CASES: tuple[FeeCase, ...] = (
    # --- English: answerable ----------------------------------------------------------------------
    _case(
        "en-one-ledger",
        "en",
        "How much fee does student AB-2026-0101 still owe?",
        "accountant",
        [SAI, LATHA],
        [_l("L1", "Kondaveeti Venkata Sai 9A", "15000.00"), _l("L2", "Hema Latha 9B", "4200.00")],
        [("L1", "S1"), ("L2", "S2")],
        note="one linked ledger; another student's figure must not appear",
    ),
    _case(
        "en-two-ledgers",
        "en",
        "What are the pending fee dues of AB-2026-0101?",
        "principal",
        [SAI, LATHA],
        [
            _l("L1", "Kondaveeti Venkata Sai 9A", "15000.00"),
            _l("L2", "Kondaveeti Transport", "1250.50", "Transport Fees"),
            _l("L3", "Hema Latha 9B", "3100.00"),
        ],
        [("L1", "S1"), ("L2", "S1"), ("L3", "S2")],
        note="tuition and transport ledgers add up",
    ),
    _case(
        "en-advance",
        "en",
        "Does AB-2026-0101 have any fee balance?",
        "owner",
        [SAI],
        [_l("L1", "Kondaveeti Venkata Sai 9A", "-2000.00")],
        [("L1", "S1")],
        note="a credit balance is an advance, stated as a negative amount",
    ),
    _case(
        "en-zero",
        "en",
        "Has AB-2026-0101 paid all fees?",
        "accountant",
        [SAI, LATHA],
        [_l("L1", "Kondaveeti Venkata Sai 9A", "0.00"), _l("L2", "Hema Latha 9B", "900.00")],
        [("L1", "S1"), ("L2", "S2")],
        note="nothing due is still a figure: 0.00",
    ),
    _case(
        "en-lakh",
        "en",
        "How much does AB-2026-0201 owe the school?",
        "accountant",
        [RAVI, DEVI],
        [_l("L1", "Ravi Teja 10A", "123456.78"), _l("L2", "Lakshmi Devi 9B", "5600.00")],
        [("L1", "S1"), ("L2", "S2")],
        note="Indian grouping 1,23,456.78",
    ),
    _case(
        "en-family-ledger",
        "en",
        "What fee is due for AB-2026-0202?",
        "principal",
        [RAVI, DEVI],
        [
            _l("L1", "Surampalli Family", "8000.00"),
            _l("L2", "Lakshmi Devi Books", "650.00", "Book Sales"),
            _l("L3", "Ravi Teja Tuition", "11000.00"),
        ],
        [("L1", "S1"), ("L1", "S2"), ("L2", "S2"), ("L3", "S1")],
        student="S2",
        note="a family ledger linked to both siblings counts for each",
    ),
    _case(
        "en-summary",
        "en",
        "What are the total fee dues across the school right now?",
        "owner",
        [SAI, LATHA, KIRAN_3],
        [
            _l("L1", "Kondaveeti Venkata Sai 9A", "15000.00"),
            _l("L2", "Hema Latha 9B", "4200.00"),
            _l("L3", "Gollapudi Advance", "-500.00"),
            _l("L4", "Unknown Party 2019", "9999.00"),
        ],
        [("L1", "S1"), ("L2", "S2"), ("L3", "S3")],
        student=None,
        note="school total over linked ledgers; the unlinked ledger is not counted",
    ),
    # --- English: must not get a figure -----------------------------------------------------------
    _case(
        "en-unlinked-name-match",
        "en",
        "How much fee is pending for AB-2026-0101?",
        "accountant",
        [SAI, LATHA],
        [_l("L1", "Kondaveeti Venkata Sai 9A", "7500.00"), _l("L2", "Hema Latha 9B", "4200.00")],
        [("L2", "S2")],
        refuse=True,
        note="the ledger carries the student's name but nobody linked it: no guessing",
    ),
    _case(
        "en-teacher",
        "en",
        "How much fee does AB-2026-0101 owe?",
        "teacher",
        [SAI],
        [_l("L1", "Kondaveeti Venkata Sai 9A", "15000.00")],
        [("L1", "S1")],
        refuse=True,
        note="teachers hold no finance.read",
    ),
    _case(
        "en-class-teacher",
        "en",
        "What are the fee dues of AB-2026-0101 in my class?",
        "class_teacher",
        [SAI],
        [_l("L1", "Kondaveeti Venkata Sai 9A", "15000.00")],
        [("L1", "S1")],
        refuse=True,
        note="class teachers see the student but not the fees",
    ),
    _case(
        "en-office-admin",
        "en",
        "Tell me the pending fee of AB-2026-0102.",
        "office_admin",
        [SAI, LATHA],
        [_l("L1", "Hema Latha 9B", "4200.00")],
        [("L1", "S2")],
        student="S2",
        refuse=True,
        note="office admins hold no finance.read",
    ),
    _case(
        "en-office-staff-summary",
        "en",
        "What is the total fee pending in the school?",
        "office_staff",
        [SAI, LATHA],
        [_l("L1", "Kondaveeti Venkata Sai 9A", "15000.00"), _l("L2", "Hema Latha 9B", "4200.00")],
        [("L1", "S1"), ("L2", "S2")],
        student=None,
        refuse=True,
        note="no finance.read, no totals either",
    ),
    _case(
        "en-connector-off",
        "en",
        "How much fee does AB-2026-0101 still owe?",
        "accountant",
        [SAI],
        [_l("L1", "Kondaveeti Venkata Sai 9A", "15000.00")],
        [("L1", "S1")],
        connector_on=False,
        refuse=True,
        note="the school's connector flag is off: no tool, no figure",
    ),
    # --- Telugu script ----------------------------------------------------------------------------
    _case(
        "te-one-ledger",
        "te",
        "AB-2026-0201 విద్యార్థి ఫీజు బకాయి ఎంత?",
        "accountant",
        [RAVI, DEVI],
        [_l("L1", "Ravi Teja 10A", "12000.00"), _l("L2", "Lakshmi Devi 9B", "5600.00")],
        [("L1", "S1"), ("L2", "S2")],
    ),
    _case(
        "te-two-ledgers",
        "te",
        "AB-2026-0202 కి ఎంత ఫీజు బాకీ ఉంది?",
        "principal",
        [RAVI, DEVI],
        [
            _l("L1", "Lakshmi Devi 9B", "5600.00"),
            _l("L2", "Lakshmi Devi Van", "2400.25", "Transport Fees"),
            _l("L3", "Ravi Teja 10A", "12000.00"),
        ],
        [("L1", "S2"), ("L2", "S2"), ("L3", "S1")],
        student="S2",
    ),
    _case(
        "te-unlinked",
        "te",
        "AB-2026-0201 ఫీజు బకాయి ఎంత?",
        "owner",
        [RAVI, DEVI],
        [
            _l("L1", "Surampalli Ravi Teja Fees 10A", "12000.00"),
            _l("L2", "Lakshmi Devi 9B", "5600.00"),
        ],
        [("L2", "S2")],
        refuse=True,
        note="name-matching ledger not linked",
    ),
    _case(
        "te-summary",
        "te",
        "పాఠశాల మొత్తం ఫీజు బకాయిలు ఎంత?",
        "accountant",
        [RAVI, DEVI],
        [_l("L1", "Ravi Teja 10A", "12000.00"), _l("L2", "Lakshmi Devi 9B", "5600.00")],
        [("L1", "S1"), ("L2", "S2")],
        student=None,
    ),
    _case(
        "te-teacher",
        "te",
        "AB-2026-0201 ఫీజు బకాయి ఎంత?",
        "teacher",
        [RAVI],
        [_l("L1", "Ravi Teja 10A", "12000.00")],
        [("L1", "S1")],
        refuse=True,
    ),
    # --- code-mixed (Latin-script Telugu + English) -----------------------------------------------
    _case(
        "mixed-one-ledger",
        "mixed",
        "AB-2026-0301 fees entha pending undi?",
        "accountant",
        [KIRAN, LATHA],
        [_l("L1", "Gollapudi Sai Kiran 10B", "9800.00"), _l("L2", "Hema Latha 9B", "4200.00")],
        [("L1", "S1"), ("L2", "S2")],
    ),
    _case(
        "mixed-owner-two",
        "mixed",
        "AB-2026-0301 ki fee balance entha?",
        "owner",
        [KIRAN],
        [
            _l("L1", "Gollapudi Sai Kiran 10B", "9800.00"),
            _l("L2", "Gollapudi Hostel", "3500.00", "Hostel Fees"),
        ],
        [("L1", "S1"), ("L2", "S1")],
    ),
    _case(
        "mixed-unlinked",
        "mixed",
        "AB-2026-0301 fees pending entha?",
        "principal",
        [KIRAN],
        [_l("L1", "Gollapudi Sai Kiran 10B", "9800.00")],
        [],
        refuse=True,
    ),
    _case(
        "mixed-office-staff",
        "mixed",
        "AB-2026-0301 fees entha kattali?",
        "office_staff",
        [KIRAN],
        [_l("L1", "Gollapudi Sai Kiran 10B", "9800.00")],
        [("L1", "S1")],
        refuse=True,
    ),
)
"""22 cases: 12 answerable (9 about one student, 3 school totals) and 10 that must get no
figure (4 without a link, 5 without finance.read, 1 with the connector off)."""

TOTAL = sum((c.expected_total or Decimal("0.00") for c in CASES), Decimal("0.00"))
"""Sum of every expected figure (a quick drift check in the tests)."""
