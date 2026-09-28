"""Pre-check report model, sheets and A4 print HTML (US-501 AC1, AC2, AC4; FR-EXP-002, FR-EXP-003).

Pure module: the service collects the data (through ``students.service`` and ``dq.service``)
into :class:`PrecheckInput`; this module arranges it into

- the **summary** (counts, profile, scope, generated time, watermark),
- the **findings** list, blockers first (US-501 AC2),
- the **ready to enter** sheet in the profile's target field order (US-501 AC4),

and renders the print HTML for the PDF. Every value is HTML-escaped; the page has no scripts,
no links and no external resources except the bundled font (served by the renderer).
"""

from __future__ import annotations

import html
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.core.pdf import FONT_FAMILY, FONT_URL
from app.exports.config import ExportsConfig, Language
from app.exports.tables import Table, clean_text

SEVERITY_RANK: Final = {"blocker": 5, "high": 4, "medium": 3, "low": 2, "info": 1}


@dataclass(frozen=True, slots=True)
class FindingLine:
    student_id: uuid.UUID
    severity: str
    admission_no: str | None
    student: str | None
    class_section: str | None
    rule_id: str
    field: str | None
    values: str
    explanation: str
    route: str


@dataclass(frozen=True, slots=True)
class ReadyLine:
    student_id: uuid.UUID
    class_section: str | None
    sort_key: tuple[str, ...]
    values: tuple[str | None, ...]


@dataclass(frozen=True, slots=True)
class PrecheckInput:
    language: Language
    school: str
    profile_label: str
    export_ref: uuid.UUID
    generated_at: str
    scope_label: str
    field_labels: tuple[str, ...]
    findings: tuple[FindingLine, ...]
    ready: tuple[ReadyLine, ...]
    sensitive_masked: bool


@dataclass(frozen=True, slots=True)
class Counts:
    students: int
    blockers: int
    warnings: int
    students_with_blockers: int
    students_ready: int


@dataclass(frozen=True, slots=True)
class PrecheckReport:
    data: PrecheckInput
    counts: Counts
    summary: Table
    findings: Table
    ready: Table
    title: str
    watermark: str

    @property
    def tables(self) -> tuple[Table, ...]:
        return (self.summary, self.findings, self.ready)


def _finding_order(line: FindingLine) -> tuple[int, str, str, str, str]:
    return (
        -SEVERITY_RANK.get(line.severity, 0),
        line.class_section or "",
        line.admission_no or "",
        line.student or "",
        line.rule_id,
    )


def _counts(data: PrecheckInput) -> tuple[Counts, dict[uuid.UUID, str]]:
    blockers = sum(1 for f in data.findings if f.severity == "blocker")
    state: dict[uuid.UUID, str] = {}
    for f in data.findings:
        if f.severity == "blocker":
            state[f.student_id] = "blocked"
        else:
            state.setdefault(f.student_id, "warning")
    with_blockers = sum(1 for s in state.values() if s == "blocked")
    students = len(data.ready)
    return (
        Counts(
            students=students,
            blockers=blockers,
            warnings=len(data.findings) - blockers,
            students_with_blockers=with_blockers,
            students_ready=sum(1 for r in data.ready if r.student_id not in state),
        ),
        state,
    )


def build_precheck(data: PrecheckInput, cfg: ExportsConfig) -> PrecheckReport:
    lang = data.language

    def t(key: str) -> str:
        return cfg.label(key, lang)

    counts, state = _counts(data)
    watermark = cfg.watermark_text()
    title = f"{t('title_precheck')}: {data.profile_label}"
    summary_rows: list[tuple[object, ...]] = [
        (t("school"), data.school),
        (t("profile"), data.profile_label),
        (t("scope"), data.scope_label),
        (t("generated_at"), data.generated_at),
        (t("export_ref"), str(data.export_ref)),
        (t("students"), counts.students),
        (t("blockers"), counts.blockers),
        (t("warnings"), counts.warnings),
        (t("students_with_blockers"), counts.students_with_blockers),
        (t("students_ready"), counts.students_ready),
        ("", watermark),
    ]
    if data.sensitive_masked:
        summary_rows.append(("", t("sensitive_masked")))
    summary = Table(
        name=t("sheet_summary"), header=(t("title_precheck"), ""), rows=tuple(summary_rows)
    )
    findings = Table(
        name=t("sheet_findings"),
        header=(
            t("col_severity"),
            t("col_admission_no"),
            t("col_student"),
            t("col_class_section"),
            t("col_rule"),
            t("col_field"),
            t("col_values"),
            t("col_explanation"),
            t("col_route"),
        ),
        rows=tuple(
            (
                t(f"severity_{f.severity}"),
                f.admission_no,
                f.student,
                f.class_section,
                f.rule_id,
                f.field,
                f.values,
                f.explanation,
                f.route,
            )
            for f in sorted(data.findings, key=_finding_order)
        ),
    )
    status_label = {
        "blocked": t("status_blocked"),
        "warning": t("status_warning"),
    }
    ready = Table(
        name=t("sheet_ready"),
        header=(*data.field_labels, t("col_status")),
        rows=tuple(
            (*r.values, status_label.get(state.get(r.student_id, ""), t("status_ready")))
            for r in sorted(data.ready, key=lambda r: r.sort_key)
        ),
    )
    return PrecheckReport(
        data=data,
        counts=counts,
        summary=summary,
        findings=findings,
        ready=ready,
        title=title,
        watermark=watermark,
    )


