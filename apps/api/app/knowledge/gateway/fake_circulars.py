"""Offline stand-ins for circular reading and notice drafting (``SOS_KB_PROVIDER_MODE=fake``).

Deterministic rules, no model, so local development and CI show realistic suggestions and the
eval harness can measure the application's validation (docs/06 §13): the numbers it produces
measure these rules plus the server-side checks, never Claude.

Circular reading (schema ``sos:circular_reading.v1``): each passage is split into sentences; a
sentence that writes a date and says what to do (English, Telugu or Latin-script Telugu action
words) becomes one deadline per date, quoting the sentence. Header lines (``Date:``, ``Ref``,
``Rc.No.``, ``తేదీ:``) and references to earlier letters ("dated", "vide") are skipped; the
reference number, the circular's date, its issuer and ``Sub:`` line fill the metadata.

Notice drafting (schema ``sos:parent_notice.v1``): a fixed bilingual template listing the dates
the school confirmed (or the first sentences of staff text).

English first (ADR-0036): a schema without the Telugu fields (``summary_te``, ``title_te``) is
the English-only request: then nothing Telugu is written (no Telugu summary or notice, a Telugu
deadline sentence gets an English title, Telugu metadata is left empty), as the prompt asks.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from typing import Any, Final

from app.knowledge.circulars import notice, reading
from app.knowledge.circulars.dates import DateMention, find_dates
from app.knowledge.gateway.fake_language import has_telugu

_PASSAGE: Final = re.compile(r"^\[(?P<n>\d+)\](?: \(page \d+\))? (?P<text>.*)$")
_SENTENCE_END: Final = re.compile(r"(?<=[.!?।])\s+(?=\S)")
_HEADER: Final = re.compile(
    r"^\s*(?:date|dt|dated|ref|reference|rc\.?\s*no|lr\.?\s*no|proc\.?\s*no|memo\.?\s*no"
    r"|తేదీ|సూచిక)(?=[\s.:]|$)[\s.:]*",
    re.IGNORECASE,
)
_EARLIER_LETTER: Final = re.compile(r"\b(?:dated|vide)\b|తేదీన జారీ|ద్వారా జారీ", re.IGNORECASE)
_ACTION: Final = re.compile(
    r"\b(?:by|before|on or before|last date|deadline|due|submit\w*|send|sent|report\w*|attend\w*"
    r"|conduct\w*|held|hold|pay\w*|upload\w*|prepare\w*|organi[sz]e\w*|complete\w*|scheduled"
    r"|lopu|loga|lopala|chivari|gaduvu|pampali|pampandi|cheyali|nirvahinch\w*|hajaru\w*)\b"
    r"|లోగా|లోపు|ముందు|చివరి తేదీ|గడువు|సమర్పించ|పంపించ|పంపాల|నిర్వహించ|హాజరు|చెల్లించ|జరుగు",
    re.IGNORECASE,
)
_REFERENCE: Final = re.compile(
    r"(?:Rc|Ref|Lr|Proc|Memo)\.?\s*No\.?\s*:?\s*[A-Za-z0-9][A-Za-z0-9/.\-]*[A-Za-z0-9]",
    re.IGNORECASE,
)
_SUBJECT: Final = re.compile(r"^\s*(?:sub(?:ject)?|విషయం)\s*[:.\-]\s*(?P<s>.+)$", re.IGNORECASE)
_ISSUER: Final = re.compile(
    r"officer|office of|commissioner|director|board of|secretary|correspondent|అధికారి|కార్యాలయ",
    re.IGNORECASE,
)
_TELUGU_SCRIPT: Final = re.compile(r"[ఀ-౿]")
_MAX_TITLE: Final = 120


def _passages(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for line in text.splitlines():
        m = _PASSAGE.match(line)
        if m:
            out.append((int(m["n"]), m["text"]))
    return out


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _year_hint(passages: list[tuple[int, str]]) -> int | None:
    for _, text in passages:
        for mention in find_dates(text):
            if mention.year is not None:
                return mention.year
    return None


def _as_date(mention: DateMention, year: int | None) -> dt.date | None:
    chosen = mention.year or year
    if chosen is None:
        return None
    try:
        return dt.date(chosen, mention.month, mention.day)
    except ValueError:
        return None


def _title(sentence: str) -> str:
    title = sentence.rstrip(".!? ")
    return title if len(title) <= _MAX_TITLE else title[:_MAX_TITLE].rsplit(" ", 1)[0]


def _english(reply: dict[str, Any]) -> dict[str, Any]:
    """The reply to the English-only prompt: no Telugu summary, Telugu metadata left empty,
    an English title for a deadline written in Telugu (the quote stays as written)."""
    out = {k: v for k, v in reply.items() if k != "summary_te"}
    for key in ("issuer", "reference_no", "subject"):
        if out[key] is not None and has_telugu(out[key]):
            out[key] = None
    out["deadlines"] = [
        {**d, "title": f"Action needed by {dt.date.fromisoformat(d['due_on']):%d/%m/%Y}"}
        if has_telugu(d["title"])
        else d
        for d in reply["deadlines"]
    ]
    return out


def circular_reading(request_text: str, *, english: bool = False) -> dict[str, Any]:
    passages = _passages(request_text)
    year = _year_hint(passages)
    deadlines: list[dict[str, Any]] = []
    reference = issued = issuer = subject = None
    for number, text in passages:
        for sentence in _sentences(text):
            if reference is None and (ref := _REFERENCE.search(sentence)):
                reference = ref.group(0)
            if subject is None and (sub := _SUBJECT.match(sentence)):
                subject = sub["s"].strip().rstrip(".")
            if issuer is None and _ISSUER.search(sentence) and len(sentence) <= 160:
                issuer = sentence.rstrip(".,")
            mentions = find_dates(sentence)
            if _HEADER.match(sentence):
                if issued is None and mentions:
                    when = _as_date(mentions[0], year)
                    issued = when.isoformat() if when else None
                continue
            if not mentions or _EARLIER_LETTER.search(sentence) or not _ACTION.search(sentence):
                continue
            for mention in mentions:
                when = _as_date(mention, year)
                if when is not None:
                    deadlines.append(
                        {
                            "title": _title(sentence),
                            "details": None,
                            "due_on": when.isoformat(),
                            "passage": number,
                            "quote": sentence,
                        }
                    )
    topic = subject or (passages[0][1][:80] if passages else "")
    summary_en = (
        f"This circular is about: {topic}."
        if topic and not _TELUGU_SCRIPT.search(topic)
        else ("This circular is written in Telugu. Open it to read the details.")
    )
    reply: dict[str, Any] = {
        "issuer": issuer,
        "reference_no": reference,
        "issued_on": issued,
        "subject": subject,
        "summary_en": summary_en,
        "summary_te": f"ఈ సర్క్యులర్ విషయం: {topic}" if topic else "ఈ సర్క్యులర్ వివరాలు చూడండి.",
        "summary_passages": [n for n, _ in passages[:2]],
        "deadlines": deadlines,
    }
    return _english(reply) if english else reply


_CONFIRMED: Final = re.compile(r"^- (?P<date>\d{2}/\d{2}/\d{4}): (?P<title>.+)$")


def parent_notice(request_text: str, *, english_only: bool = False) -> dict[str, Any]:
    dates = [m["date"] for m in map(_CONFIRMED.match, request_text.splitlines()) if m]
    staff = request_text.split("\n\n", 1)[1] if request_text.startswith("Source: text") else ""
    if dates:
        en_points = "; ".join(dates)
        body_en = f"Dear parents, please note the following dates from the school: {en_points}."
        body_te = f"ప్రియమైన తల్లిదండ్రులకు, దయచేసి ఈ తేదీలను గమనించండి: {en_points}."
    else:
        first = " ".join(_sentences(staff)[:2]) if staff else ""
        english = first if first and not _TELUGU_SCRIPT.search(first) else ""
        body_en = (
            f"Dear parents, please note: {english}".strip()
            if english
            else ("Dear parents, please read the school's notice below.")
        )
        body_te = "ప్రియమైన తల్లిదండ్రులకు, దయచేసి పాఠశాల సూచనను గమనించండి."
    if english_only:
        return {"title_en": "Notice for parents", "body_en": body_en}
    return {
        "title_en": "Notice for parents",
        "body_en": body_en,
        "title_te": "తల్లిదండ్రులకు సూచన",
        "body_te": body_te,
    }


def structured_reply(schema: Mapping[str, Any], request_text: str) -> dict[str, Any] | None:
    """The stand-in's JSON for a known schema, or None (then a minimal schema instance)."""
    tag = schema.get("description")
    properties = schema.get("properties") or {}
    if tag == reading.SCHEMA_TAG:
        return circular_reading(request_text, english="summary_te" not in properties)
    if tag == notice.SCHEMA_TAG:
        return parent_notice(request_text, english_only="title_te" not in properties)
    return None


__all__ = ["circular_reading", "parent_notice", "structured_reply"]
