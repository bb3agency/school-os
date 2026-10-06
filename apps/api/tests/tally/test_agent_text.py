"""Text from the Tally agent is cleaned before it is stored (audit 2026-10-06 R-08; invariant 4,
OWASP API10 unsafe consumption of third-party data).

Ledger, group and company names come from the school's Tally data, which people type freely: an
accountant may put an Aadhaar number in a ledger name ("Ravi K 2345 …"). The sync stored and
showed it in clear on the party screens and in the full export. Names also carried control and
bidi-override characters that can make one ledger look like another on the mapping screen. Agent
text is now NFC-normalised, stripped of control and bidi format characters, and any Aadhaar-like
number is masked. A sync is never refused for it (one ledger must not stop the whole sync).
"""

from __future__ import annotations

from app.core.redaction import contains_full_aadhaar, verhoeff_check_digit
from app.tally.schemas import CatalogIn, PartyIn, SyncIn

BODY = "23456789012"
AADHAAR_LIKE = BODY + verhoeff_check_digit(BODY)  # synthetic, never a real number
SPACED = f"{AADHAAR_LIKE[:4]} {AADHAAR_LIKE[4:8]} {AADHAAR_LIKE[8:]}"


def _party(name: str, group: str = "Sundry Debtors") -> PartyIn:
    return PartyIn.model_validate(
        {"guid": "abc-1", "name": name, "group": group, "closing_balance": "10.00"}
    )


def test_R_08_aadhaar_numbers_in_agent_names_are_masked() -> None:
    for raw in (AADHAAR_LIKE, SPACED):
        party = _party(f"Ravi K {raw}", group=f"Class 9 {raw}")
        assert not contains_full_aadhaar(party.name)
        assert not contains_full_aadhaar(party.group)
        assert party.name.startswith("Ravi K ")
    catalog = CatalogIn.model_validate({"company": f"School {SPACED}", "groups": []})
    assert not contains_full_aadhaar(catalog.company)


def test_R_08_control_and_bidi_characters_are_removed_from_agent_names() -> None:
    party = _party("Ravi\u202e K\x07\u2066 Kumar\x00")
    assert party.name == "Ravi K Kumar"
    sync = SyncIn.model_validate(
        {
            "batch_id": "00000000-0000-7000-8000-000000000001",
            "company": "Model\x1b School",
            "as_of": "2026-10-01",
            "groups": ["Debtors\u200f"],
            "parties": [],
        }
    )
    assert (sync.company, sync.groups) == ("Model School", ["Debtors"])


def test_R_08_ordinary_names_are_unchanged() -> None:
    party = _party("  Lakshmi   Devi (Class 9A) ")
    assert party.name == "Lakshmi   Devi (Class 9A)"
