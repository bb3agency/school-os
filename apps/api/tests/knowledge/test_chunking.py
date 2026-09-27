"""Structure-aware chunking (docs/06 §4.5; FR-KB-001 hybrid index input, FR-DOC-006 page refs).

Properties checked on generated documents (hypothesis): the same input always gives the same
chunks (idempotent ingestion stages), every input word appears in order (no text lost), sizes stay
within the configured bounds, token counts match the estimate, and Telugu grapheme clusters
(aksharas) are never broken. All text is synthetic (invariant 11).
"""

from __future__ import annotations

import copy
import itertools
import unicodedata
import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.knowledge.chunking import (
    StructureChunker,
    context_header,
    count_tokens,
    detect_language,
    estimate_tokens,
    graphemes,
    split_word,
)
from app.knowledge.config.chunking import ChunkingConfig, load_chunking_config
from app.knowledge.domain import (
    Chunk,
    DocumentContext,
    ExtractedBlock,
    ExtractedDocument,
    ExtractedPage,
)
from app.knowledge.interfaces import Chunker

CONFIG = load_chunking_config()
RATE = CONFIG.token_estimate
CONTEXT = DocumentContext(
    doc_type="circular",
    title="Half-yearly exam timings",
    issuer="DEO Guntur",
    reference_no="Rc.No.123/B/2026",
    issued_on=date(2026, 8, 12),
    subject="Exam timings",
)
TELUGU_WORDS = (
    "పాఠశాల",
    "పరీక్ష",
    "విద్యార్థులు",
    "సమయం",
    "క్షేత్రం",
    "ప్రకటన",
    "తరగతి",
    "ఉపాధ్యాయులు",
    "శ్రీ",
    "స్వాతంత్ర్య",
)
ENGLISH_WORDS = (
    "school",
    "exam",
    "timings",
    "circular",
    "students",
    "attendance",
    "principal",
    "holiday",
    "fees",
    "uniform",
    "Sub:",
    "1.",
    "(a)",
    "12/08/2026",
)


def small_config(**overrides: Any) -> ChunkingConfig:
    data = CONFIG.model_dump(mode="json")
    data.update(
        {
            "target_tokens": {"min": 20, "max": 40},
            "overlap_tokens": {"min": 5, "max": 8},
            "table_max_tokens": 60,
        }
    )
    data.update(overrides)
    return ChunkingConfig.model_validate(data)


def document(*pages: Sequence[ExtractedBlock]) -> ExtractedDocument:
    return ExtractedDocument(
        tenant_id=uuid.UUID(int=1),
        document_id=uuid.UUID(int=2),
        version_id=uuid.UUID(int=3),
        version_no=1,
        pages=tuple(ExtractedPage(page_no=i + 1, blocks=tuple(b)) for i, b in enumerate(pages)),
    )


def para(text: str) -> ExtractedBlock:
    return ExtractedBlock(kind="paragraph", text=text)


def heading(text: str, level: int = 1) -> ExtractedBlock:
    return ExtractedBlock(kind="heading", text=text, level=level)


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> ExtractedBlock:
    return ExtractedBlock(
        kind="table",
        text="\n".join(" | ".join(r) for r in rows),
        table_header=tuple(header),
    )


# --- oracle helpers -------------------------------------------------------------------------------


def input_words(doc: ExtractedDocument) -> list[str]:
    words: list[str] = []
    for page in doc.pages:
        for block in page.blocks:
            if block.kind == "page_break":
                continue
            if block.kind == "table":
                header = " | ".join(" ".join(c.split()) for c in block.table_header)
                if header.replace("|", " ").split():
                    words += header.split()
                for row in block.text.split("\n"):
                    words += row.split()
            else:
                words += block.text.split()
    return words


def is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(any(h == n for h in it) for n in needle)


def input_clusters(doc: ExtractedDocument) -> set[str]:
    found: set[str] = set()
    for page in doc.pages:
        for block in page.blocks:
            found.update(graphemes(block.text))
            for cell in block.table_header:
                found.update(graphemes(cell))
    return found


