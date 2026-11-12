"""Knowledge gaps without a database (FA-06): redaction of stored questions, grouping keys,
the config, the close body and the catalogs (FR-KB-081, FR-KB-082, FR-KB-091, FR-KB-093,
FR-KB-094). Synthetic text only."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.admin.config import load_retention
from app.core.redaction import verhoeff_check_digit
from app.knowledge import gaps, tasks
from app.knowledge.config.gaps import load_gaps_config
from app.knowledge.gap_schemas import KnowledgeGapCloseIn
from app.privacy import config as privacy_config
from app.privacy.holds_config import load_config as load_holds


def _aadhaar_like() -> str:
    base = "23456789012"[:11]
    return base + verhoeff_check_digit(base)


def test_FR_KB_082_stored_question_is_redacted_before_it_is_kept() -> None:
    number = _aadhaar_like()
    text = (
        f"Did Synthetica's parent (9876543210, parent@example.test) give Aadhaar {number} "
        "for admission no 20451 in roll 17?"
    )
    out = gaps.gap_question(text)
    for secret in (number, "9876543210", "parent@example.test", "20451"):
        assert secret not in out
    assert "17" in out  # a two-digit number (a class, a roll) stays readable
    assert "[number]" in out
    assert "[redacted-email]" in out


def test_FR_KB_082_long_questions_are_cut() -> None:
    cfg = load_gaps_config()
    out = gaps.gap_question("where is the circular " * 100)
    assert len(out) <= cfg.max_question_chars
    assert out.endswith("…")


def test_FR_KB_083_grouping_ignores_case_spacing_and_punctuation() -> None:
    one = gaps.gap_key(gaps.gap_question("When is the PTM for class 9A?"))
    two = gaps.gap_key(gaps.gap_question("  when is the ptm for   CLASS 9A "))
    assert one == two == "when is the ptm for class 9a"
    assert gaps.gap_key(gaps.gap_question("?!")) == ""


def test_FR_KB_091_retention_is_short_configurable_and_held() -> None:
    cfg = load_gaps_config()
    assert cfg.retention_days == 90
    assert cfg.verified is False
    category = load_retention().categories[gaps.RETENTION_CATEGORY]
    assert category.default_days == cfg.retention_days
    assert category.max_days == cfg.retention_days  # may only be shortened
    assert category.configurable
    assert category.enforced_by == tasks.PURGE_GAPS_TASK == "knowledge.purge_gaps"
    held = load_holds().retention_jobs["knowledge.purge_gaps"]
    assert held.record_class == "ask_gaps"
    assert "ask_gaps" in privacy_config.record_classes().classes


def test_FR_KB_093_the_jobs_are_scheduled() -> None:
    beat = tasks.beat_schedule()
    assert beat["knowledge-collect-gaps"]["task"] == tasks.COLLECT_GAPS_TASK
    assert beat["knowledge-purge-gaps"]["task"] == tasks.PURGE_GAPS_TASK


@pytest.mark.parametrize(
    "body",
    [
        {"resolution": "verified_answer"},
        {"resolution": "document"},
        {"resolution": "dismissed", "document_id": str(uuid.uuid4())},
        {"resolution": "document", "verified_answer_id": str(uuid.uuid4())},
    ],
)
def test_FR_KB_088_close_body_names_exactly_its_link(body: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        KnowledgeGapCloseIn.model_validate(body)


def test_FR_KB_094_the_question_column_is_in_the_key_rotation_census() -> None:
    import app.knowledge.service  # noqa: F401  (registers the re-encryptors)
    from app.students import rotation

    assert ("kb.knowledge_gaps", "question_ciphertext") in rotation.CIPHERTEXT_COLUMNS
    assert "kb_gaps" in rotation.reencryptor_names()
