"""Judge calibration items (``judge_calibration.jsonl``; FA-07, FR-KB-053; docs/06 §13.11).

Built from the synthetic corpus: for each dated document line, claims and answers whose label
follows from how they were made. Faithfulness: the line itself and its first clause (supported);
the line with a date moved, the line plus a figure no passage writes, and another document's line
(not supported). Correctness: the line answering a question whose reference is its date (correct);
a moved date, another document's line and "not found" (incorrect). Labels by construction stand in
for person labels until a person labels a sample (docs/06 §13.11 runbook); no real data.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from sos_evals.judge import CalibrationItem
from sos_evals.schema import CorpusItem

_DATE: Final = re.compile(r"(?<!\d)(\d{2})/(\d{2})/(\d{4})(?!\d)")
DOCS_PER_LOCALE: Final = {"en": 11, "te": 2}
NOT_FOUND: Final = "Not found in school records you can access."


def _lead(item: CorpusItem) -> str:
    return item.content.split("\n", 1)[0].strip()


def _date(text: str) -> re.Match[str]:
    match = _DATE.search(text)
    if match is None:
        raise ValueError("a calibration line needs a date")
    return match


def _shift(text: str) -> str:
    """The first date moved by one day (28 wraps to 01)."""
    match = _date(text)
    day = int(match.group(1))
    moved = f"{(day % 28) + 1:02d}/{match.group(2)}/{match.group(3)}"
    return text[: match.start()] + moved + text[match.end() :]


def _clause(text: str) -> str:
    match = _date(text)
    return text[: match.end()].rstrip(" ,") + "."


def build(corpus: Sequence[CorpusItem]) -> list[CalibrationItem]:
    picked: list[CorpusItem] = []
    dates: set[str] = set()
    for locale, wanted in DOCS_PER_LOCALE.items():
        count = 0
        for item in sorted(corpus, key=lambda c: c.source):
            if item.kind != "document" or item.locale != locale or item.injection_canaries:
                continue
            match = _DATE.search(_lead(item))
            if match is None or match.group() in dates:
                continue
            dates.add(match.group())
            picked.append(item)
            count += 1
            if count == wanted:
                break
    rows: list[dict[str, object]] = []
    for index, item in enumerate(picked):
        lead = _lead(item)
        other = _lead(picked[(index + 1) % len(picked)])
        passage = f"{item.title}\n{item.content}"
        date = _date(lead)
        question = f"What does the notice '{item.title}' say about the date?"
        rows += [
            {"kind": "faithfulness", "perturbation": "verbatim", "text": lead, "label": True},
            {"kind": "faithfulness", "perturbation": "first_clause", "text": _clause(lead)},
            {"kind": "faithfulness", "perturbation": "date_shift", "text": _shift(lead)},
            {
                "kind": "faithfulness",
                "perturbation": "added_figure",
                "text": f"{lead.rstrip('.')}; the late fee is Rs 4,250.",
            },
            {"kind": "faithfulness", "perturbation": "other_passage", "text": other},
            {"kind": "correctness", "perturbation": "verbatim", "text": lead},
            {"kind": "correctness", "perturbation": "date_shift", "text": _shift(lead)},
            {"kind": "correctness", "perturbation": "other_document", "text": other},
            {"kind": "correctness", "perturbation": "not_found", "text": NOT_FOUND},
        ]
        for row in rows[-9:]:
            row["label"] = row["perturbation"] in ("verbatim", "first_clause")
            if row["kind"] == "faithfulness":
                row["passages"] = [passage]
            else:
                row["question"] = question
                row["reference"] = date.group()
    return [
        CalibrationItem.model_validate({"id": f"judge-{n:03d}", **row})
        for n, row in enumerate(rows, start=1)
    ]


__all__ = ["build"]