def check_invariants(doc: ExtractedDocument, chunks: list[Chunk], cfg: ChunkingConfig) -> None:
    # Numbered 1..n in document order, none empty.
    assert [c.chunk_no for c in chunks] == list(range(1, len(chunks) + 1))
    assert all(c.content.strip() for c in chunks)
    # No text lost, nothing invented.
    words = input_words(doc)
    out = [w for c in chunks for w in c.content.split()]
    assert is_subsequence(words, out)
    assert set(out) <= set(words) | {"|"}
    # Sizes within bounds and consistent with the estimate.
    for c in chunks:
        limit = cfg.table_max_tokens if c.is_table else cfg.target_tokens.max
        assert 1 <= c.token_count <= limit, (c.chunk_no, c.token_count)
        assert c.token_count == count_tokens(c.content, cfg.token_estimate)
    # Pages are real pages of the document.
    page_nos = {p.page_no for p in doc.pages}
    for c in chunks:
        assert c.page_from in page_nos
        assert c.page_to in page_nos
        assert c.page_from is not None
        assert c.page_to is not None
        assert c.page_from <= c.page_to
    # Telugu integrity: every cluster of the output is a cluster of the input and no chunk
    # starts with a combining mark (a vowel sign or virama cut off its consonant).
    clusters = input_clusters(doc) | {" ", "\n", "|"}
    for c in chunks:
        assert set(graphemes(c.content)) <= clusters
        assert unicodedata.category(c.content[0]) not in ("Mn", "Mc", "Me")


# --- strategies -----------------------------------------------------------------------------------

word = st.sampled_from(TELUGU_WORDS + ENGLISH_WORDS) | st.from_regex(r"[a-z]{1,12}", fullmatch=True)
text = st.lists(word, min_size=1, max_size=160).map(" ".join)
cell = st.lists(word, min_size=0, max_size=4).map(" ".join)
block = st.one_of(
    st.builds(para, text),
    st.builds(lambda t: ExtractedBlock(kind="list_item", text=t), text),
    st.builds(heading, st.lists(word, min_size=1, max_size=6).map(" ".join), st.integers(1, 3)),
    st.builds(
        table,
        st.lists(cell, min_size=0, max_size=4),
        st.lists(st.lists(cell, min_size=1, max_size=4), min_size=1, max_size=40),
    ),
    st.just(ExtractedBlock(kind="page_break", text="")),
)
documents = st.lists(st.lists(block, min_size=0, max_size=8), min_size=1, max_size=4).map(
    lambda pages: document(*pages)
)
PROPERTY = settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])


# --- properties -----------------------------------------------------------------------------------


@PROPERTY
@given(doc=documents)
def test_FR_KB_001_chunking_is_deterministic(doc: ExtractedDocument) -> None:
    first = StructureChunker(CONFIG).chunk(doc, CONTEXT)
    assert StructureChunker(CONFIG).chunk(copy.deepcopy(doc), CONTEXT) == first
    chunker = StructureChunker(CONFIG)
    assert chunker.chunk(doc, CONTEXT) == chunker.chunk(doc, CONTEXT) == first


@PROPERTY
@given(doc=documents)
def test_FR_KB_001_no_text_lost_and_sizes_within_bounds(doc: ExtractedDocument) -> None:
    check_invariants(doc, StructureChunker(CONFIG).chunk(doc, CONTEXT), CONFIG)


@PROPERTY
@given(doc=documents)
def test_FR_KB_001_small_budgets_keep_the_same_invariants(doc: ExtractedDocument) -> None:
    cfg = small_config()
    check_invariants(doc, StructureChunker(cfg).chunk(doc, CONTEXT), cfg)


@PROPERTY
@given(clusters=st.lists(st.sampled_from(TELUGU_WORDS), min_size=1, max_size=40))
def test_graphemes_round_trip_and_never_start_with_a_mark(clusters: list[str]) -> None:
    joined = "".join(clusters)
    parts = graphemes(joined)
    assert "".join(parts) == joined
    assert all(unicodedata.category(p[0]) not in ("Mn", "Mc", "Me") for p in parts)


