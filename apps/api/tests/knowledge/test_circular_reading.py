"""Circular reading and notice drafting, knowledge side (M4; docs/06 §4.10, §10.4-10.5).

Offline and deterministic: the real gateway (redaction, budget, metering, schema check) over the
offline fake provider, plus the server-side validation that keeps only what the passages say
(FR-CIR-002, FR-CIR-003, FR-NOTICE-001..004, invariants 4, 5, 8, 9, 13).

English first (ADR-0036): the tests written for the bilingual reading and notice run with the
Telugu switch ON explicitly (``telugu=True``, the v1 prompts and full schemas) and keep their
assertions; the ``english_first`` tests pin the default (switch off): nothing the model writes
in Telugu script is kept.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.authz.kv import InMemoryKV
from app.core.redaction import verhoeff_check_digit
from app.knowledge.circulars import notice as notice_rules
from app.knowledge.circulars import reading as reading_rules
from app.knowledge.circulars.dates import find_dates, mentions_date, parse_iso
from app.knowledge.config.circulars import load_circulars_config
from app.knowledge.config.llm import load_llm_config
from app.knowledge.config.tools import load_tools_config
from app.knowledge.domain import Metering
from app.knowledge.gateway.budget import (
    BudgetGuard,
    InMemorySpendLedger,
    StaticAiPolicy,
    TenantAiSettings,
)
from app.knowledge.gateway.fake import FakeTransport
from app.knowledge.gateway.fake_language import ENGLISH_ONLY
from app.knowledge.gateway.gateway import Gateway
from app.knowledge.gateway.metering import RecordingSink
from app.knowledge.gateway.schema_check import validate
from app.knowledge.prompts import registry

TENANT = uuid.UUID("01920000-0000-7000-8000-00000000c001")
CFG = load_circulars_config()
SRC = "sos://doc/01920000-0000-7000-8000-00000000d001/v1#p1"

EN_PASSAGES = (
    "Office of the District Educational Officer, Guntur. Rc.No.123/B/2026 Date: 01/10/2026. "
    "Sub: Collection of UDISE+ data. Ref: Circular dated 12/08/2026.",
    "All Headmasters are requested to submit the UDISE+ data sheets on or before 15/10/2026. "
    "The science exhibition will be conducted on 5th November 2026 at the district centre.",
)
TE_PASSAGES = (
    "జిల్లా విద్యాశాఖ అధికారి కార్యాలయం, గుంటూరు. తేదీ: 02/10/2026.",
    "పదవ తరగతి విద్యార్థుల నామినల్ రోల్స్ 20 అక్టోబర్ 2026 లోగా సమర్పించాలి.",
)


def passages(*texts: str) -> tuple[reading_rules.Passage, ...]:
    return tuple(
        reading_rules.Passage(number=i, chunk_no=i - 1, page=1, source=SRC, text=t)
        for i, t in enumerate(texts, start=1)
    )


def request(*texts: str) -> reading_rules.ReadingRequest:
    context = reading_rules.CircularContext(title="Synthetic circular", issuer=None, issued_on=None)
    return reading_rules.build_request(context, passages(*texts), CFG.reading)


def gateway(transport: Any = None, *, ai_enabled: bool = True) -> tuple[Gateway, RecordingSink]:
    config = load_llm_config()
    guard = BudgetGuard(
        config,
        StaticAiPolicy(TenantAiSettings(ai_enabled, Decimal(5000))),
        InMemorySpendLedger(),
        InMemoryKV(),
    )
    sink = RecordingSink()
    gw = Gateway(
        config=config,
        tools_config=load_tools_config(),
        transport=transport or FakeTransport(record=True),
        guard=guard,
        sink=sink,
        enabled=lambda: True,
    )
    return gw, sink


# --- dates (FR-CIR-003) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Submit by 15/10/2026.", (15, 10, 2026)),
        ("on or before 15-10-2026", (15, 10, 2026)),
        ("Dt: 01.10.2026", (1, 10, 2026)),
        ("by 12-11-26", (12, 11, 2026)),
        ("on 5th November 2026", (5, 11, 2026)),
        ("October 15, 2026", (15, 10, 2026)),
        ("2026-10-15", (15, 10, 2026)),
        ("20 అక్టోబర్ 2026 లోగా", (20, 10, 2026)),
        ("అక్టోబర్ 20న", (20, 10, None)),
        ("15వ తేదీ నవంబర్", (15, 11, None)),
    ],
)
def test_FR_CIR_003_dates_are_found_in_every_written_form(
    text: str, expected: tuple[int, int, int | None]
) -> None:
    found = find_dates(text)
    assert [(m.day, m.month, m.year) for m in found] == [expected]


@pytest.mark.parametrize("text", ["Class 10 2026", "Rs. 1500", "31/02/2026", "Room 12/3"])
def test_FR_CIR_003_numbers_that_are_not_dates_are_ignored(text: str) -> None:
    assert find_dates(text) == []


def test_FR_CIR_003_mentions_date_needs_the_same_day_month_and_year() -> None:
    assert mentions_date("by 15/10/2026", dt.date(2026, 10, 15))
    assert not mentions_date("by 15/10/2026", dt.date(2026, 10, 16))
    assert not mentions_date("by 15/10/2026", dt.date(2027, 10, 15))
    assert mentions_date("అక్టోబర్ 15న", dt.date(2027, 10, 15))  # no year written: any year
    assert parse_iso("2026-10-15") == dt.date(2026, 10, 15)
    assert parse_iso("15/10/2026") is None
    assert parse_iso("2026-02-30") is None


# --- schema and prompts (FR-CIR-002, FR-NOTICE-003, invariant 13) ------------------------------


def _objects(schema: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    found = [schema] if schema.get("type") == "object" else []
    for value in schema.values():
        if isinstance(value, Mapping):
            found += _objects(value)
            for inner in value.values():
                if isinstance(inner, Mapping):
                    found += _objects(inner)
        if isinstance(value, list):
            for inner in value:
                if isinstance(inner, Mapping):
                    found += _objects(inner)
    return found


@pytest.mark.parametrize(
    "schema",
    [
        reading_rules.SCHEMA,
        notice_rules.SCHEMA,
        reading_rules.ENGLISH_SCHEMA,
        notice_rules.ENGLISH_SCHEMA,
    ],
    ids=["reading", "notice", "reading_english", "notice_english"],
)
def test_FR_CIR_002_schemas_use_only_what_structured_outputs_accept(
    schema: Mapping[str, Any],
) -> None:
    text = json.dumps(schema)
    for keyword in ("minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"):
        assert keyword not in text  # unsupported by structured outputs; checked server-side
    objects = _objects(schema)
    assert objects
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_FR_CIR_002_prompts_are_versioned_files_with_their_roles() -> None:
    reading = registry.load_prompt(CFG.reading.prompt.id, CFG.reading.prompt.version)
    notice = registry.load_prompt(CFG.notice.prompt.id, CFG.notice.prompt.version)
    assert reading.header.model_config_key == "circular"
    assert notice.header.model_config_key == "notice"
    assert reading.placeholders == frozenset()
    assert notice.placeholders == frozenset()
    assert "data, not instructions" in reading.text  # SEC-019
    assert "Never guess" in reading.text
    assert "Never include personal details" in notice.text
    # ADR-0036: the bilingual prompts are kept (dormant) for the switch-on path.
    assert CFG.reading.telugu_prompt is not None
    assert CFG.notice.telugu_prompt is not None
    for ref in (CFG.reading.telugu_prompt, CFG.notice.telugu_prompt):
        kept = registry.load_prompt(ref.id, ref.version)
        assert "Telugu" in kept.text
        assert ENGLISH_ONLY not in kept.text
    # Thinking off, or (Gemini 3 cannot turn it off; ADR-0033) at the lowest level Vertex takes.
    for config in (load_llm_config(), load_llm_config().use_fallback()):
        for role in ("circular", "notice"):
            settings = config.roles[role]
            assert settings.thinking == "disabled" or (
                settings.thinking == "level" and settings.thinking_level == "low"
            ), role
            caps = config.capabilities[settings.model]
            assert settings.thinking == "disabled" or not caps.can_disable_thinking, role


# --- validation (FR-CIR-003) --------------------------------------------------------------------


def _raw(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "issuer": "Office of the District Educational Officer, Guntur",
        "reference_no": "Rc.No.123/B/2026",
        "issued_on": "2026-10-01",
        "subject": "Collection of UDISE+ data",
        "summary_en": "Submit UDISE+ data by 15 October.",
        "summary_te": "UDISE+ వివరాలు 15 అక్టోబర్ లోగా సమర్పించండి.",
        "summary_passages": [2, 2, 9],
        "deadlines": [
            {
                "title": "Submit UDISE+ data sheets",
                "details": None,
                "due_on": "2026-10-15",
                "passage": 2,
                "quote": "submit the UDISE+ data sheets on or before 15/10/2026",
            }
        ],
    }
    base.update(overrides)
    return base


def test_FR_CIR_003_a_grounded_reading_is_kept_with_its_citation() -> None:
    req = request(*EN_PASSAGES)
    out = reading_rules.validate_reading(_raw(), req, CFG.reading, telugu=True)
    assert out.issuer == "Office of the District Educational Officer, Guntur"
    assert out.reference_no == "Rc.No.123/B/2026"
    assert out.issued_on == dt.date(2026, 10, 1)
    assert out.subject == "Collection of UDISE+ data"
    assert [(d.due_on, d.citation.passage, d.citation.source) for d in out.deadlines] == [
        (dt.date(2026, 10, 15), 2, SRC)
    ]
    assert [c.passage for c in out.summary_sources] == [2]  # duplicates and unknown dropped
    assert out.dropped == 0


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"due_on": "2026-10-16"}, "date not written in the quote"),
        ({"quote": "submit everything on or before 16/10/2026"}, "quote not in the passage"),
        ({"passage": 7}, "no such passage"),
        ({"passage": 1}, "quote is in another passage"),
        ({"due_on": "15/10/2026"}, "not an ISO date"),
        ({"title": ""}, "no title"),
    ],
)
def test_FR_CIR_003_ungrounded_deadlines_are_dropped(change: dict[str, Any], why: str) -> None:
    deadline = {**_raw()["deadlines"][0], **change}
    out = reading_rules.validate_reading(
        _raw(deadlines=[deadline]), request(*EN_PASSAGES), CFG.reading, telugu=True
    )
    assert out.deadlines == (), why
    assert out.dropped == 1


def test_FR_CIR_003_metadata_not_in_the_passages_is_left_empty() -> None:
    raw = _raw(
        issuer="Commissioner of School Education",
        reference_no="Rc.No.999/2026",
        issued_on="2026-09-30",
        subject="Something else",
        summary_te="Only English here",
        summary_en="తెలుగు మాత్రమే",
    )
    out = reading_rules.validate_reading(raw, request(*EN_PASSAGES), CFG.reading, telugu=True)
    assert (out.issuer, out.reference_no, out.issued_on, out.subject) == (None, None, None, None)
    assert (out.summary_en, out.summary_te) == (None, None)


def test_FR_CIR_003_duplicates_and_extra_deadlines_are_dropped() -> None:
    one = _raw()["deadlines"][0]
    many = [one] * (CFG.reading.max_deadlines + 3)
    out = reading_rules.validate_reading(
        _raw(deadlines=many), request(*EN_PASSAGES), CFG.reading, telugu=True
    )
    assert len(out.deadlines) == 1
    assert out.dropped == CFG.reading.max_deadlines + 2


def test_FR_CIR_002_long_circulars_are_cut_to_the_configured_limits() -> None:
    texts = [f"Paragraph {i} " + "word " * 400 for i in range(80)]
    req = request(*texts)
    assert len(req.passages) < len(texts)
    assert req.passages_total == 80
    assert len(req.text) <= CFG.reading.max_input_chars
    assert [p.number for p in req.passages] == list(range(1, len(req.passages) + 1))


def test_invariant_4_aadhaar_numbers_never_reach_the_request() -> None:
    body = "12345678901"
    aadhaar = body + verhoeff_check_digit(body)
    req = request(f"Aadhaar {aadhaar} of the officer must not travel. Submit by 15/10/2026.")
    assert aadhaar not in req.text


# --- the gateway round trip with the offline fake (FR-CIR-002, FR-KB-009) -----------------------


def _read(gw: Gateway, req: reading_rules.ReadingRequest) -> reading_rules.CircularReading:
    prompt = registry.load_prompt("circular_reading", 1)
    raw = gw.generate_json(
        Metering(tenant_id=TENANT, feature="circulars"),
        "circular",
        prompt.render(),
        req.text,
        reading_rules.SCHEMA,
    )
    validate(raw, reading_rules.SCHEMA)
    return reading_rules.validate_reading(raw, req, CFG.reading, telugu=True)


def test_FR_CIR_002_english_circular_through_the_gateway() -> None:
    gw, sink = gateway()
    with capture_logs() as logs:
        out = _read(gw, request(*EN_PASSAGES))
    assert [d.due_on for d in out.deadlines] == [dt.date(2026, 10, 15), dt.date(2026, 11, 5)]
    assert out.reference_no == "Rc.No.123/B/2026"
    assert out.issued_on == dt.date(2026, 10, 1)
    assert out.subject == "Collection of UDISE+ data"
    assert out.summary_te is not None
    (event,) = sink.events
    assert (event.feature, event.role, event.outcome) == ("circulars", "circular", "ok")
    text = json.dumps(logs, default=str, ensure_ascii=False)
    assert "UDISE" not in text  # invariant 5
    assert "Headmasters" not in text


def test_FR_CIR_002_telugu_circular_through_the_gateway() -> None:
    gw, _sink = gateway()
    out = _read(gw, request(*TE_PASSAGES))
    assert [d.due_on for d in out.deadlines] == [dt.date(2026, 10, 20)]
    assert out.deadlines[0].citation.passage == 2
    assert out.issued_on == dt.date(2026, 10, 2)


def test_FR_CIR_005_ai_switched_off_raises_a_coded_error() -> None:
    from app.knowledge.gateway.errors import AiDisabled

    gw, sink = gateway(ai_enabled=False)
    with pytest.raises(AiDisabled):
        _read(gw, request(*EN_PASSAGES))
    assert sink.events == []


# --- notices (FR-NOTICE-001..004) ----------------------------------------------------------------


def test_FR_NOTICE_002_personal_numbers_are_detected() -> None:
    body = "12345678901"
    assert notice_rules.has_personal_numbers("Call 9876543210 for details")
    assert notice_rules.has_personal_numbers("Write to office@example.org")
    assert notice_rules.has_personal_numbers(f"Aadhaar {body}{verhoeff_check_digit(body)}")
    assert not notice_rules.has_personal_numbers("Sports day on 14/11/2026 at 9:00. Fee Rs. 200.")


def test_FR_NOTICE_001_circular_request_carries_only_passages_and_confirmed_dates() -> None:
    source = notice_rules.NoticeSource(
        kind="circular",
        title="Sports day",
        passages=passages("Sports day will be held on 14/11/2026 at the school ground."),
        deadlines=(notice_rules.ConfirmedDeadline(dt.date(2026, 11, 14), "Sports day"),),
    )
    text = notice_rules.build_request(source, CFG.notice)
    assert "14/11/2026: Sports day" in text
    assert "[1] Sports day will be held" in text


def test_FR_NOTICE_003_draft_through_the_gateway_is_bilingual_and_redacted() -> None:
    gw, sink = gateway()
    source = notice_rules.NoticeSource(
        kind="circular",
        title="Sports day",
        passages=passages("Sports day will be held on 14/11/2026 at the school ground."),
        deadlines=(notice_rules.ConfirmedDeadline(dt.date(2026, 11, 14), "Sports day"),),
    )
    prompt = registry.load_prompt("parent_notice", 1)
    raw = gw.generate_json(
        Metering(tenant_id=TENANT, feature="notices"),
        "notice",
        prompt.render(),
        notice_rules.build_request(source, CFG.notice),
        notice_rules.SCHEMA,
    )
    draft = notice_rules.validate_notice(raw, CFG.notice, telugu=True)
    assert "14/11/2026" in draft.body_en
    assert draft.title_te
    assert draft.body_te
    assert sink.events[0].feature == "notices"
    leaky = notice_rules.validate_notice(
        {
            "title_en": "Notice",
            "body_en": "Call 9876543210.",
            "title_te": "Only English",
            "body_te": "ఫోన్ 9876543210",
        },
        CFG.notice,
        telugu=True,
    )
    assert "9876543210" not in leaky.body_en + leaky.body_te
    assert leaky.title_te == ""  # Telugu fields must be Telugu script


@pytest.mark.parametrize(
    "value",
    [
        "A" * 300,  # one long word: no space to cut at
        "Sports" + "-" * 250,
        "Word " + "B" * 300,  # the only space comes too early to help
        ("Sports day " * 30).strip(),
    ],
    ids=["no_space", "hyphens", "one_early_space", "words"],
)
def test_FR_NOTICE_003_cut_values_fit_the_configured_limits(value: str) -> None:
    """A cut value keeps its "…" within the limit, so an AI title always passes the notice
    PATCH validation (``max_length=120``) and a reading keeps within its own limits."""
    limits = CFG.notice
    draft = notice_rules.validate_notice(
        {"title_en": value, "body_en": value * 10, "title_te": "క" * 300, "body_te": "ఆ" * 3000},
        limits,
        telugu=True,
    )
    for text, limit in (
        (draft.title_en, limits.max_title_chars),
        (draft.title_te, limits.max_title_chars),
        (draft.body_en, limits.max_body_chars),
        (draft.body_te, limits.max_body_chars),
    ):
        assert 0 < len(text) <= limit
        assert text.endswith("…")
    for limit in (120, CFG.reading.max_title_chars):
        cut = reading_rules._cut(value, limit)
        assert len(cut) <= limit
        assert cut.endswith("…")


# --- English first (ADR-0036): the default, with SOS_TELUGU_ENABLED off -------------------------

TELUGU_SCRIPT = re.compile(r"[ఀ-౿]")


def _english_read(gw: Gateway, req: reading_rules.ReadingRequest) -> reading_rules.CircularReading:
    ref = CFG.reading.prompt_for(False)
    raw = gw.generate_json(
        Metering(tenant_id=TENANT, feature="circulars"),
        "circular",
        registry.load_prompt(ref.id, ref.version).render(),
        req.text,
        reading_rules.schema_for(False),
    )
    validate(raw, reading_rules.ENGLISH_SCHEMA)
    return reading_rules.validate_reading(raw, req, CFG.reading, telugu=False)


def _shown(out: reading_rules.CircularReading) -> list[str]:
    """Everything of a reading a person sees that the model wrote (not the quotes)."""
    values = [out.issuer, out.reference_no, out.subject, out.summary_en, out.summary_te]
    for d in out.deadlines:
        values += [d.title, d.details]
    return [v for v in values if v]


def test_english_first_prompts_are_english_only_by_default() -> None:
    assert (CFG.reading.prompt.id, CFG.reading.prompt.version) == ("circular_reading", 2)
    assert (CFG.notice.prompt.id, CFG.notice.prompt.version) == ("parent_notice", 2)
    for ref in (CFG.reading.prompt_for(False), CFG.notice.prompt_for(False)):
        text = registry.load_prompt(ref.id, ref.version).text
        assert ENGLISH_ONLY in text
        assert "summary_te" not in text
        assert "title_te" not in text
    assert CFG.reading.prompt_for(True) == CFG.reading.telugu_prompt
    assert "summary_te" not in reading_rules.ENGLISH_SCHEMA["properties"]
    assert set(notice_rules.ENGLISH_SCHEMA["properties"]) == {"title_en", "body_en"}


def test_english_first_validation_keeps_no_telugu_the_model_wrote() -> None:
    raw = _raw(
        subject="UDISE+ వివరాల సేకరణ",
        summary_en="Submit UDISE+ data by 15 October.",
        deadlines=[
            {
                "title": "UDISE+ వివరాలు సమర్పించండి",
                "details": "ప్రధానోపాధ్యాయులు",
                "due_on": "2026-10-15",
                "passage": 2,
                "quote": "submit the UDISE+ data sheets on or before 15/10/2026",
            }
        ],
    )
    te_req = request(EN_PASSAGES[0] + " విషయం: UDISE+ వివరాల సేకరణ", EN_PASSAGES[1])
    out = reading_rules.validate_reading(raw, te_req, CFG.reading, telugu=False)
    assert out.summary_te is None  # a valid Telugu summary is not kept while Telugu is hidden
    assert out.summary_en == "Submit UDISE+ data by 15 October."
    assert out.subject is None  # grounded, but written in Telugu script
    (deadline,) = out.deadlines  # the deadline itself is never lost
    assert deadline.title == CFG.reading.english_title_fallback
    assert deadline.details is None
    assert deadline.citation.quote  # the quote is the circular's own words (evidence)
    assert not any(TELUGU_SCRIPT.search(v) for v in _shown(out))
    # With the switch on the same reading keeps the Telugu subject and title.
    on = reading_rules.validate_reading(raw, te_req, CFG.reading, telugu=True)
    assert on.subject == "UDISE+ వివరాల సేకరణ"
    assert on.deadlines[0].title == "UDISE+ వివరాలు సమర్పించండి"


@pytest.mark.parametrize("texts", [EN_PASSAGES, TE_PASSAGES], ids=["en", "te"])
def test_english_first_circular_through_the_gateway_is_english_only(
    texts: tuple[str, ...],
) -> None:
    gw, _sink = gateway()
    out = _english_read(gw, request(*texts))
    assert out.deadlines  # the Telugu circular's deadline is still found
    assert out.summary_en
    assert out.summary_te is None
    assert not any(TELUGU_SCRIPT.search(v) for v in _shown(out))


def test_english_first_notice_is_english_only() -> None:
    gw, _sink = gateway()
    ref = CFG.notice.prompt_for(False)
    source = notice_rules.NoticeSource(
        kind="circular",
        title="క్రీడా దినోత్సవం",
        passages=passages("క్రీడా దినోత్సవం 14/11/2026న పాఠశాల మైదానంలో జరుగుతుంది."),
        deadlines=(notice_rules.ConfirmedDeadline(dt.date(2026, 11, 14), "Sports day"),),
    )
    raw = gw.generate_json(
        Metering(tenant_id=TENANT, feature="notices"),
        "notice",
        registry.load_prompt(ref.id, ref.version).render(),
        notice_rules.build_request(source, CFG.notice),
        notice_rules.schema_for(False),
    )
    validate(raw, notice_rules.ENGLISH_SCHEMA)
    draft = notice_rules.validate_notice(raw, CFG.notice, telugu=False)
    assert "14/11/2026" in draft.body_en
    assert (draft.title_te, draft.body_te) == ("", "")
    assert not TELUGU_SCRIPT.search(draft.title_en + draft.body_en)
    # A model that writes Telugu anyway: nothing of it is kept.
    leaky = notice_rules.validate_notice(
        {
            "title_en": "తల్లిదండ్రులకు సూచన",
            "body_en": "Sports day on 14/11/2026.",
            "title_te": "తల్లిదండ్రులకు సూచన",
            "body_te": "క్రీడా దినోత్సవం 14/11/2026న.",
        },
        CFG.notice,
        telugu=False,
    )
    assert (leaky.title_en, leaky.body_en, leaky.title_te, leaky.body_te) == (
        "",
        "Sports day on 14/11/2026.",
        "",
        "",
    )
