"""Every contributor in app/privacy/subjects.yaml exposes the subject protocol (FR-DPDP-008,
NFR-PRV-007; ADR-0048 decisions 6 and 8; protocol in app/core/subjects.py).

Self-contained: reads the registry YAML directly, imports ``app.<module>.service`` and inspects
the public functions' signatures. No database.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any

import pytest
import yaml

PRIVACY_DIR = Path(__file__).resolve().parents[2] / "app" / "privacy"
# The privacy module's own contributor is built and tested with the privacy service.
SKIP = {"privacy"}


def _contributors() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load((PRIVACY_DIR / "subjects.yaml").read_text(encoding="utf-8"))
    contributors: dict[str, dict[str, Any]] = data["contributors"]
    return {name: spec for name, spec in contributors.items() if name not in SKIP}


CONTRIBUTORS = _contributors()


def _params(fn: object) -> list[inspect.Parameter]:
    assert callable(fn)
    return list(inspect.signature(fn).parameters.values())


@pytest.mark.parametrize("module", sorted(CONTRIBUTORS))
def test_FR_DPDP_008_contributor_exposes_subject_report(module: str) -> None:
    service = importlib.import_module(f"app.{module}.service")
    fn = getattr(service, "subject_report", None)
    assert fn is not None, f"app.{module}.service has no subject_report"
    assert [p.name for p in _params(fn)] == ["session", "subject"]


@pytest.mark.parametrize("module", sorted(CONTRIBUTORS))
def test_FR_DPDP_012_eraser_exposed_iff_registered(module: str) -> None:
    service = importlib.import_module(f"app.{module}.service")
    fn = getattr(service, "erase_for_subject", None)
    if not CONTRIBUTORS[module].get("eraser", False):
        assert fn is None, f"app.{module}.service has erase_for_subject but eraser is false"
        return
    assert fn is not None, f"app.{module}.service has no erase_for_subject"
    params = _params(fn)
    assert [p.name for p in params] == ["session", "subject", "class_keys", "dry_run"]
    assert params[3].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("module", sorted(CONTRIBUTORS))
def test_FR_DPDP_008_contributor_classes_are_its_own(module: str) -> None:
    classes = yaml.safe_load((PRIVACY_DIR / "record_classes.yaml").read_text(encoding="utf-8"))
    for key in CONTRIBUTORS[module]["classes"]:
        assert key in classes["classes"], f"{module}: unknown record class {key}"
        assert classes["classes"][key]["owner"] == module, f"{module}: {key} is owned elsewhere"
