"""Rendered admission-register page images (PNG) for the extraction queue (docs/12 §3; US-402,
FR-IMP-020..023, PRV-016 test material). SYNTHETIC ONLY (CLAUDE.md invariant 11).

Pure module: :func:`build_register_pages` returns PNG bytes; the student seeder stores them as
``register_scan`` documents through the documents service. Each page follows the fake
extraction provider's rendering conventions (``app.extraction.providers``, documented there and
restated here so this module does not reach into another module's internals):

- a ``tEXt`` chunk with keyword ``sos-fake-extraction`` holding compact ASCII JSON
  ``{"size": [w, h], "rows": [...], "spans": [...]}``;
- every span box ``[left, top, right, bottom]`` (pixels, right/bottom exclusive) filled with
  grey ink ``(90, 90, 90)`` on white; the fake provider reads a span only while its box is not
  entirely black (so a redacted number disappears on re-read);
- row cells ``{"value", "confidence", "bbox"}`` with ``bbox = (x, y, w, h)`` as fractions of
  the page.

Rows are the register values of seeded students (one section per page, by roll number), so a
confirmed row matches an existing record. The first page of the first batch also carries an
``aadhaar`` cell and span with an Aadhaar-LIKE number from
:func:`app.devtools.fake_ids.invalid_aadhaar_like`: it FAILS the Verhoeff check, is never a
real Aadhaar number and is never written to the database by the seeder (it lives only in the
image object in local SeaweedFS). Dedicated PRV-016 tests that need a Verhoeff-valid number
build it inside the test process (docs/12 §3).
"""

from __future__ import annotations

import io
import json
import random
from dataclasses import dataclass
from typing import Any, Final

from PIL import Image, ImageDraw, PngImagePlugin

from app.devtools.fake_ids import invalid_aadhaar_like
from app.devtools.students import REG, SchoolStudents, StudentSpec

FAKE_SCRIPT_KEYWORD: Final = "sos-fake-extraction"
INK: Final = (90, 90, 90)
PAPER: Final = (255, 255, 255)
WIDTH: Final = 1400
ROW_HEIGHT: Final = 60
TOP: Final = 120
MARGIN: Final = 20
ROWS_PER_PAGE: Final = 10
COLUMNS: Final = ("admission_no", "admission_date", "full_name", "gender", "dob", "father_name",
                  "mother_name")  # fmt: skip
COLUMN_WIDTHS: Final = (130, 150, 280, 90, 140, 280, 280)  # sum 1350 < WIDTH - 2 * MARGIN
AADHAAR_COLUMN: Final = "aadhaar"


@dataclass(frozen=True, slots=True)
class RegisterPage:
    batch_no: int
    page_no: int
    section: tuple[str, str]
    admission_nos: tuple[str, ...]
    has_aadhaar_like: bool
    png: bytes

    @property
    def title(self) -> str:
        klass, name = self.section
        batch, page = self.batch_no, self.page_no
        return f"Synthetic admission register {klass}-{name}, batch {batch} page {page}"

    @property
    def filename(self) -> str:
        return f"synthetic-register-b{self.batch_no}-p{self.page_no}.png"


def _script_png(script: dict[str, Any]) -> bytes:
    width, height = script["size"]
    page = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(page)
    for span in script["spans"]:
        left, top, right, bottom = span["box"]
        draw.rectangle((left, top, right - 1, bottom - 1), fill=INK)
    info = PngImagePlugin.PngInfo()
    info.add_text(FAKE_SCRIPT_KEYWORD, json.dumps(script, ensure_ascii=True, separators=(",", ":")))
    out = io.BytesIO()
    page.save(out, "PNG", pnginfo=info)
    return out.getvalue()


def _page_script(
    rng: random.Random, students: list[StudentSpec], *, aadhaar_like: str | None
) -> dict[str, Any]:
    widths = list(COLUMN_WIDTHS)
    columns = list(COLUMNS)
    if aadhaar_like is not None:
        columns.append(AADHAAR_COLUMN)
        widths = [w - 25 for w in widths] + [7 * 25]
    height = TOP + ROW_HEIGHT * (len(students) + 1) + MARGIN
    size = (WIDTH, height)
    spans: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    def box(col: int, line: int) -> list[int]:
        left = MARGIN + sum(widths[:col])
        top = TOP + line * ROW_HEIGHT
        return [left + 4, top + 12, left + widths[col] - 4, top + ROW_HEIGHT - 12]

    for col, name in enumerate(columns):
        spans.append({"text": name.replace("_", " ").upper(), "box": box(col, 0)})
    for line, student in enumerate(students, start=1):
        row: dict[str, Any] = {}
        for col, name in enumerate(columns):
            if name == AADHAAR_COLUMN:
                value = aadhaar_like if line == 1 else None
            else:
                value = student.value(name, REG)
                if value is not None and name in ("full_name", "father_name", "mother_name"):
                    value = value.upper()
            if value is None:
                continue
            b = box(col, line)
            spans.append({"text": value, "box": b})
            row[name] = {
                "value": value,
                "confidence": round(rng.uniform(0.55, 0.99), 3),
                "bbox": [
                    round(b[0] / size[0], 4),
                    round(b[1] / size[1], 4),
                    round((b[2] - b[0]) / size[0], 4),
                    round((b[3] - b[1]) / size[1], 4),
                ],
            }
        rows.append(row)
    return {"size": list(size), "rows": rows, "spans": spans}


def build_register_pages(
    school: SchoolStudents,
    *,
    dataset_version: str,
    seed: int,
    tenant_index: int,
    batches: int,
    pages_per_batch: int,
    rows_per_page: int = ROWS_PER_PAGE,
) -> list[RegisterPage]:
    """``batches x pages_per_batch`` pages, one section each (sections in plan order)."""
    rng = random.Random(f"{dataset_version}:{seed}:tenant:{tenant_index}:register-pages")  # noqa: S311
    by_section: dict[tuple[str, str], list[StudentSpec]] = {}
    for s in school.students:
        if s.value("full_name", REG) is not None and s.value("admission_no", REG) is not None:
            by_section.setdefault(s.section, []).append(s)
    sections = [k for k, v in by_section.items() if v]
    pages: list[RegisterPage] = []
    for n in range(batches * pages_per_batch):
        if n >= len(sections):
            break
        section = sections[n * max(len(sections) // (batches * pages_per_batch), 1) % len(sections)]
        chosen = sorted(by_section[section], key=lambda s: int(s.roll_no))[:rows_per_page]
        prv016 = n == 0
        aadhaar_like = invalid_aadhaar_like(rng) if prv016 else None
        grouped = (
            f"{aadhaar_like[:4]} {aadhaar_like[4:8]} {aadhaar_like[8:]}" if aadhaar_like else None
        )
        script = _page_script(rng, chosen, aadhaar_like=grouped)
        pages.append(
            RegisterPage(
                batch_no=n // pages_per_batch + 1,
                page_no=n % pages_per_batch + 1,
                section=section,
                admission_nos=tuple(s.admission_no for s in chosen),
                has_aadhaar_like=prv016,
                png=_script_png(script),
            )
        )
    return pages