# --- text primitives ------------------------------------------------------------------------------


def test_telugu_conjuncts_are_single_clusters() -> None:
    assert graphemes("క్షేత్రం") == ["క్షే", "త్రం"]
    assert graphemes("శ్రీ") == ["శ్రీ"]
    assert graphemes("స్వాతంత్ర్య") == ["స్వా", "తం", "త్ర్య"]


def test_token_estimate_is_per_word_and_pessimistic_for_telugu() -> None:
    assert estimate_tokens("examination", RATE) == 3  # 11 Latin characters / 4
    assert estimate_tokens("a", RATE) == 1
    assert estimate_tokens("పాఠశాల", RATE) == 4  # పా ఠ శా ల: four aksharas
    assert count_tokens("school  exam\nfees", RATE) == 4


def test_long_telugu_word_is_cut_between_aksharas() -> None:
    word = "స్వాతంత్ర్య" * 30
    pieces = list(split_word(word, 7, RATE))
    assert "".join(pieces) == word
    assert all(estimate_tokens(p, RATE) <= 7 for p in pieces)
    assert all(unicodedata.category(p[0]) not in ("Mn", "Mc", "Me") for p in pieces)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Half-yearly exam timings", "en"),
        ("పాఠశాల పరీక్ష సమయం", "te"),
        ("పరీక్ష timings మార్పు notice", "mixed"),
        ("12/08/2026 - 10:00", None),
    ],
)
def test_FR_KB_006_language_by_script(value: str, expected: str | None) -> None:
    assert detect_language(value, CONFIG.language_dominant_share) == expected


# --- structure ------------------------------------------------------------------------------------


def test_headings_open_sections_with_a_heading_path() -> None:
    doc = document(
        [
            heading("Exam schedule"),
            para("The half-yearly exams start on Monday."),
            heading("Timings", level=2),
            para("Morning session 10:00 to 13:00."),
            heading("Fees"),
            para("Exam fee is due by Friday."),
        ]
    )
    chunks = StructureChunker(CONFIG).chunk(doc, CONTEXT)
    assert [c.heading_path for c in chunks] == [
        ("Exam schedule",),
        ("Exam schedule", "Timings"),
        ("Fees",),
    ]
    assert chunks[1].content == "Timings\n\nMorning session 10:00 to 13:00."
    assert chunks[1].context_header.endswith("§ Exam schedule > Timings")


def test_block_markers_and_numbered_paragraphs_end_a_full_enough_chunk() -> None:
    cfg = small_config()
    filler = " ".join(f"w{i}" for i in range(25))  # 25 tokens >= min 20
    doc = document(
        [para(filler), para("Sub: revised timings"), para(filler), para("2. Second point")]
    )
    chunks = StructureChunker(cfg).chunk(doc, CONTEXT)
    starts = [c.content.split()[0] for c in chunks]
    assert starts == ["w0", "Sub:", "2."]


def test_markers_do_not_split_a_chunk_below_the_minimum() -> None:
    doc = document([para("Ref: letter dated 01/08/2026"), para("Sub: exam timings")])
    chunks = StructureChunker(CONFIG).chunk(doc, CONTEXT)
    assert len(chunks) == 1
    assert chunks[0].content == "Ref: letter dated 01/08/2026\n\nSub: exam timings"


def test_long_text_overlaps_by_the_configured_amount() -> None:
    words = [f"{c}{i:03d}" for c in "ab" for i in range(1000)]  # 4 characters: one token each
    chunks = StructureChunker(CONFIG).chunk(document([para(" ".join(words))]), CONTEXT)
    assert len(chunks) >= 3
    for before, after in itertools.pairwise(chunks):
        prev, nxt = before.content.split(), after.content.split()
        overlap = next(k for k in range(len(nxt), 0, -1) if prev[-k:] == nxt[:k])
        assert CONFIG.overlap_tokens.min <= overlap <= CONFIG.overlap_tokens.max
    assert all(c.token_count <= CONFIG.target_tokens.max for c in chunks)


