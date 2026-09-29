"""``sos://`` source URIs (docs/06 §8): stable, parseable, and never carry names or values.

Citations are validated server-side against the sources a request's tool results provided
(FR-KB-005), so every producer (retrieval, record tools, verified answers) and the validator
must build and read exactly the same strings.
"""

from __future__ import annotations

import uuid

import pytest

from app.knowledge import sources

DOC = uuid.UUID("0192a0de-0000-7000-8000-00000000c001")
STUDENT = uuid.UUID("0192a0de-0000-7000-8000-00000000b001")
FINDING = uuid.UUID("0192a0de-0000-7000-8000-00000000f001")


def test_FR_KB_005_document_page_uri_matches_docs_06_section_8() -> None:
    uri = sources.document_page(DOC, version_no=3, page=2)
    assert uri == f"sos://doc/{DOC}/v3#p2"
    ref = sources.parse(uri)
    assert ref == sources.SourceRef(kind="doc", object_id=DOC, version_no=3, page=2)


def test_FR_KB_005_student_field_uri_round_trips() -> None:
    uri = sources.student_field(STUDENT, attribute="dob", source="admission_register")
    assert uri == f"sos://student/{STUDENT}/field/dob?src=admission_register"
    ref = sources.parse(uri)
    assert ref.kind == "student"
    assert ref.object_id == STUDENT
    assert ref.attribute == "dob"
    assert ref.source == "admission_register"


@pytest.mark.parametrize(
    ("builder", "kind"),
    [
        (sources.finding, "finding"),
        (sources.change_request, "change"),
        (sources.verified_answer, "verified"),
        (sources.fee_dues, "fee"),  # M6 get_fee_dues (ADR-0032)
    ],
)
def test_FR_KB_005_simple_uris_round_trip(builder: object, kind: str) -> None:
    uri = builder(FINDING)  # type: ignore[operator]
    assert uri == f"sos://{kind}/{FINDING}"
    assert sources.parse(uri) == sources.SourceRef(kind=kind, object_id=FINDING)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("attribute", "source"),
    [
        ("K. Venkata Sai", "admission_register"),  # a name is never a key
        ("dob", "14/03/2012"),  # a value is never a key
        ("DOB", "admission_register"),
        ("dob", ""),
        ("x" * 65, "admission_register"),
    ],
)
def test_SEC_018_uris_refuse_anything_that_is_not_a_key(attribute: str, source: str) -> None:
    with pytest.raises(ValueError, match="key"):
        sources.student_field(STUDENT, attribute=attribute, source=source)


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.test/doc",
        "sos://doc/not-a-uuid/v1#p1",
        f"sos://doc/{DOC}/v0#p1",
        f"sos://doc/{DOC}/v1#p0",
        f"sos://doc/{DOC}/v1",
        f"sos://student/{STUDENT}/field/dob",
        f"sos://student/{STUDENT}/field/Ravi?src=admission_register",
        f"sos://unknown/{DOC}",
        f"sos://finding/{DOC}?x=1",
        f" sos://finding/{DOC}",
    ],
)
def test_FR_KB_005_parse_rejects_malformed_uris(uri: str) -> None:
    with pytest.raises(ValueError, match="source URI"):
        sources.parse(uri)


def test_FR_KB_005_page_and_version_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        sources.document_page(DOC, version_no=0, page=1)
    with pytest.raises(ValueError, match="positive"):
        sources.document_page(DOC, version_no=1, page=0)
