"""Certificate and register print pages (HTML/CSS templates, template version ``v1``;
FR-CERT-001, FR-CERT-011, FR-REG-001..004; CLAUDE.md §10 "print is first-class").

Pure module: the service passes finished data (:class:`~app.certificates.schemas
.CertificateContent`, register lines) and gets a self-contained page back. Every value is passed
through :func:`html.escape` (a name such as ``<script>`` prints as text) and through the Aadhaar
mask (FR-CERT-009, defence in depth). Pages have no scripts, links or images; their only style
block is allowed by hash in :data:`STYLE_CSP` when served to a browser. For the PDF the same page
gets the bundled Noto Sans Telugu (``@font-face`` served in memory by :mod:`app.core.pdf`); in a
browser the system's Telugu font is used (Nirmala UI on Windows, Noto Sans Telugu on Android).
Line heights stay at 1.6 or more so Telugu vowel signs are never clipped.

English first (ADR-0036): while Telugu is hidden (``app.core.languages``) pages are English
only: no Telugu headings, labels, statements, watermarks or footers, no Telugu font in the
font stack and no ``@font-face`` for the bundled font. The Telugu paths stay, dormant.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal
from zoneinfo import ZoneInfo

from app.certificates.schemas import CertificateContent, ContentLine
from app.core.languages import contains_telugu, telugu_enabled
from app.core.pdf import FONT_FAMILY, font_face_css
from app.core.redaction import mask_aadhaar

IST: Final = ZoneInfo("Asia/Kolkata")
TEMPLATE_VERSION: Final = "v1"

Mark = Literal["draft", "cancelled"] | None

# English first (ADR-0036): the default stack names no Telugu font; the Telugu one is used only
# while Telugu is shown.
_FONTS: Final = '"Noto Sans", Arial, sans-serif'
_FONTS_TE: Final = (
    f'"{FONT_FAMILY}", "Noto Sans Telugu", "Nirmala UI", "Gautami", "Noto Sans", Arial, sans-serif'
)


def _certificate_style(fonts: str) -> str:
    return f"""
@page {{ size: A4; margin: 14mm 14mm 18mm; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ color: #111; background: #fff; font-family: {fonts}; font-size: 11pt;
  line-height: 1.7; }}
