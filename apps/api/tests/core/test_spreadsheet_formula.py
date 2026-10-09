"""Formula neutralisation sees past leading whitespace and full-width signs (SEC-017; audit
2026-10-04 data-layer hardening note 4). Pure: no database."""

from __future__ import annotations

import pytest

from app.core.spreadsheet import looks_like_formula, neutralise_formula, safe_cell, starts_formula

HIDDEN_FORMULAS = [
    " =1+1",
    '   =HYPERLINK("http://evil.test","x")',
    "\u00a0=1+1",  # no-break space
    "\u3000=1+1",  # ideographic space
    "\u2003+SUM(A1:A2)",  # em space
    "\n=1+1",
    " \t@SUM(A1)",
    "\u200b=1+1",  # zero-width space
    "\ufeff=1+1",  # byte order mark
    "\uff1d1+1",  # full-width equals
    "\uff0bSUM(A1:A2)",
    "\uff0d2+3",
    "\uff20SUM(A1)",
    "  \uff1dcmd|' /C calc'!A0",
    "\ufe661+1",  # small equals sign
]


@pytest.mark.parametrize("value", HIDDEN_FORMULAS)
def test_DL_hardening_4_hidden_formula_is_neutralised(value: str) -> None:
    assert starts_formula(value)
    assert neutralise_formula(value) == "'" + value
    assert safe_cell(value).startswith("'")


@pytest.mark.parametrize("value", ["\uff1d1+1", " =1+1", "\u3000@x", "\uff0bSUM(A1)"])
def test_DL_hardening_4_reading_flags_hidden_formulas(value: str) -> None:
    assert looks_like_formula(value)


@pytest.mark.parametrize(
    "value",
    [
        "Venkata Sai",
        " K. Lakshmi",
        "\uff19A",
        "  ",
        "",
        "2012-03-14",
        "Rs. 1,200",
        "\uff15\uff10\uff10",
    ],
)
def test_DL_hardening_4_ordinary_values_are_unchanged(value: str) -> None:
    assert not starts_formula(value)
    assert neutralise_formula(value) == value
