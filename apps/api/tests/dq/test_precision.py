"""Precision of the blocker/high rules on a labelled synthetic set (FR-DQ exit criterion:
precision >= 0.95 for blocker and high findings; docs/12 §5 "precision/recall per rule").

Names come from ``app.devtools.names`` (synthetic AP names and their register-style variants);
every student carries a label saying whether the compared value is a real problem. A prediction
is a finding of severity blocker or high. The set is generated from fixed seeds, so the numbers
are reproducible; synthetic data only.
"""

from __future__ import annotations

import random
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.devtools.names import VariantClass, generate_name, variants
from app.dq.checks import build_checks, evaluate
from app.dq.rules import Finding, Severity, load_rules

CS = sys.modules["sos_test_dq_checks_support"]
REG = "admission_register"
AAD = "aadhaar_as_printed"
SERIOUS = {Severity.BLOCKER, Severity.HIGH}
MIN_PRECISION = 0.95
CHECKS = build_checks(load_rules())
LETTERS = "bcdfgklmnprstv"


@dataclass(frozen=True)
class Labelled:
    student_id: uuid.UUID
    problem: bool
    kind: str


def _typo(rng: random.Random, text: str) -> str:
    """One letter replaced inside the longest word (a keyboard slip)."""
    words = text.split()
    i = max(range(len(words)), key=lambda k: len(words[k]))
    word = words[i]
    pos = rng.randrange(2, len(word) - 1)
    choices = [c for c in LETTERS if c != word[pos].lower()]
    words[i] = word[:pos] + rng.choice(choices) + word[pos + 1 :]
    return " ".join(words)


def _other_name(rng: random.Random, name: Any) -> str:
    while True:
        other = generate_name(rng, gender=name.gender)
        if other.surname != name.surname and other.given != name.given:
            return str(other.canonical)


def _precision(
    labelled: list[Labelled], findings: list[Finding], rule_id: str
) -> tuple[float, int, float]:
    flagged = {f.student_id for f in findings if f.rule_id == rule_id and f.severity in SERIOUS}
    truth = {x.student_id: x.problem for x in labelled}
    tp = sum(1 for s in flagged if truth[s])
    positives = sum(1 for x in labelled if x.problem)
    precision = tp / len(flagged) if flagged else 1.0
    recall = tp / positives if positives else 1.0
    return precision, len(flagged), recall


def _dataset(
    seed: int, build: Callable[[random.Random, Any], tuple[dict[tuple[str, str], str], str, bool]]
) -> tuple[list[Any], list[Labelled]]:
    rng = random.Random(seed)
    students, labels = [], []
    for _ in range(600):
        name = generate_name(rng)
        values, kind, problem = build(rng, name)
        facts = CS.facts(values=values)
        students.append(facts)
        labels.append(Labelled(facts.student_id, problem, kind))
    return students, labels


def _base(name: Any, dob: str = "2011-08-19") -> dict[tuple[str, str], str]:
    return {
        ("full_name", REG): name.canonical,
        ("dob", REG): dob,
        ("gender", REG): "female" if name.gender == "f" else "male",
        ("father_name", REG): "Synthetic Father",
        ("mother_name", REG): "Synthetic Mother",
    }


def _spread_dob(rng: random.Random) -> str:
    return f"2011-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def _aadhaar_name(rng: random.Random, name: Any) -> tuple[dict[tuple[str, str], str], str, bool]:
    values = _base(name, _spread_dob(rng))
    roll = rng.random()
    options = variants(name)
    if roll < 0.2:
        value, kind, problem = name.canonical, "same", False
    elif roll < 0.55:
        pick = rng.choice([v for v in options if v.variant_class is not VariantClass.SCRIPT])
        value, kind, problem = pick.text, f"variant:{pick.variant_class}", False
    elif roll < 0.6 and any(v.variant_class is VariantClass.SCRIPT for v in options):
        value = next(v.text for v in options if v.variant_class is VariantClass.SCRIPT)
        kind, problem = "script", False
    elif roll < 0.8:
        value, kind, problem = _typo(rng, name.canonical), "typo", True
    else:
        value, kind, problem = _other_name(rng, name), "different", True
    values[("aadhaar_name_as_printed", AAD)] = value
    return values, kind, problem