def test_no_overlap_across_headings_or_tables() -> None:
    cfg = small_config()
    doc = document(
        [
            para(" ".join(f"a{i}" for i in range(30))),
            table(["Class", "Time"], [["6", "10:00"]]),
            heading("Next"),
            para("b1 b2"),
        ]
    )
    chunks = StructureChunker(cfg).chunk(doc, CONTEXT)
    assert [c.content.split()[0] for c in chunks] == ["a0", "Class", "Next"]


def test_small_table_stays_whole_with_its_header() -> None:
    doc = document(
        [table(["Class", "Exam", "Time"], [["6", "Maths", "10:00"], ["7", "Telugu", "10:00"]])]
    )
    (chunk,) = StructureChunker(CONFIG).chunk(doc, CONTEXT)
    assert chunk.is_table
    assert chunk.content == "Class | Exam | Time\n6 | Maths | 10:00\n7 | Telugu | 10:00"


def test_large_table_is_split_by_row_groups_repeating_the_header() -> None:
    cfg = small_config()
    rows = [[f"{i}", "Maths", "10:00", "Room", f"r{i}"] for i in range(40)]
    doc = document([table(["Class", "Exam", "Time", "Room", "No"], rows)])
    chunks = StructureChunker(cfg).chunk(doc, CONTEXT)
    assert len(chunks) > 1
    for c in chunks:
        assert c.is_table
        assert c.content.startswith("Class | Exam | Time | Room | No\n")
        assert c.token_count <= cfg.target_tokens.max
    check_invariants(doc, chunks, cfg)


def test_page_refs_span_the_pages_a_chunk_covers() -> None:
    doc = document([para("first page text")], [para("second page text")])
    (chunk,) = StructureChunker(CONFIG).chunk(doc, CONTEXT)
    assert (chunk.page_from, chunk.page_to) == (1, 2)


def test_giant_telugu_word_is_cut_without_overlap_or_broken_aksharas() -> None:
    word = "విద్యార్థులు" * 200
    chunks = StructureChunker(CONFIG).chunk(document([para(word)]), CONTEXT)
    assert len(chunks) > 1
    assert "".join(c.content for c in chunks) == word
    for c in chunks:
        assert c.token_count <= CONFIG.target_tokens.max
        assert unicodedata.category(c.content[0]) not in ("Mn", "Mc", "Me")
        assert c.language == "te"


def test_empty_document_has_no_chunks() -> None:
    doc = document([para("   "), ExtractedBlock(kind="page_break", text="")])
    assert StructureChunker(CONFIG).chunk(doc, CONTEXT) == []


# --- contextual header ----------------------------------------------------------------------------


def test_contextual_header_follows_docs_06() -> None:
    assert context_header(CONTEXT, ("3. Timings",)) == (
        "[Circular] Half-yearly exam timings · DEO Guntur · Ref Rc.No.123/B/2026 · 12 Aug 2026"
        " · Subject: Exam timings · § 3. Timings"
    )
    bare = DocumentContext(doc_type="verified_answer", title="")
    assert context_header(bare, ()) == "[Verified answer]"


def test_contextual_header_is_on_every_chunk_but_never_in_the_content() -> None:
    doc = document([para(" ".join(f"w{i}" for i in range(900)))])
    chunks = StructureChunker(CONFIG).chunk(doc, CONTEXT)
    for c in chunks:
        assert c.context_header.startswith("[Circular] Half-yearly exam timings")
        assert "DEO Guntur" not in c.content


def test_contextual_header_can_be_switched_off() -> None:
    cfg = small_config(contextual_header=False)
    (chunk,) = StructureChunker(cfg).chunk(document([para("school exam")]), CONTEXT)
    assert chunk.context_header == ""


def test_structure_chunker_is_a_chunker() -> None:
    assert isinstance(StructureChunker(), Chunker)
