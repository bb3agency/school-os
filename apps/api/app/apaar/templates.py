"""Printable APAAR consent form (A4, one page per student; FR-APC-004; CLAUDE.md §10 "print is
first-class").

Pure module: the service passes finished data and gets a self-contained page back. Every value
goes through :func:`html.escape` and the Aadhaar mask (invariant 4, defence in depth; the form
never prints an Aadhaar number, only what the office already holds: name, admission number,
class, date of birth and the UDISE+ PEN). Pages have no scripts, links, images or forms; their
only style block is allowed by hash in :data:`STYLE_CSP`. Telugu forms use the system's Telugu
font (Nirmala UI on Windows, Noto Sans Telugu on Android) with line heights of 1.7 or more so
vowel signs are never clipped. The parent form's language is the school's choice (owner decision
D9), not ``SOS_TELUGU_ENABLED``.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.apaar.config import ApaarConfig, FormLanguage
from app.core.redaction import mask_aadhaar

_FONTS: Final = '"Noto Sans", Arial, sans-serif'
_FONTS_TE: Final = '"Noto Sans Telugu", "Nirmala UI", "Gautami", "Noto Sans", Arial, sans-serif'


def _style(fonts: str) -> str:
    return f"""
@page {{ size: A4; margin: 14mm 14mm 16mm; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ color: #111; background: #fff; font-family: {fonts}; font-size: 10.5pt;
  line-height: 1.7; }}
article {{ page-break-after: always; break-after: page; }}
article:last-child {{ page-break-after: auto; break-after: auto; }}
header {{ text-align: center; border-bottom: 1px solid #222; padding-bottom: 2mm; }}
.school {{ font-size: 15pt; font-weight: 700; margin: 0; line-height: 1.6; }}
h1 {{ font-size: 13.5pt; margin: 3mm 0 0; line-height: 1.7; }}
.subtitle {{ margin: 0; font-size: 9.5pt; color: #333; line-height: 1.7; }}
h2 {{ font-size: 11pt; margin: 4mm 0 1mm; line-height: 1.7; }}
p {{ margin: 1.5mm 0; text-align: justify; line-height: 1.8; }}
table {{ width: 100%; border-collapse: collapse; margin: 2mm 0; }}
th, td {{ border: 1px solid #555; padding: 1.4mm 2.5mm; text-align: left; vertical-align: top;
  line-height: 1.7; overflow-wrap: anywhere; }}
th {{ width: 38%; font-weight: 600; background: #f3f3f3; }}
.choice {{ display: flex; gap: 3mm; align-items: flex-start; border: 1px solid #333;
  padding: 2mm 3mm; margin: 2mm 0; page-break-inside: avoid; line-height: 1.8; }}
.box {{ flex: 0 0 6mm; height: 6mm; border: 1.5px solid #111; margin-top: 0.8mm; }}
.lines {{ display: grid; grid-template-columns: 1fr 1fr; gap: 3mm 8mm; margin-top: 4mm; }}
.line {{ border-bottom: 1px solid #111; min-height: 9mm; padding-top: 5mm; font-size: 9pt;
  color: #333; line-height: 1.7; }}
.rel span {{ margin-right: 5mm; }}
.office {{ border: 1px dashed #555; padding: 2mm 3mm; margin-top: 5mm; font-size: 9pt;
  line-height: 1.7; }}
footer {{ margin-top: 3mm; font-size: 8.5pt; color: #444; line-height: 1.7; }}
@media screen {{ body {{ background: #e9e9e9; }} article {{ max-width: 190mm; margin: 6mm auto;
  padding: 8mm; background: #fff; }} }}
"""


STYLE: Final = _style(_FONTS)
STYLE_TE: Final = _style(_FONTS_TE)


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


# Content-Security-Policy for the print view served to browsers: nothing but the two style blocks.
STYLE_CSP: Final = (
    "default-src 'none'; style-src "
    + " ".join(_style_hash(s) for s in (STYLE, STYLE_TE))
    + "; img-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)


@dataclass(frozen=True, slots=True)
class FormStudent:
    """What one form prints about the student (C2 only; never an Aadhaar number)."""

    name: str | None
    admission_no: str | None
    class_section: str | None
    dob: dt.date | None
    pen: str | None


def _e(value: object) -> str:
    text = "" if value is None else str(value)
    return html.escape(mask_aadhaar(text), quote=True)


def format_date(value: dt.date | None) -> str:
    """DD/MM/YYYY (PRD §8: Indian conventions)."""
    return value.strftime("%d/%m/%Y") if value is not None else ""


def _form(cfg: ApaarConfig, language: FormLanguage, school: str, student: FormStudent) -> str:
    def t(key: str) -> str:
        return _e(cfg.text(language, key))

    rows = (
        ("name", student.name),
        ("admission_no", student.admission_no),
        ("class_section", student.class_section),
        ("dob", format_date(student.dob)),
        ("pen", student.pen),
    )
    details = "".join(f"<tr><th>{t(k)}</th><td>{_e(v)}</td></tr>" for k, v in rows)
    return (
        "<article>"
        f'<header><p class="school">{_e(school)}</p><h1>{t("title")}</h1>'
        f'<p class="subtitle">{t("subtitle")}</p></header>'
        f"<h2>{t('student_details')}</h2><table>{details}</table>"
        f"<p>{t('what')}</p><p><strong>{t('voluntary')}</strong></p>"
        f"<p>{t('board')}</p><p>{t('data')}</p><p>{t('withdraw')}</p>"
        f"<h2>{t('choice')}</h2>"
        f'<div class="choice"><span class="box"></span><span>{t("give")}</span></div>'
        f'<div class="choice"><span class="box"></span><span>{t("refuse")}</span></div>'
        '<div class="lines">'
        f'<div class="line">{t("parent_name")}</div>'
        f'<div class="line rel">{t("relationship")}: <span>☐ {t("father")}</span>'
        f"<span>☐ {t('mother')}</span><span>☐ {t('guardian')}</span></div>"
        f'<div class="line">{t("signature")}</div>'
        f'<div class="line">{t("date")}</div>'
        "</div>"
        f'<div class="office"><strong>{t("office")}</strong> · {t("received_on")}: '
        f"__________ · {t('recorded_by')}: ______________</div>"
        f"<footer>{t('footer')}</footer>"
        "</article>"
    )


def render_forms(
    cfg: ApaarConfig, *, language: FormLanguage, school: str, students: Sequence[FormStudent]
) -> str:
    """One A4 page per student, in the order given."""
    style = STYLE_TE if language == "te" else STYLE
    pages = "".join(_form(cfg, language, school, s) for s in students)
    title = _e(cfg.text(language, "title"))
    return (
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
        f"<title>{title}</title><style>{style}</style></head><body>{pages}</body></html>"
    )


__all__ = ["STYLE_CSP", "FormStudent", "format_date", "render_forms"]