def test_FR_DQ_003_DQ_001_precision_on_labelled_names() -> None:
    students, labels = _dataset(501, _aadhaar_name)
    findings = evaluate(CS.context(students), CHECKS)
    precision, flagged, recall = _precision(labels, findings, "DQ-001")
    assert flagged > 150
    assert precision >= MIN_PRECISION, (precision, flagged)
    # Different people are always caught (blocker); typos mostly (some become VARIANT).
    by_id = {f.student_id: f for f in findings if f.rule_id == "DQ-001"}
    different = [x for x in labels if x.kind == "different"]
    caught = sum(
        1
        for x in different
        if by_id.get(x.student_id) is not None and by_id[x.student_id].severity is Severity.BLOCKER
    )
    assert caught / len(different) >= MIN_PRECISION
    assert recall >= 0.8, recall


def _aadhaar_dob_gender(
    rng: random.Random, name: Any
) -> tuple[dict[tuple[str, str], str], str, bool]:
    values = _base(name)
    dob_differs = rng.random() < 0.3
    values[("aadhaar_dob_as_printed", AAD)] = "2011-09-19" if dob_differs else "2011-08-19"
    values[("aadhaar_gender_as_printed", AAD)] = values[("gender", REG)].upper()
    return values, "dob" if dob_differs else "same", dob_differs


def test_DQ_002_DQ_003_precision_on_labelled_values() -> None:
    """Also the worst case for DQ-008: 600 records share one date of birth (blocking keys)."""
    students, labels = _dataset(502, _aadhaar_dob_gender)
    findings = evaluate(CS.context(students), CHECKS)
    precision, flagged, recall = _precision(labels, findings, "DQ-002")
    assert flagged > 100
    assert (precision, recall) == (1.0, 1.0)
    assert _precision(labels, findings, "DQ-003")[1] == 0  # only letter case differs


def _board(rng: random.Random, name: Any) -> tuple[dict[tuple[str, str], str], str, bool]:
    values = _base(name, _spread_dob(rng))
    roll = rng.random()
    if roll < 0.5:
        board, kind = name.canonical.upper(), "same"
    elif roll < 0.7:
        board, kind = rng.choice(variants(name)).text, "variant"
    elif roll < 0.85:
        board, kind = _typo(rng, name.canonical), "typo"
    else:
        board, kind = _other_name(rng, name), "different"
    values[("full_name", "board_registration")] = board
    # A Telugu-script board value is the same name (EXACT after transliteration).
    problem = kind != "same" and not any(
        v.text == board and v.variant_class is VariantClass.SCRIPT for v in variants(name)
    )
    return values, kind, problem


def test_DQ_010_precision_on_labelled_board_names() -> None:
    students, labels = _dataset(510, _board)
    findings = evaluate(CS.context(students), CHECKS)
    precision, flagged, recall = _precision(labels, findings, "DQ-010")
    assert flagged > 150
    assert precision >= MIN_PRECISION, precision
    assert recall >= MIN_PRECISION, recall


def test_DQ_008_precision_on_labelled_pairs() -> None:
    rng = random.Random(508)
    students, truth = [], {}
    for i in range(300):
        name = generate_name(rng)
        father = generate_name(rng, gender="m").canonical
        mother = generate_name(rng, gender="f").canonical
        dob = f"20{10 + i % 5}-0{1 + i % 9}-{10 + i % 18}"
        base = {
            ("full_name", REG): name.canonical,
            ("dob", REG): dob,
            ("father_name", REG): father,
            ("mother_name", REG): mother,
        }
        first = CS.facts(values=base)
        kind = ("duplicate", "twin", "namesake", "unrelated")[i % 4]
        other = dict(base)
        if kind == "duplicate":
            other[("full_name", REG)] = rng.choice(
                [v.text for v in variants(name) if v.variant_class is not VariantClass.SCRIPT]
            )
        elif kind == "twin":
            other[("full_name", REG)] = f"{name.surname} {_other_name(rng, name).split()[-1]}"
        elif kind == "namesake":
            other[("father_name", REG)] = _other_name(rng, generate_name(rng, gender="m"))
            other[("mother_name", REG)] = _other_name(rng, generate_name(rng, gender="f"))
        else:
            other[("full_name", REG)] = _other_name(rng, name)
        second = CS.facts(values=other)
        students += [first, second]
        truth[first.student_id] = truth[second.student_id] = kind == "duplicate"
    findings = evaluate(CS.context(students), CHECKS)
    flagged = {f.student_id for f in findings if f.rule_id == "DQ-008"}
    tp = sum(1 for s in flagged if truth[s])
    assert len(flagged) > 100
    assert tp / len(flagged) >= MIN_PRECISION
    assert tp / sum(truth.values()) >= MIN_PRECISION
