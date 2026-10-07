"""Formula neutralisation sees past leading whitespace and full-width signs (SEC-017; audit
2026-10-04 data-layer hardening note 4). Pure: no database."""

from __future__ import annotations

import pytest

from app.core.spreadsheet import looks_like_formula, neutralise_formula, safe_cell, starts_formula

HIDDEN_FORMULAS = [
    " =1+1",
    "   =HYPERLINK(\"http://evil.test\",\"x\")",
    " =1+1",  # no-break space
    "　=1+1",  # ideographic space
    " +SUM(A1:A2)",  # em space
    "\n=1+1",
    " \t@SUM(A1)",
    "​=1+1",  # zero-width space
    "﻿=1+1",  # byte order mark
    "＝1+1",  # full-width equals
    "＋SUM(A1:A2)",
    "－2+3",
    "＠SUM(A1)",
    "  ＝cmd|' /C calc'!A0",
    "﹦1+1",  # small equals sign
]


@pytest.mark.parametrize("value", HIDDEN_FORMULAS)
def test_DL_hardening_4_hidden_formula_is_neutralised(value: str) -> None:
    assert starts_formula(value)
    assert neutralise_formula(value) == "'" + value
    assert safe_cell(value).startswith("'")


@pytest.mark.parametrize("value", ["＝1+1", " =1+1", "　@x", "＋SUM(A1)"])
def test_DL_hardening_4_reading_flags_hidden_formulas(value: str) -> None:
    assert looks_like_formula(value)


@pytest.mark.parametrize(
    "value",
    ["Venkata Sai", " K. Lakshmi", "９A", "  ", "", "2012-03-14", "Rs. 1,200", "５００"],
)
def test_DL_hardening_4_ordinary_values_are_unchanged(value: str) -> None:
    assert not starts_formula(value)
    assert neutralise_formula(value) == value
