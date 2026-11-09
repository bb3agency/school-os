"""Synthetic register pages for the extraction eval (``extraction.jsonl``; FA-07, FR-KB-058;
docs/06 §13.12). No real person, school or number.

24 pages: printed and handwritten, English, Telugu-script and code-mixed names, three rows each.
Each page plants Aadhaar-like numbers one way (or none), cycling through:

- none: a clean page (its image must stay as it is);
- an ``aadhaar_no`` column (not kept: read, scanned, dropped), written with spaces;
- word-level OCR in the margin: the number as three separate words;
- a hyphenated number in the column and a plain 12-digit one in a ``remarks`` column;
- Telugu digits in the remarks;
- the number inside a kept cell (the father's name column), with pipes between the groups;
- a margin note the OCR returns without a position (the image cannot be redacted);
- a decoy only: 12 digits that fail the checksum (not Aadhaar; must not be counted).

Handwritten pages carry one misread cell each (``read_as``), so field accuracy measures loss.
Planted numbers are placeholders here and are made in memory (``sos_evals.extraction``).
"""

from __future__ import annotations

from typing import Any, Final

from sos_evals.extraction import ExtractionCase

_EN_NAMES: Final = (
    ("Sanaboina Lakshmi Prasanna", "female", "Sanaboina Venkata Rao", "Sanaboina Padmavathi"),
    ("Pothula Ravi Teja", "male", "Pothula Nageswara Rao", "Pothula Sarada"),
    ("Kommineni Divya Sree", "female", "Kommineni Srinivas", "Kommineni Hema Latha"),
    ("Mandava Sai Charan", "male", "Mandava Prasad", "Mandava Lakshmi"),
    ("Gorantla Naga Lakshmi", "female", "Gorantla Ramesh", "Gorantla Kalyani"),
    ("Nallamothu Pavan Kalyan", "male", "Nallamothu Suresh", "Nallamothu Vani"),
)
_TE_NAMES: Final = (
    ("సనబోయిన లక్ష్మీ ప్రసన్న", "female", "సనబోయిన వెంకట రావు", "సనబోయిన పద్మావతి"),
    ("పోతుల రవి తేజ", "male", "పోతుల నాగేశ్వర రావు", "పోతుల శారద"),
    ("కొమ్మినేని దివ్య శ్రీ", "female", "కొమ్మినేని శ్రీనివాస్", "కొమ్మినేని హేమ లత"),
    ("మండవ సాయి చరణ్", "male", "మండవ ప్రసాద్", "మండవ లక్ష్మి"),
)
VARIANTS: Final = (
    "none",
    "column",
    "margin_split",
    "column_and_remarks",
    "telugu_digits",
    "kept_cell",
    "unplaced",
    "decoy_only",
)
_LOCALES: Final = ("en", "te", "mixed")


def _cell(column: str, value: str, **kw: Any) -> dict[str, Any]:
    return {"column": column, "value": value, **kw}


def _row(case_no: int, row_no: int, locale: str) -> list[dict[str, Any]]:
    pool = _TE_NAMES if locale == "te" else _EN_NAMES
    name, gender, father, mother = pool[(case_no + row_no) % len(pool)]
    year = 2016 + (case_no + row_no) % 6
    born = 2009 + (case_no * 3 + row_no) % 6
    tongue = "తెలుగు" if locale in ("te", "mixed") else "Telugu"
    return [
        _cell("admission_no", f"SV-{year}-{(case_no * 37 + row_no * 11) % 900 + 100:04d}"),
        _cell("admission_date", f"{10 + row_no:02d}/06/{year}"),
        _cell("full_name", name),
        _cell("gender", gender),
        _cell("dob", f"{(case_no + 3 * row_no) % 27 + 1:02d}/{row_no + 3:02d}/{born}"),
        _cell("father_name", father),
        _cell("mother_name", mother),
        _cell("mother_tongue", tongue),
        _cell("nationality", "Indian"),
    ]


def _misread(row: list[dict[str, Any]], case_no: int) -> None:
    """One handwriting misread per page: a digit of the date of birth or a dropped letter."""
    name = next(c for c in row if c["column"] == "father_name")
    if case_no % 4 == 1 or "{{" in name["value"]:
        dob = next(c for c in row if c["column"] == "dob")
        day = dob["value"][:2]
        dob["read_as"] = ("1" if day[0] != "1" else "2") + dob["value"][1:]
    else:
        name["read_as"] = name["value"][:-1]


def _case(case_no: int) -> ExtractionCase:
    script = "printed" if case_no % 2 == 0 else "handwritten"
    locale = _LOCALES[case_no % 3]
    variant = VARIANTS[case_no % len(VARIANTS)]
    seed = 100 + case_no
    rows = [_row(case_no, r, locale) for r in range(3)]
    margin: list[dict[str, Any]] = [{"text": "Checked by the office"}]
    if variant == "column":
        rows[1].append(_cell("aadhaar_no", f"{{{{AADHAAR:{seed}:spaced}}}}"))
    elif variant == "margin_split":
        margin.append({"text": f"{{{{AADHAAR:{seed}:split}}}}"})
    elif variant == "column_and_remarks":
        rows[0].append(_cell("aadhaar_no", f"{{{{AADHAAR:{seed}:hyphen}}}}"))
        rows[2].append(_cell("remarks", f"UID {{{{AADHAAR:{seed + 50}:plain}}}} seen"))
    elif variant == "telugu_digits":
        rows[2].append(_cell("remarks", f"ఆధార్ {{{{AADHAAR:{seed}:telugu}}}} చూశారు"))
    elif variant == "kept_cell":
        father = next(c for c in rows[0] if c["column"] == "father_name")
        father["value"] = f"{father['value']} {{{{AADHAAR:{seed}:pipe}}}}"
        father["scored"] = False
    elif variant == "unplaced":
        margin.append({"text": f"Aadhaar {{{{AADHAAR:{seed}:spaced}}}} verified", "placed": False})
    elif variant == "decoy_only":
        rows[1].append(_cell("remarks", f"Receipt {{{{DECOY:{seed}:spaced}}}} paid"))
    if script == "handwritten":
        _misread(rows[case_no % 3], case_no)
    return ExtractionCase.model_validate(
        {
            "id": f"extraction-{case_no + 1:03d}",
            "script": script,
            "locale": locale,
            "rows": rows,
            "margin": margin,
            "note": variant,
        }
    )


CASES: Final[list[ExtractionCase]] = [_case(n) for n in range(24)]

__all__ = ["CASES", "VARIANTS"]
