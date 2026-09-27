"""Synthetic school documents for the M2 knowledge-base corpus (docs/12 §3: "circulars in EN/TE,
minutes, fee policy"). SYNTHETIC ONLY (CLAUDE.md invariant 11).

Pure module: :func:`build_corpus` returns small Word documents (DOCX, text only, no PDF) built
with the standard library (``zipfile``; no new dependency). The student seeder stores them
through the documents service. The files are byte-for-byte deterministic (fixed ZIP timestamps),
so re-runs and duplicate detection see the same SHA-256. Content names no person: offices and
roles only; amounts and dates are invented.
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from dataclasses import dataclass
from typing import Final, Literal
from xml.sax.saxutils import escape

DOCX_MIME: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ZIP_TIME: Final = (2026, 6, 1, 0, 0, 0)

Purpose = Literal["circular", "policy", "other"]
DocType = Literal["circular", "policy", "minutes"]
Language = Literal["en", "te", "mixed"]


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    key: str
    title: str
    purpose: Purpose
    doc_type: DocType
    language: Language
    issued_on: dt.date
    paragraphs: tuple[str, ...]

    @property
    def filename(self) -> str:
        return f"synthetic-{self.key}.docx"

    def docx(self) -> bytes:
        return build_docx(self.paragraphs)


_CONTENT_TYPES: Final = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS: Final = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
_W: Final = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def build_docx(paragraphs: tuple[str, ...]) -> bytes:
    """A minimal valid DOCX: one run per paragraph, UTF-8 (Telugu stays Telugu)."""
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>' for p in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in (
            ("[Content_Types].xml", _CONTENT_TYPES),
            ("word/document.xml", document),
            ("_rels/.rels", _RELS),
        ):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data.encode("utf-8"))
    return out.getvalue()


def build_corpus(school_name: str) -> list[CorpusDocument]:
    """Six documents per school: circulars (EN, TE, bilingual), SMC minutes, fee policy EN/TE."""
    return [
        CorpusDocument(
            key="circular-half-yearly-exams",
            title="Circular: half-yearly examinations 2026-27",
            purpose="circular",
            doc_type="circular",
            language="en",
            issued_on=dt.date(2026, 9, 1),
            paragraphs=(
                school_name,
                "Circular No. 14/2026-27 (synthetic)",
                "Half-yearly examinations for classes I to XII will be held from 5 October 2026 "
                "to 16 October 2026. Classes Nursery to UKG have activity-based assessment in the "
                "same week.",
                "Hall tickets will be issued by the class teacher on 1 October 2026. Students "
                "with fee dues must clear them before collecting the hall ticket.",
                "Answer scripts will be shown to parents at the parent-teacher meeting on "
                "31 October 2026.",
                "Principal",
            ),
        ),
        CorpusDocument(
            key="circular-sankranti-holidays-te",
            title="సర్క్యులర్: సంక్రాంతి సెలవులు 2027",
            purpose="circular",
            doc_type="circular",
            language="te",
            issued_on=dt.date(2026, 12, 20),
            paragraphs=(
                school_name,
                "సర్క్యులర్ సంఖ్య 31/2026-27 (సింథటిక్)",
                "సంక్రాంతి సందర్భంగా పాఠశాలకు 10 జనవరి 2027 నుండి 18 జనవరి 2027 వరకు సెలవులు. "
                "పాఠశాల 19 జనవరి 2027 న తిరిగి ప్రారంభమవుతుంది.",
                "సెలవుల హోంవర్క్ తరగతి ఉపాధ్యాయులు అందజేస్తారు.",
                "ప్రధానోపాధ్యాయులు",
            ),
        ),
        CorpusDocument(
            key="circular-ptm-bilingual",
            title="Circular: parent-teacher meeting / తల్లిదండ్రుల సమావేశం",
            purpose="circular",
            doc_type="circular",
            language="mixed",
            issued_on=dt.date(2026, 10, 20),
            paragraphs=(
                school_name,
                "The parent-teacher meeting for all classes is on Saturday, 31 October 2026, "
                "from 9:30 am to 12:30 pm in the classrooms.",
                "అన్ని తరగతుల తల్లిదండ్రుల-ఉపాధ్యాయుల సమావేశం 31 అక్టోబర్ 2026 శనివారం ఉదయం "
                "9:30 నుండి మధ్యాహ్నం 12:30 వరకు తరగతి గదులలో జరుగుతుంది.",
                "Principal / ప్రధానోపాధ్యాయులు",
            ),
        ),
        CorpusDocument(
            key="minutes-smc-2026-08",
            title="Minutes: School Management Committee meeting, August 2026",
            purpose="other",
            doc_type="minutes",
            language="en",
            issued_on=dt.date(2026, 8, 22),
            paragraphs=(
                school_name,
                "Minutes of the School Management Committee meeting held on 22 August 2026 "
                "(synthetic).",
                "Present: the chairperson, the principal, two teacher members and four parent "
                "members.",
                "1. The committee approved the purchase of 40 library books for classes VI to X.",
                "2. Drinking-water filters on the ground floor will be serviced every three "
                "months.",
                "3. The office will check student records against UDISE+ before the portal closes "
                "on 30 November 2026 and report mismatches at the next meeting.",
                "Next meeting: 21 November 2026.",
            ),
        ),
        CorpusDocument(
            key="fee-policy-2026-27-en",
            title="Fee policy 2026-27",
            purpose="policy",
            doc_type="policy",
            language="en",
            issued_on=dt.date(2026, 5, 15),
            paragraphs=(
                school_name,
                "Fee policy for the academic year 2026-27 (synthetic figures).",
                "Tuition fee is paid in three terms: by 15 June, by 15 October and by "
                "15 January. Nursery to UKG: Rs 6,000 per term. Classes I to V: Rs 7,500 per term. "
                "Classes VI to X: Rs 9,000 per term. Classes XI and XII: Rs 11,000 per term.",
                "A late fee of Rs 100 per week applies after the due date, up to Rs 500 per term.",
                "Siblings studying in the school get a 10 per cent concession on the younger "
                "child's tuition fee.",
                "Fees once paid are not refunded, except the caution deposit, which is returned "
                "with the transfer certificate.",
            ),
        ),
        CorpusDocument(
            key="fee-policy-2026-27-te",
            title="ఫీజు విధానం 2026-27",
            purpose="policy",
            doc_type="policy",
            language="te",
            issued_on=dt.date(2026, 5, 15),
            paragraphs=(
                school_name,
                "2026-27 విద్యా సంవత్సరానికి ఫీజు విధానం (సింథటిక్ అంకెలు).",
                "ట్యూషన్ ఫీజు మూడు విడతలుగా చెల్లించాలి: జూన్ 15, అక్టోబర్ 15, జనవరి 15 లోపు.",
                "గడువు తర్వాత వారానికి రూ. 100 ఆలస్య రుసుము, ఒక విడతకు గరిష్టంగా రూ. 500.",
                "పాఠశాలలో చదువుతున్న తోబుట్టువులకు చిన్న పిల్లల ట్యూషన్ ఫీజులో 10 శాతం రాయితీ.",
            ),
        ),
    ]
