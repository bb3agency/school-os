"""Pre-check report layout (US-501 AC1, AC2, AC4; FR-EXP-002, FR-EXP-003): blockers first, the
"ready to enter" sheet in the profile's field order, bilingual texts, escaped print HTML."""

from __future__ import annotations

import uuid

from app.core.redaction import verhoeff_check_digit
from app.exports.config import load_config
from app.exports.report import (
    FindingLine,
    PrecheckInput,
    ReadyLine,
    build_precheck,
    render_precheck_html,
)

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _finding(
    student: uuid.UUID, severity: str, rule: str, name: str = "Synthetica A"
) -> FindingLine:
    return FindingLine(
        student_id=student,
        severity=severity,
        admission_no="EX/1",
        student=name,
        class_section="IX-A",
        rule_id=rule,
        field="Date of birth",
        values="Admission register: ••/••/••••",
        explanation="Date of birth differs.",
        route="Correct the school record.",
    )


def _input(language: str = "en", findings: tuple[FindingLine, ...] | None = None) -> PrecheckInput:
    return PrecheckInput(
        language=language,  # type: ignore[arg-type]
        school="Synthetic Model School",
        profile_label="CISCE registration 2026",
        export_ref=uuid.uuid4(),
        generated_at="27/09/2026 10:00",
        scope_label="IX-A",
        field_labels=("Admission number", "Full name", "Gender", "Date of birth"),
        findings=findings
        if findings is not None
        else (
            _finding(A, "medium", "DQ-004"),
            _finding(B, "blocker", "DQ-005"),
            _finding(A, "high", "DQ-003"),
        ),
        ready=(
            ReadyLine(
                A, "IX-A", ("000120", "A", "00000002", "a"), ("EX/1", "A", "F", "14/03/2012")
            ),
            ReadyLine(
                B, "IX-A", ("000120", "A", "00000001", "b"), ("EX/2", "B", "M", "01/01/2012")
            ),
            ReadyLine(C, "IX-A", ("000120", "A", "00000003", "c"), ("EX/3", "C", "F", None)),
        ),
        sensitive_masked=True,
    )


def test_US_501_AC2_blockers_come_first() -> None:
    report = build_precheck(_input(), load_config())
    severities = [row[0] for row in report.findings.rows]
    assert severities == ["Blocker", "High", "Medium"]
    assert report.counts.blockers == 1
    assert report.counts.warnings == 2
    assert report.counts.students_with_blockers == 1
    assert report.counts.students_ready == 1  # only C has no finding


def test_US_501_AC4_ready_sheet_in_target_order_with_status() -> None:
    report = build_precheck(_input(), load_config())
    assert report.ready.header == (
        "Admission number",
        "Full name",
        "Gender",
        "Date of birth",
        "Pre-check status",
    )
    rows = report.ready.rows
    assert [r[0] for r in rows] == ["EX/2", "EX/1", "EX/3"], "sorted by roll number"
    assert rows[0][-1] == "Blocked - fix first"
    assert rows[1][-1] == "Check warnings"
    assert rows[2][-1] == "Ready"
    assert [t.name for t in report.tables] == ["Summary", "Findings", "Ready to enter"]


def test_FR_EXP_002_telugu_labels() -> None:
    report = build_precheck(_input("te"), load_config())
    assert report.findings.rows[0][0] == "సమర్పణను ఆపేది"
    assert report.ready.header[-1] == "తనిఖీ స్థితి"
    assert report.summary.name == "సారాంశం"


def test_US_901_AC2_watermark_in_summary_and_html() -> None:
    cfg = load_config()
    report = build_precheck(_input(), cfg)
    assert any(cfg.watermark.en in str(v) for row in report.summary.rows for v in row)
    page = render_precheck_html(report, cfg)
    assert cfg.watermark.en in page
    assert cfg.watermark.te in page
    assert "Restricted values are hidden" in page


def test_html_escapes_values_and_has_no_scripts_or_external_resources() -> None:
    hostile = _finding(A, "blocker", "DQ-005", name='<script>alert(1)</script><img src="x">')
    report = build_precheck(_input(findings=(hostile,)), load_config())
    page = render_precheck_html(report, load_config())
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "<img" not in page
    assert "http://" not in page
    assert page.count("https://") == 2  # the bundled font URL (CSS + CSP), served from memory


def test_invariant_4_aadhaar_masked_in_pdf_html() -> None:
    body = "34567890123"
    number = body + verhoeff_check_digit(body)
    line = _finding(A, "high", "DQ-003", name=f"Synthetica {number}")
    page = render_precheck_html(
        build_precheck(_input(findings=(line,)), load_config()), load_config()
    )
    assert number not in page


def test_no_findings_note() -> None:
    report = build_precheck(_input(findings=()), load_config())
    page = render_precheck_html(report, load_config())
    assert "No open problems were found." in page
    assert report.counts.students_ready == 3
