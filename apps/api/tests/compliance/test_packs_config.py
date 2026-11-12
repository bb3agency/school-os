"""Inspection pack definitions and the index page (FC-07, FO-12; FR-COMP-024, FR-COMP-025,
FR-COMP-038..FR-COMP-040). Pure: no database."""

from __future__ import annotations

import copy
import datetime as dt
import re
import uuid
from importlib import resources
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.compliance import packs_config as pc
from app.compliance import packs_page as page
from app.compliance.packs_schemas import (
    InspectionPackCountsOut,
    InspectionPackDetailOut,
    InspectionPackItemOut,
)


def _raw() -> dict[str, Any]:
    text = resources.files("app.compliance").joinpath("packs_catalogue.yaml").read_text("utf-8")
    raw: dict[str, Any] = yaml.safe_load(text)
    return raw


def test_FR_COMP_024_pack_file_loads_unverified_with_a_source() -> None:
    cat = pc.pack_catalogue()
    assert cat.verified is False
    assert "secondary" in cat.source
    assert {"deo_inspection", "apsermc_inspection", "cbse_inspection", "fire_inspection"} <= set(
        cat.packs
    )
    sources = {i.source for p in cat.packs.values() for i in p.items}
    assert sources == set(pc.SOURCES)
    # Evidence SchoolOS does not hold yet names the feature that would add it.
    features = {i.feature for p in cat.packs.values() for i in p.items if i.feature}
    assert features == {"FC-04", "FC-05", "FC-09"}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r["packs"]["fire_inspection"]["items"][0].update(kind="no_such"), "unknown"),
        (lambda r: r["packs"]["fire_inspection"]["items"][0].update(kind="other"), "unknown"),
        (lambda r: r["packs"]["fire_inspection"]["items"][0].pop("kind"), "kind exactly"),
        (lambda r: r["packs"]["fire_inspection"]["items"][3].pop("feature"), "feature exactly"),
        (lambda r: r["packs"]["fire_inspection"]["items"][3].update(feature="X"), "feature id"),
        (lambda r: r["packs"]["fire_inspection"].update(boards=["ICSE"]), "unknown boards"),
        (
            lambda r: r["packs"]["fire_inspection"]["items"].append(
                copy.deepcopy(r["packs"]["fire_inspection"]["items"][0])
            ),
            "repeat",
        ),
        (lambda r: r["packs"].update({"Bad-Key": r["packs"]["fire_inspection"]}), "pack key"),
    ],
)
def test_FR_COMP_024_bad_pack_file_is_refused(mutate: Any, message: str) -> None:
    raw = _raw()
    mutate(raw)
    with pytest.raises(ValidationError, match=message):
        pc.parse(raw)


def test_FR_COMP_025_packs_and_items_follow_the_school_boards() -> None:
    cat = pc.pack_catalogue()
    ap = cat.packs_for(["BSEAP"])
    cbse = cat.packs_for(["CBSE"])
    assert "cbse_inspection" not in ap
    assert "cbse_inspection" in cbse
    apsermc_ap = {i.key for i in ap["apsermc_inspection"].items_for(["BSEAP"])}
    apsermc_cbse = {i.key for i in cbse["apsermc_inspection"].items_for(["CBSE"])}
    assert "state_recognition" in apsermc_ap
    assert "cbse_affiliation" not in apsermc_ap
    assert "cbse_affiliation" in apsermc_cbse
    assert "state_recognition" not in apsermc_cbse


def _detail(items: list[InspectionPackItemOut]) -> InspectionPackDetailOut:
    return InspectionPackDetailOut(
        id=uuid.uuid4(),
        pack_key="fire_inspection",
        pack_label="Fire department <inspection>",
        authority="State fire services department",
        pack_version=1,
        verified=False,
        academic_year_id=None,
        academic_year_label="2026-27",
        visit_on=dt.date(2026, 12, 1),
        registers=[],
        counts=InspectionPackCountsOut(ready=1, missing=1, not_tracked=1),
        generated_at=dt.datetime(2026, 11, 12, 6, 0, tzinfo=dt.UTC),
        generated_by_name="Office",
        items=items,
    )


def test_FR_COMP_038_index_page_escapes_and_has_only_the_hashed_style() -> None:
    items = [
        InspectionPackItemOut(
            key="fire_noc",
            label="Fire safety certificate",
            source="certificate",
            state="ready",
            fix_by="Office admin",
            kind="fire_noc",
            valid_until=dt.date(2030, 1, 31),
            has_copy=True,
        ),
        InspectionPackItemOut(
            key="occupancy",
            label="Occupancy 2345 6789 0124",
            source="certificate",
            state="missing",
            fix_by="Correspondent",
        ),
        InspectionPackItemOut(
            key="safety_drills",
            label="Fire and evacuation drills",
            source="not_tracked",
            state="not_tracked",
            fix_by="Office admin",
            feature="FC-09",
        ),
    ]
    html = page.render(
        page.IndexData(
            school_name="Synthetic <School>",
            school_address="1 Main Road",
            pack=_detail(items),
            printed_at=dt.datetime(2026, 11, 12, 7, 0, tzinfo=dt.UTC),
        )
    )
    assert "Synthetic &lt;School&gt;" in html
    assert "Fire department &lt;inspection&gt;" in html
    assert "<script" not in html.lower()
    assert "<a " not in html.lower()
    assert "<img" not in html.lower()
    assert "Valid until 31 Jan 2030; copy on file" in html
    assert "Not tracked in SchoolOS" in html
    assert "Planned in SchoolOS (FC-09)" in html
    assert "not yet confirmed" in html  # verified: false
    assert "01 Dec 2026" in html
    assert "2345 6789 0124" not in html  # Verhoeff-valid 12 digits are masked (invariant 4)
    styles = re.findall(r"<style>(.*?)</style>", html, flags=re.S)
    assert styles == [page.STYLE]
    assert page.STYLE_CSP.startswith("default-src 'none'; style-src 'sha256-")


def test_FR_COMP_039_details_cover_every_source() -> None:
    base: dict[str, Any] = {"key": "k1", "label": "Item", "fix_by": "Principal"}
    cases: list[tuple[dict[str, Any], str]] = [
        ({"source": "register", "state": "left_out", "included": False}, "Not chosen"),
        ({"source": "register", "state": "ready", "included": True}, "Chosen"),
        ({"source": "disclosure", "state": "missing"}, "No page published"),
        (
            {
                "source": "disclosure",
                "state": "ready",
                "published_at": dt.datetime(2026, 10, 1, 4, tzinfo=dt.UTC),
            },
            "Published 01 Oct 2026",
        ),
        ({"source": "privacy_notice", "state": "missing"}, "No notice recorded"),
        ({"source": "dpdp_readiness", "state": "incomplete", "done": 3, "applicable": 8}, "3 of 8"),
        ({"source": "certificate", "state": "no_copy", "has_copy": False}, "No expiry; no copy"),
    ]
    for extra, expected in cases:
        assert expected in page.details(InspectionPackItemOut(**base, **extra)), extra
