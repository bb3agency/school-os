"""Accountant and management report routes for the authorization matrix (FM-04; FR-FEE-061..075;
synthetic data only).

Loaded by path from ``tests/security/test_authz_matrix.py``. The reports are read-only and take no
object id in the path (BOLA has nothing to add; the other-school year is covered in
``tests/fees/test_period_reports.py``). Fees are switched on for school A only around each call
(the paths start with ``/api/v1/fees/``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine

BASE = "/api/v1/fees/reports"
FY = "2026-27"

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per report route (school A, default periods)."""

    def g(path: str) -> Builder:
        return lambda w, r, a: (path, None, {})

    return {
        ("GET", f"{BASE}/collections"): g(f"{BASE}/collections"),
        ("GET", f"{BASE}/collections/print"): g(f"{BASE}/collections/print"),
        ("GET", f"{BASE}/collections/download"): g(f"{BASE}/collections/download"),
        ("GET", f"{BASE}/dcb"): g(f"{BASE}/dcb"),
        ("GET", f"{BASE}/dcb/download"): g(f"{BASE}/dcb/download"),
        ("GET", f"{BASE}/registers"): g(f"{BASE}/registers"),
        ("GET", f"{BASE}/registers/download"): g(f"{BASE}/registers/download?kind=refunds"),
        ("GET", f"{BASE}/ageing"): g(f"{BASE}/ageing"),
        ("GET", f"{BASE}/ageing/download"): g(f"{BASE}/ageing/download?format=xlsx"),
        ("GET", f"{BASE}/receipt-series"): g(f"{BASE}/receipt-series"),
        ("GET", f"{BASE}/year-end-pack"): g(f"{BASE}/year-end-pack?financial_year={FY}"),
    }


__all__ = ["matrix_specs"]