main {{ position: relative; border: 3px double #222; padding: 7mm 8mm; min-height: 250mm; }}
.watermark {{ position: absolute; top: 38%; left: 0; right: 0; text-align: center;
  transform: rotate(-24deg); font-size: 38pt; font-weight: 700; line-height: 1.8;
  color: rgba(150, 0, 0, 0.16); z-index: 0; }}
header, section, footer {{ position: relative; z-index: 1; }}
header {{ text-align: center; border-bottom: 1px solid #222; padding-bottom: 3mm; }}
.school {{ font-size: 17pt; font-weight: 700; margin: 0; line-height: 1.6; }}
.school-te {{ font-size: 14pt; font-weight: 600; margin: 0; line-height: 1.8; }}
.address, .affiliation {{ margin: 0; font-size: 10pt; line-height: 1.7; }}
h1 {{ text-align: center; font-size: 15pt; margin: 4mm 0 1mm; letter-spacing: 0.04em;
  line-height: 1.6; }}
.title-te {{ display: block; font-size: 13pt; letter-spacing: 0; line-height: 1.8; }}
.meta {{ display: flex; justify-content: space-between; gap: 6mm; margin: 3mm 0;
  flex-wrap: wrap; }}
.duplicate {{ border: 2px solid #111; padding: 2mm 3mm; margin: 2mm 0; font-weight: 700;
  line-height: 1.7; }}
.statement {{ margin: 4mm 0; text-align: justify; line-height: 1.9; }}
table {{ width: 100%; border-collapse: collapse; margin: 3mm 0; }}
tr {{ page-break-inside: avoid; }}
th, td {{ border: 1px solid #555; padding: 1.6mm 2.5mm; text-align: left; vertical-align: top;
  line-height: 1.7; overflow-wrap: anywhere; }}
th {{ width: 46%; font-weight: 600; background: #f3f3f3; }}
td.no {{ width: 7%; text-align: center; }}
.te {{ display: block; font-weight: 400; color: #333; }}
.blank {{ color: #555; }}
.signatures {{ display: flex; justify-content: space-between; gap: 10mm; margin-top: 18mm;
  page-break-inside: avoid; }}
.signatures div {{ flex: 1; border-top: 1px solid #111; padding-top: 2mm; line-height: 1.7;
  text-align: center; }}
footer {{ margin-top: 6mm; font-size: 8.5pt; color: #444; line-height: 1.7; }}
@media screen {{ body {{ background: #e9e9e9; }} main {{ max-width: 190mm; margin: 6mm auto;
  background: #fff; }} }}
"""


def _register_style(fonts: str) -> str:
    return f"""
@page {{ size: A4 landscape; margin: 12mm 10mm 14mm; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ color: #111; background: #fff; font-family: {fonts}; font-size: 8.5pt;
  line-height: 1.7; }}
header {{ margin-bottom: 3mm; }}
.school {{ font-size: 13pt; font-weight: 700; margin: 0; line-height: 1.6; }}
h1 {{ font-size: 13pt; margin: 1mm 0; line-height: 1.7; }}
.te {{ display: block; font-weight: 400; color: #333; }}
.meta {{ margin: 0 0 2mm; }}
table {{ width: 100%; border-collapse: collapse; }}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; }}
th, td {{ border: 1px solid #555; padding: 1.2mm 1.6mm; text-align: left; vertical-align: top;
  line-height: 1.7; overflow-wrap: anywhere; }}
th {{ background: #eee; font-weight: 600; }}
tr.cancelled td {{ color: #7a0000; }}
.empty {{ border: 1px solid #333; padding: 3mm; }}
footer {{ margin-top: 4mm; font-size: 8pt; color: #444; }}
@media screen {{ body {{ background: #e9e9e9; padding: 6mm; }} main {{ background: #fff;
  padding: 6mm; }} }}
"""


# English (the default, ADR-0036) and Telugu variants of the two print styles.
CERTIFICATE_STYLE: Final = _certificate_style(_FONTS)
REGISTER_STYLE: Final = _register_style(_FONTS)
CERTIFICATE_STYLE_TE: Final = _certificate_style(_FONTS_TE)
REGISTER_STYLE_TE: Final = _register_style(_FONTS_TE)


def certificate_style() -> str:
    """The certificate style in use: Telugu font stack only while Telugu is shown."""
    return CERTIFICATE_STYLE_TE if telugu_enabled() else CERTIFICATE_STYLE


def register_style() -> str:
    """The register style in use: Telugu font stack only while Telugu is shown."""
    return REGISTER_STYLE_TE if telugu_enabled() else REGISTER_STYLE


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


# Content-Security-Policy for the print views served to browsers: nothing but the style blocks
# (both variants are pinned, so turning Telugu on or off needs no other change).
STYLE_CSP: Final = (
    "default-src 'none'; style-src "
    + " ".join(
        _style_hash(s)
        for s in (CERTIFICATE_STYLE, REGISTER_STYLE, CERTIFICATE_STYLE_TE, REGISTER_STYLE_TE)
    )
    + "; img-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
# The PDF page is printed by the renderer, which serves only the bundled font.
_PDF_CSP: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; font-src https://assets.sos.invalid"
)


def _e(value: object) -> str:
    text = "" if value is None else str(value)
    return html.escape(mask_aadhaar(text), quote=True)


def format_date(value: dt.date | None) -> str:
    """DD/MM/YYYY (PRD §8: Indian conventions)."""
    return value.strftime("%d/%m/%Y") if value is not None else ""


_ORDINALS: Final = (
    "",
    "First",
    "Second",
    "Third",
    "Fourth",
    "Fifth",
    "Sixth",
    "Seventh",
    "Eighth",
    "Ninth",
    "Tenth",
    "Eleventh",
    "Twelfth",
    "Thirteenth",
    "Fourteenth",
    "Fifteenth",
    "Sixteenth",
    "Seventeenth",
    "Eighteenth",
    "Nineteenth",
    "Twentieth",
)
_ONES: Final = (
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
)
_TENS: Final = (
    "",
    "",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
)
_MONTHS: Final = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _ordinal_day(day: int) -> str:
    if day <= 20:
        return _ORDINALS[day]
    tens, ones = divmod(day, 10)
    if ones == 0:
        return {3: "Thirtieth"}.get(tens, _ORDINALS[20])
    return f"{_TENS[tens]}-{_ORDINALS[ones]}"


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def _year_words(year: int) -> str:
    thousands, rest = divmod(year, 1000)
    hundreds, below = divmod(rest, 100)
    parts = [f"{_ONES[thousands]} Thousand"] if thousands else []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if below:
        parts.append(_below_hundred(below))
    return " ".join(parts)


def date_in_words(value: dt.date) -> str:
    """English words for a date, as TCs print the date of birth ("Fourteenth March Two
    Thousand Twelve")."""
    return f"{_ordinal_day(value.day)} {_MONTHS[value.month]} {_year_words(value.year)}"


# --- certificate page ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DuplicateMark:
    copy_no: int
    issued_on: dt.date


def _line(lines: Sequence[ContentLine], key: str) -> str:
    for line in lines:
        if line.key == key:
            return line.value or ""
    return ""


def _statement(content: CertificateContent) -> tuple[str, str]:
    """The certifying sentence (EN, TE) for bonafide, study and conduct certificates."""
    name = _e(content.student_name)
    father = _e(_line(content.fields, "father_name"))
    adm = _e(content.admission_no)
    year = _e(content.academic_year_label)
    details = content.details
    klass_en = _e(content.class_label_en or "")
    klass_te = _e(content.class_label_te or content.class_label_en or "")
    if content.certificate_type == "bonafide":
        dob = _e(_line(content.fields, "dob"))
        purpose_en = _e(_line(details, "purpose"))
        purpose_te = _e(_line(details, "purpose_te"))
        en = (
            f"This is to certify that <strong>{name}</strong> (father's name: {father}; "
            f"admission number {adm}) is a bonafide student of this school, studying in "
            f"<strong>{klass_en}</strong> during the academic year {year}. The date of birth "
            f"as per the school records is {dob}. This certificate is issued for the purpose "
            f"of: {purpose_en}."
        )
        te = (
            f"<strong>{name}</strong> (తండ్రి పేరు: {father}; ప్రవేశ సంఖ్య {adm}) మా పాఠశాలలో "
            f"{year} విద్యా సంవత్సరంలో <strong>{klass_te}</strong> చదువుతున్న విద్యార్థి అని "
            f"ధృవీకరిస్తున్నాము. పాఠశాల రికార్డుల ప్రకారం పుట్టిన తేదీ {dob}. ఈ ధృవీకరణ పత్రం "
            f"{purpose_te} కోసం ఇవ్వబడుతోంది."
        )
        return en, te
    period_from = _e(_line(details, "study_from"))
    period_to = _e(_line(details, "study_to"))
    if content.certificate_type == "study":
        en = (
            f"This is to certify that <strong>{name}</strong> (father's name: {father}; "
            f"admission number {adm}) studied in this school from {period_from} to "
            f"{period_to}, as per the school records."
        )
        te = (
            f"<strong>{name}</strong> (తండ్రి పేరు: {father}; ప్రవేశ సంఖ్య {adm}) పాఠశాల "
            f"రికార్డుల ప్రకారం మా పాఠశాలలో {period_from} నుండి {period_to} వరకు చదివినట్లు "
            f"ధృవీకరిస్తున్నాము."
        )
        return en, te
    conduct_en = _e(_line(details, "conduct"))
    conduct_te = _e(_line(details, "conduct_te"))
    en = (
        f"This is to certify that <strong>{name}</strong> (father's name: {father}; admission "
        f"number {adm}) was a student of this school from {period_from} to {period_to}, and "
        f"that the student's conduct and character during this period were "
        f"<strong>{conduct_en}</strong>."
    )
    te = (
        f"<strong>{name}</strong> (తండ్రి పేరు: {father}; ప్రవేశ సంఖ్య {adm}) మా పాఠశాలలో "
        f"{period_from} నుండి {period_to} వరకు చదివారు. ఈ కాలంలో ప్రవర్తన, నడవడిక "
        f"<strong>{conduct_te}</strong> అని ధృవీకరిస్తున్నాము."
    )
    return en, te


def _te(fragment: str) -> str:
    """A Telugu-only fragment of a page: kept while Telugu is shown, dropped otherwise
    (ADR-0036)."""
    return fragment if telugu_enabled() else ""


def _slash(en: str, te: str) -> str:
    """``English / Telugu`` while Telugu is shown, ``English`` otherwise (ADR-0036)."""
    return f"{en} / {te}" if telugu_enabled() else en


def _english_value(value: str | None) -> str | None:
    """A printed value made by SchoolOS as ``English / Telugu`` (a gender, a choice, a class)
    keeps its English part. Other values (names as recorded, even in Telugu script) are data
    and stay as they are."""
    if value is None or not contains_telugu(value) or " / " not in value:
        return value
    english = value.split(" / ", 1)[0]
    return value if contains_telugu(english) else english


def _english_lines(lines: Sequence[ContentLine]) -> list[ContentLine]:
    return [
        line.model_copy(update={"label_te": "", "value": _english_value(line.value)})
        for line in lines
        if not line.key.endswith("_te")
    ]


def english_only(content: CertificateContent) -> CertificateContent:
    """``content`` without its Telugu-only parts, as shown while Telugu is hidden (ADR-0036):
    Telugu titles, labels, school name and address emptied, the Telugu statement lines
    (``*_te``) dropped and the Telugu half of ``English / Telugu`` values removed. The frozen
    record itself is not changed (its hash still verifies)."""
    return content.model_copy(
        update={
            "title_te": "",
            "school_name_te": "",
            "school_address_te": "",
            "class_label_te": None,
            "fields": _english_lines(content.fields),
            "details": _english_lines(content.details),
            "blanks": _english_lines(content.blanks),
        }
    )


def shown(content: CertificateContent) -> CertificateContent:
    """The content as shown now: whole while Telugu is shown, :func:`english_only` otherwise."""
    return content if telugu_enabled() else english_only(content)


def _rows(lines: Sequence[ContentLine], start: int) -> tuple[str, int]:
    out = []
    n = start
    for line in lines:
        n += 1
        value = (
            _e(line.value)
            if line.value
            else '<span class="blank">______________________________</span>'
        )
        out.append(
            f'<tr><td class="no">{n}</td><th>{_e(line.label_en)}'
            + _te(f'<span class="te">{_e(line.label_te)}</span>')
            + f"</th><td>{value}</td></tr>"
        )
    return "".join(out), n


def render_certificate(
    content: CertificateContent,
    *,
    reference: str,
    mark: Mark = None,
    duplicate: DuplicateMark | None = None,
    for_pdf: bool = False,
) -> str:
    """The A4 certificate page. ``reference``: short record reference printed in the footer;
    ``mark``: ``draft`` (awaiting approval, not valid) or ``cancelled``; ``duplicate``: the
    copy number and date of a duplicate (FR-CERT-007). English only while Telugu is hidden
    (ADR-0036)."""
    content = shown(content)
    watermark = ""
    if mark == "draft":
        watermark = "DRAFT · NOT VALID" + _te("<br>ముసాయిదా · చెల్లదు")
    elif mark == "cancelled":
        watermark = "CANCELLED" + _te("<br>రద్దు చేయబడింది")
    elif duplicate is not None:
        watermark = "DUPLICATE" + _te("<br>నకలు")
    school = [f'<p class="school">{_e(content.school_name_en)}</p>']
    if content.school_name_te:
        school.append(f'<p class="school-te">{_e(content.school_name_te)}</p>')
    for text in (content.school_address_en, content.school_address_te):
        if text:
            school.append(f'<p class="address">{_e(text)}</p>')
    if content.school_affiliation:
        school.append(f'<p class="affiliation">{_e(content.school_affiliation)}</p>')
    dup = ""
    if duplicate is not None:
        dup = (
            f'<p class="duplicate">DUPLICATE (copy {duplicate.copy_no}) issued on '
            f"{_e(format_date(duplicate.issued_on))} in place of the original serial number "
            f"{_e(content.serial)} dated {_e(format_date(content.issued_on))}."
            + _te(
                f'<span class="te">నకలు (ప్రతి {duplicate.copy_no}) '
                f"{_e(format_date(duplicate.issued_on))} న, అసలు క్రమ సంఖ్య {_e(content.serial)} "
                f"(తేదీ {_e(format_date(content.issued_on))}) స్థానంలో ఇవ్వబడింది.</span>"
            )
            + "</p>"
        )
    meta = (
        '<div class="meta">'
        f"<span>{_slash('Serial no.', 'క్రమ సంఖ్య')}: "
        f"<strong>{_e(content.serial or '—')}</strong></span>"
        f"<span>{_slash('Admission no.', 'ప్రవేశ సంఖ్య')}: "
        f"<strong>{_e(content.admission_no)}</strong></span>"
        f"<span>{_slash('Date', 'తేదీ')}: "
        f"<strong>{_e(format_date(content.issued_on))}</strong></span>"
        "</div>"
    )
    if content.certificate_type == "transfer":
        body_rows, n = _rows(content.fields, 0)
        detail_rows, n = _rows(content.details, n)
        blank_rows, _ = _rows(content.blanks, n)
        body = f"<table><tbody>{body_rows}{detail_rows}{blank_rows}</tbody></table>"
    else:
        en, te = _statement(content)
        body = f'<p class="statement">{en}</p>' + _te(f'<p class="statement">{te}</p>')
        if content.blanks:
            blank_rows, _ = _rows(content.blanks, 0)
            body += f"<table><tbody>{blank_rows}</tbody></table>"
    place = _e(content.school_place)
    signatures = (
        '<div class="signatures">'
        "<div>Prepared by (office)"
        + _te('<span class="te">తయారు చేసినవారు (కార్యాలయం)</span>')
        + "</div><div>Checked by"
        + _te('<span class="te">తనిఖీ చేసినవారు</span>')
        + "</div><div>Principal (signature and seal)"
        + _te('<span class="te">ప్రధానోపాధ్యాయుల సంతకం, ముద్ర</span>')
        + "</div></div>"
    )
    style = (font_face_css() if for_pdf else "") + certificate_style()
    csp = f'<meta http-equiv="Content-Security-Policy" content="{_PDF_CSP}">' if for_pdf else ""
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f"{csp}<title>{_e(content.title_en)} {_e(content.serial)}</title>"
        f"<style>{style}</style></head><body><main>"
        + (f'<div class="watermark" aria-hidden="true">{watermark}</div>' if watermark else "")
        + f"<header>{''.join(school)}</header>"
        f"<h1>{_e(content.title_en.upper())}"
        + _te(f'<span class="title-te">{_e(content.title_te)}</span>')
        + f"</h1>{meta}{dup}<section>{body}</section>"
        f"<section><p>{_slash('Place', 'స్థలం')}: {place}</p></section>{signatures}"
        f"<footer>Generated from the school's records by SchoolOS · record reference "
        f"{_e(reference)} · template {TEMPLATE_VERSION}"
        + _te('<span class="te">పాఠశాల రికార్డుల నుండి SchoolOS తయారు చేసింది</span>')
        + "</footer></main></body></html>"
    )


# --- registers -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegisterPage:
    title_en: str
    title_te: str
    school_name: str
    school_name_te: str
    academic_year_label: str
    printed_at: dt.datetime
    header: Sequence[tuple[str, str]]  # (EN, TE) column headings
    rows: Sequence[Sequence[str]]
    cancelled: Sequence[bool]
    empty_en: str
    empty_te: str


def render_register(page: RegisterPage) -> str:
    """An A4 landscape register page (FR-REG-001..004): bilingual headings (English only while
    Telugu is hidden, ADR-0036), one row per line, cancelled lines marked, headings repeated on
    every printed page."""
    head = "".join(
        f"<th>{_e(en)}" + _te(f'<span class="te">{_e(te)}</span>') + "</th>"
        for en, te in page.header
    )
    if page.rows:
        body = "".join(
            ('<tr class="cancelled">' if cancelled else "<tr>")
            + "".join(f"<td>{_e(c)}</td>" for c in row)
            + "</tr>"
            for row, cancelled in zip(page.rows, page.cancelled, strict=True)
        )
        table = f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    else:
        table = (
            f'<p class="empty">{_e(page.empty_en)}'
            + _te(f'<span class="te">{_e(page.empty_te)}</span>')
            + "</p>"
        )
    printed = page.printed_at.astimezone(IST).strftime("%d/%m/%Y %H:%M")
    school_te = (
        _te(f'<span class="te">{_e(page.school_name_te)}</span>') if page.school_name_te else ""
    )
    footer = "Printed from SchoolOS for the school's paper register" + _te(
        " · కాగితపు రిజిస్టర్ కోసం SchoolOS నుండి ముద్రించబడింది"
    )
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f"<title>{_e(page.title_en)} {_e(page.academic_year_label)}</title>"
        f"<style>{register_style()}</style></head><body><main>"
        f'<header><p class="school">{_e(page.school_name)}{school_te}</p>'
        f"<h1>{_e(page.title_en)}" + _te(f'<span class="te">{_e(page.title_te)}</span>') + "</h1>"
        f'<p class="meta">{_slash("Academic year", "విద్యా సంవత్సరం")}: '
        f"<strong>{_e(page.academic_year_label)}</strong> · "
        f"{_slash('Printed', 'ముద్రించిన తేదీ')}: "
        f"{_e(printed)} IST · {_slash('Rows', 'వరుసలు')}: {len(page.rows)}</p></header>"
        f"<section>{table}</section>"
        f"<footer>{footer}</footer>"
        "</main></body></html>"
    )


__all__ = [
    "CERTIFICATE_STYLE",
    "CERTIFICATE_STYLE_TE",
    "REGISTER_STYLE",
    "REGISTER_STYLE_TE",
    "STYLE_CSP",
    "TEMPLATE_VERSION",
    "DuplicateMark",
    "Mark",
    "RegisterPage",
    "certificate_style",
    "date_in_words",
    "english_only",
    "format_date",
    "register_style",
    "render_certificate",
    "render_register",
    "shown",
]
