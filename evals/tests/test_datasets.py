"""Synthetic datasets: schema, freshness and the properties that keep the gates meaningful."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from sos_evals import acl, datasets, generator
from sos_evals.acl import visible
from sos_evals.schema import CATEGORIES, Acl, Asker, EvalItem

DATA = datasets.load()


def test_committed_datasets_match_the_generator() -> None:
    """Regenerate with `uv run python -m sos_evals generate` after changing generator.py."""
    assert generator.stale_files(datasets.DATASETS_DIR) == []


def test_every_category_has_questions_and_a_fast_subset() -> None:
    for category in CATEGORIES:
        items = [i for i in DATA.items if i.category == category]
        assert items, category
        assert any(i.fast for i in items), category


def test_FR_KB_010_SEC_018_SEC_019_hard_gate_items_all_run_in_the_fast_subset() -> None:
    for item in DATA.items:
        if item.leakage_probe or item.injection or item.category in {"permissions", "adversarial"}:
            assert item.fast, item.id


def _probe_kind(item: EvalItem, source: str) -> str:
    target, asker = DATA.corpus[source], item.asker
    rules = (
        ("cross-tenant", target.tenant != asker.tenant),
        ("record-scope", target.kind == "record"),
        (
            "c3",
            target.sensitivity == "C3" and not acl.has(asker, acl.STUDENT_READ_SENSITIVE),
        ),
        ("membership", bool(target.acl.members)),
        ("empty-acl", target.acl == Acl()),
        (
            "cross-section",
            bool(target.acl.sections) and not set(target.acl.sections) & set(asker.sections),
        ),
        ("cross-class", bool(target.acl.classes) and not target.acl.roles),
    )
    return next((name for name, applies in rules if applies), "cross-role")


def test_FR_KB_010_leakage_probes_cover_every_visibility_boundary() -> None:
    kinds = {_probe_kind(item, s) for item in DATA.items for s in item.probe_sources}
    assert kinds == {
        "cross-tenant",
        "record-scope",
        "c3",
        "membership",
        "empty-acl",
        "cross-section",
        "cross-class",
        "cross-role",
    }


def test_FR_KB_010_probes_come_from_every_kind_of_asker() -> None:
    """School-wide readers, scoped teachers and ACL managers are all probed."""
    probers = {i.asker.role for i in DATA.items if i.leakage_probe}
    assert {"office_staff", "accountant", "class_teacher", "teacher", "principal"} <= probers


def test_corpus_size_follows_docs_06_section_13_1() -> None:
    documents = [c for c in DATA.corpus.values() if c.kind == "document"]
    records = [c for c in DATA.corpus.values() if c.kind == "record"]
    assert len(documents) >= 300
    assert len({c.source.split("/v")[0] for c in documents}) >= 250  # distinct documents
    assert {c.locale for c in documents} == {"en", "te", "mixed"}
    assert {c.doc_type for c in documents} >= {
        "circular",
        "minutes",
        "policy",
        "letter",
        "register_scan",
        "timetable",
        "report",
    }
    assert sum(c.sensitivity == "C3" for c in documents) >= 5
    assert sum(bool(c.injection_canaries) for c in documents) >= 10
    assert sum(not c.is_latest for c in documents) >= 10
    assert any(c.acl.members for c in documents)
    assert any(c.acl == Acl() for c in documents)
    assert len({c.source.split("/field/")[0] for c in records}) >= 30  # students
    assert any("UDISE+ records a different" in c.content for c in records)  # mismatches


def test_question_sets_are_large_and_the_fast_subset_stays_small() -> None:
    assert len(DATA.items) >= 200
    assert len(DATA.select("fast")) <= len(DATA.items) // 3
    assert sum(i.category == "records" for i in DATA.items) >= 25
    record_questions = [
        i for i in DATA.items if any(DATA.corpus[s].kind == "record" for s in i.expected_sources)
    ]
    assert {i.locale for i in record_questions} == {"en", "te", "mixed"}


def test_every_asker_holds_kb_ask() -> None:
    assert all(acl.can_ask(i.asker) for i in DATA.items)


def test_FR_KB_010_every_probe_answer_exists_for_someone_who_may_see_it() -> None:
    """A probe is meaningful only if the forbidden answer is really in the corpus."""
    for item in DATA.items:
        for source in item.probe_sources:
            assert DATA.corpus[source].is_latest, item.id


def test_SEC_019_injection_items_cite_injected_documents_in_both_languages() -> None:
    injected = [i for i in DATA.items if i.injection]
    assert {i.locale for i in injected} >= {"en", "te"}
    for source in {s for i in injected for s in i.expected_sources}:
        assert DATA.corpus[source].injection_canaries


def test_FR_KB_006_questions_cover_english_telugu_and_code_mixed() -> None:
    assert {i.locale for i in DATA.items} == {"en", "te", "mixed"}


def test_temporal_questions_expect_the_latest_version_only() -> None:
    superseded = [c.source for c in DATA.corpus.values() if not c.is_latest]
    assert superseded
    for item in DATA.items:
        assert not set(item.expected_sources) & set(superseded), item.id


def test_invariant_4_and_11_no_twelve_digit_numbers_in_the_synthetic_data() -> None:
    twelve = re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")
    for path in datasets.dataset_files(datasets.DATASETS_DIR):
        assert not twelve.search(path.read_text(encoding="utf-8")), path.name


def test_source_uris_carry_no_names_or_values() -> None:
    """docs/06 §8: URIs never contain names or values."""
    for source in DATA.corpus:
        assert re.fullmatch(
            r"sos://(doc/[0-9a-f-]{36}/v\d+#p\d+|student/[0-9a-f-]{36}/field/[a-z_]+\?src=[a-z_]+)",
            source,
        ), source


def test_fast_suite_is_a_subset_of_full() -> None:
    fast, full = DATA.select("fast"), DATA.select("full")
    assert 0 < len(fast) < len(full)
    assert set(fast) <= set(full)


def _write(directory: Path) -> None:
    generator.write(directory)


def test_load_rejects_missing_files(tmp_path: Path) -> None:
    with pytest.raises(datasets.DatasetError, match="missing dataset files"):
        datasets.load(tmp_path)


def test_load_rejects_an_item_in_the_wrong_file(tmp_path: Path) -> None:
    _write(tmp_path)
    records = (tmp_path / "records.jsonl").read_text(encoding="utf-8")
    documents = tmp_path / "documents.jsonl"
    documents.write_text(documents.read_text(encoding="utf-8") + records, encoding="utf-8")
    with pytest.raises(datasets.DatasetError, match=r"is in documents.jsonl"):
        datasets.load(tmp_path)


def test_load_rejects_invalid_json(tmp_path: Path) -> None:
    _write(tmp_path)
    (tmp_path / "temporal.jsonl").write_text("{not json\n", encoding="utf-8")
    with pytest.raises(datasets.DatasetError, match=r"temporal.jsonl:1"):
        datasets.load(tmp_path)


def test_validate_rejects_a_probe_the_asker_can_see() -> None:
    item = next(i for i in DATA.items if i.leakage_probe)
    target = DATA.corpus[item.probe_sources[0]]
    insider = Asker(tenant=target.tenant, role="principal")  # sees every document and record
    assert visible(insider, target)
    bad = item.model_copy(update={"asker": insider})
    with pytest.raises(datasets.DatasetError, match="is visible to the asker"):
        datasets.validate(DATA.corpus, [bad])


def test_validate_rejects_an_expected_source_the_asker_cannot_retrieve() -> None:
    item = next(i for i in DATA.items if i.category == "temporal")
    old = next(c.source for c in DATA.corpus.values() if not c.is_latest)
    bad = item.model_copy(update={"expected_sources": (old,)})
    with pytest.raises(datasets.DatasetError, match="not retrievable"):
        datasets.validate(DATA.corpus, [bad])


def test_validate_rejects_duplicate_ids() -> None:
    item = DATA.items[0]
    with pytest.raises(datasets.DatasetError, match="duplicate item id"):
        datasets.validate(DATA.corpus, [item, item])


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"expect_refusal": True}, "refusal item has no expected sources"),
        ({"expected_sources": ()}, "answerable item needs expected sources"),
        ({"leakage_probe": True}, "leakage probe expects a refusal"),
        ({"id": "records-001", "category": "documents"}, "must start with the category"),
        ({"locale": "hi"}, "locale"),
        ({"asker": {"tenant": "t", "role": "parent"}}, "role"),
    ],
)
def test_eval_item_schema_rejects_inconsistent_items(
    change: dict[str, object], message: str
) -> None:
    row = DATA.items[0].model_dump(mode="json") | change
    with pytest.raises(ValidationError, match=message):
        EvalItem.model_validate(row)


def test_corpus_item_schema_requires_its_marker_and_canaries_in_the_content() -> None:
    entry = next(c for c in DATA.corpus.values() if c.injection_canaries)
    row = entry.model_dump(mode="json")
    with pytest.raises(ValidationError, match="marker"):
        type(entry).model_validate(row | {"content": "no marker here"})
    with pytest.raises(ValidationError, match="canary"):
        type(entry).model_validate(row | {"injection_canaries": ["NOT-IN-TEXT"]})
    with pytest.raises(ValidationError, match="sos://"):
        type(entry).model_validate(row | {"source": "https://example.invalid/doc"})