# --- print HTML -----------------------------------------------------------------------------------

STYLE: Final = f"""
@font-face {{ font-family: "{FONT_FAMILY}"; src: url("{FONT_URL}") format("truetype");
  font-weight: 100 900; font-stretch: 62.5% 100%; }}
@page {{ size: A4 landscape; margin: 14mm 12mm 16mm; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ color: #111; background: #fff; font-family: "{FONT_FAMILY}", sans-serif;
  font-size: 9pt; line-height: 1.7; }}
.watermark {{ position: fixed; top: 42%; left: 0; right: 0; text-align: center;
  transform: rotate(-18deg); font-size: 26pt; font-weight: 700; color: rgba(160, 0, 0, 0.10);
  line-height: 1.8; z-index: 0; pointer-events: none; }}
header, section {{ position: relative; z-index: 1; }}
h1 {{ font-size: 15pt; margin: 0 0 1mm; line-height: 1.6; }}
.school {{ font-size: 12pt; font-weight: 600; margin: 0; }}
.mark {{ margin: 1mm 0 3mm; font-weight: 600; color: #a00; }}
table {{ width: 100%; border-collapse: collapse; margin: 2mm 0 5mm; }}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; }}
th, td {{ border: 1px solid #666; padding: 1.2mm 2mm; text-align: left; vertical-align: top;
  line-height: 1.7; overflow-wrap: anywhere; }}
th {{ background: #eee; font-weight: 600; }}
.summary th {{ width: 45%; }}
.summary {{ width: 60%; }}
.sev-blocker td:first-child {{ font-weight: 700; color: #a00; }}
.note {{ border: 1px solid #333; padding: 2mm 3mm; margin: 2mm 0; }}
"""


def _e(value: object) -> str:
    return html.escape(clean_text(value), quote=True)


def _row(cells: Sequence[object], *, tag: str = "td", cls: str | None = None) -> str:
    inner = "".join(f"<{tag}>{_e(c)}</{tag}>" for c in cells)
    attr = f' class="{html.escape(cls, quote=True)}"' if cls else ""
    return f"<tr{attr}>{inner}</tr>"


def render_precheck_html(report: PrecheckReport, cfg: ExportsConfig) -> str:
    """A4 landscape print page: summary and findings (blockers first), watermarked."""
    data = report.data
    lang = data.language
    summary = "".join(_row(r, tag="td") for r in report.summary.rows[:10])
    notes = []
    if data.sensitive_masked:
        notes.append(f'<p class="note">{_e(cfg.label("sensitive_masked", lang))}</p>')
    if report.findings.rows:
        severities = [f.severity for f in sorted(data.findings, key=_finding_order)]
        body = "".join(
            _row(r, cls=f"sev-{sev}")
            for r, sev in zip(report.findings.rows, severities, strict=True)
        )
        findings = (
            f"<table><thead>{_row(report.findings.header, tag='th')}</thead>"
            f"<tbody>{body}</tbody></table>"
        )
    else:
        findings = f'<p class="note">{_e(cfg.label("no_findings", lang))}</p>'
    return (
        "<!doctype html>"
        f'<html lang="{lang}"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; font-src https://assets.sos.invalid\">"
        f"<title>{_e(report.title)}</title><style>{STYLE}</style></head><body>"
        f'<div class="watermark" aria-hidden="true">{_e(cfg.watermark.en)}<br>'
        f"{_e(cfg.watermark.te)}</div>"
        f'<header><p class="school">{_e(data.school)}</p><h1>{_e(report.title)}</h1>'
        f'<p class="mark">{_e(report.watermark)}</p></header>'
        f'<section><table class="summary"><tbody>{summary}</tbody></table>{"".join(notes)}'
        f"</section><section><h1>{_e(cfg.label('sheet_findings', lang))}</h1>{findings}</section>"
        "</body></html>"
    )


__all__ = [
    "SEVERITY_RANK",
    "Counts",
    "FindingLine",
    "PrecheckInput",
    "PrecheckReport",
    "ReadyLine",
    "build_precheck",
    "render_precheck_html",
]
