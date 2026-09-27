"""Structure-aware chunker (docs/06 §4.5): implements :class:`app.knowledge.interfaces.Chunker`.

Rules, in order of strength:

- **Hard boundaries** (a chunk never spans them, no overlap across them): headings (each opens a
  section; ``heading_path`` is the stack of open headings) and tables.
- **Soft boundaries** (a chunk that already has ``target_tokens.min`` ends here): paragraphs
  opening with a block marker (``Sub:``, ``Ref:``, ``Order:`` from config), numbered paragraphs
  (``1.``, ``(a)``, ``iv)``, ``2.3``), explicit page breaks and page changes.
- **Size**: text is packed word by word up to ``target_tokens.max``. When a chunk is full the
  next one starts with the last ``overlap_tokens`` (whole words) of the previous one. A word
  longer than a chunk is cut between grapheme clusters (never inside a Telugu akshara) and gets
  no overlap.
- **Tables** stay whole up to ``table_max_tokens``; larger tables are cut into row groups of at
  most ``target_tokens.max`` tokens, each repeating the header row.

Every word of the input appears, in order, in the chunks (overlap only repeats words), chunks
are numbered from 1 in document order, and the output depends only on the input and the config.
Boundary: pure (see the package docstring).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Literal

from app.knowledge.chunking.header import context_header
from app.knowledge.chunking.text import (
    combine_languages,
    detect_language,
    estimate_tokens,
    normalize_space,
    split_word,
)
from app.knowledge.config.chunking import ChunkingConfig, load_chunking_config
from app.knowledge.domain import (
    Chunk,
    DocumentContext,
    ExtractedBlock,
    ExtractedDocument,
    Locale,
)

_NUMBERED: Final = re.compile(
    r"^(?:\(?(?:\d{1,3}|[A-Za-z]|[ivxlIVXL]{1,5})[.)]|\d{1,3}(?:\.\d{1,3})+\.?)\s"
)
TABLE_CELL_SEPARATOR: Final = " | "
"""How extraction joins the cells of a table row (one row per line of the block text)."""

SegmentKind = Literal["heading", "paragraph", "list_item"]


@dataclass(frozen=True, slots=True)
class _Word:
    text: str
    tokens: int
    piece: bool = False
    """A cut of a word longer than a chunk (never repeated as overlap)."""
    glued: bool = False
    """Continues the previous piece without a space."""


@dataclass(slots=True)
class _Segment:
    unit: int
    kind: SegmentKind
    page: int
    language: Locale | None
    words: list[_Word] = field(default_factory=list)


def _render(words: list[_Word]) -> str:
    out: list[str] = []
    for word in words:
        if out and not word.glued:
            out.append(" ")
        out.append(word.text)
    return "".join(out)


class StructureChunker:
    """``chunk(document, context) -> list[Chunk]`` (the :class:`Chunker` protocol)."""

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config or load_chunking_config()

    @property
    def config(self) -> ChunkingConfig:
        return self._config

    def chunk(self, document: ExtractedDocument, context: DocumentContext) -> list[Chunk]:
        return _Run(self._config, context).run(document)


class _Run:
    """State of one ``chunk()`` call (a fresh object per call keeps the chunker reentrant)."""

    def __init__(self, config: ChunkingConfig, context: DocumentContext) -> None:
        self.cfg = config
        self.context = context
        self.max = config.target_tokens.max
        self.min = config.target_tokens.min
        self.overlap = config.overlap_tokens
        self.piece_budget = self.max - self.overlap.max
        self.markers = tuple(m.casefold() for m in config.block_markers)
        self.chunks: list[Chunk] = []
        self.path: tuple[str, ...] = ()
        self.segments: list[_Segment] = []
        self.tokens = 0
        self.body_words = 0
        self.unit = 0

    # --- driver ---------------------------------------------------------------------------

    def run(self, document: ExtractedDocument) -> list[Chunk]:
        pending_boundary = False
        previous_page: int | None = None
        for page in document.pages:
            if previous_page is not None and page.page_no != previous_page:
                pending_boundary = True
            previous_page = page.page_no
            for block in page.blocks:
                if block.kind == "page_break":
                    pending_boundary = True
                    continue
                if block.kind == "table":
                    self._table(block, page.page_no)
                    pending_boundary = False
                    continue
                text = normalize_space(block.text)
                if not text:
                    continue
                language = block.language or detect_language(text, self.cfg.language_dominant_share)
                if block.kind == "heading":
                    self._flush(carry=False)
                    level = max(1, block.level or 1)
                    self.path = (*self.path[: level - 1], text)
                    self._text(text, "heading", page.page_no, language, boundary=False)
                else:
                    boundary = pending_boundary or self._opens_block(text)
                    kind: SegmentKind = "list_item" if block.kind == "list_item" else "paragraph"
                    self._text(text, kind, page.page_no, language, boundary=boundary)
                pending_boundary = False
        self._flush(carry=False)
        return self.chunks

    def _opens_block(self, text: str) -> bool:
        return text.casefold().startswith(self.markers) or bool(_NUMBERED.match(text))

    # --- words ----------------------------------------------------------------------------

    def _words(self, text: str, budget: int) -> list[_Word]:
        rate = self.cfg.token_estimate
        words: list[_Word] = []
        for raw in text.split():
            tokens = estimate_tokens(raw, rate)
            if tokens <= budget:
                words.append(_Word(raw, tokens))
                continue
            for i, piece in enumerate(split_word(raw, budget, rate)):
                words.append(_Word(piece, estimate_tokens(piece, rate), piece=True, glued=i > 0))
        return words

    # --- text -----------------------------------------------------------------------------

    def _text(
        self,
        text: str,
        kind: SegmentKind,
        page: int,
        language: Locale | None,
        *,
        boundary: bool,
    ) -> None:
        self.unit += 1
        if boundary and self.body_words and self.tokens >= self.min:
            self._flush(carry=False)
        for word in self._words(text, self.piece_budget):
            if self.body_words and self.tokens + word.tokens > self.max:
                self._flush(carry=not word.glued)
            self._append(word, kind, page, language)

    def _append(self, word: _Word, kind: SegmentKind, page: int, language: Locale | None) -> None:
        if not self.segments or self.segments[-1].unit != self.unit:
            self.segments.append(_Segment(self.unit, kind, page, language))
        self.segments[-1].words.append(word)
        self.tokens += word.tokens
        self.body_words += 1

    def _flush(self, *, carry: bool) -> None:
        if self.body_words:
            content_parts: list[str] = []
            previous: _Segment | None = None
            for seg in self.segments:
                if previous is not None:
                    both_items = previous.kind == seg.kind == "list_item"
                    content_parts.append("\n" if both_items else "\n\n")
                content_parts.append(_render(seg.words))
                previous = seg
            pages = [seg.page for seg in self.segments]
            self._emit(
                "".join(content_parts),
                self.tokens,
                min(pages),
                max(pages),
                combine_languages([seg.language for seg in self.segments]),
                is_table=False,
            )
        tail = self._overlap_tail() if carry and self.body_words else []
        self.segments = tail
        self.tokens = sum(w.tokens for seg in tail for w in seg.words)
        self.body_words = 0

    def _overlap_tail(self) -> list[_Segment]:
        picked: list[tuple[_Segment, _Word]] = []
        total = 0
        for seg in reversed(self.segments):
            for word in reversed(seg.words):
                if (
                    word.piece
                    or total + word.tokens > self.overlap.max
                    or total >= self.overlap.min
                ):
                    break
                picked.append((seg, word))
                total += word.tokens
            else:
                continue
            break
        tail: list[_Segment] = []
        for seg, word in reversed(picked):
            if not tail or tail[-1].unit != seg.unit:
                tail.append(_Segment(seg.unit, seg.kind, seg.page, seg.language))
            tail[-1].words.append(word)
        return tail

    # --- tables ---------------------------------------------------------------------------

    def _table(self, block: ExtractedBlock, page: int) -> None:
        self._flush(carry=False)
        header_line = TABLE_CELL_SEPARATOR.join(normalize_space(c) for c in block.table_header)
        if not normalize_space(header_line.replace("|", " ")):
            header_line = ""
        rows = [normalize_space(r) for r in block.text.split("\n")]
        rows = [r for r in rows if r]
        if not rows and not header_line:
            return
        language = block.language or detect_language(
            "\n".join([header_line, *rows]), self.cfg.language_dominant_share
        )
        header_words = self._words(header_line, self.piece_budget)
        header_tokens = sum(w.tokens for w in header_words)
        row_words = [self._words(r, self.piece_budget) for r in rows]
        total = header_tokens + sum(w.tokens for words in row_words for w in words)
        if total <= self.cfg.table_max_tokens:
            lines = ([_render(header_words)] if header_words else []) + [
                _render(words) for words in row_words
            ]
            self._emit("\n".join(lines), total, page, page, language, is_table=True)
            return
        repeat = bool(header_words) and header_tokens <= self.max // 2
        budget = self.max - (header_tokens if repeat else 0)
        units = row_words if repeat or not header_words else [header_words, *row_words]
        groups: list[list[list[_Word]]] = []
        group_tokens = 0
        for words in self._fit_rows(units, budget):
            tokens = sum(w.tokens for w in words)
            if not groups or group_tokens + tokens > budget:
                groups.append([])
                group_tokens = 0
            groups[-1].append(words)
            group_tokens += tokens
        for group in groups:
            lines = [_render(words) for words in group]
            tokens = sum(w.tokens for words in group for w in words)
            if repeat:
                lines.insert(0, _render(header_words))
                tokens += header_tokens
            self._emit("\n".join(lines), tokens, page, page, language, is_table=True)

    def _fit_rows(self, rows: list[list[_Word]], budget: int) -> list[list[_Word]]:
        """Rows longer than ``budget`` are cut into several lines (words cut further if needed)."""
        rate = self.cfg.token_estimate
        out: list[list[_Word]] = []
        for words in rows:
            current: list[_Word] = []
            tokens = 0
            for word in words:
                parts = [word]
                if word.tokens > budget:
                    parts = [
                        _Word(p, estimate_tokens(p, rate), piece=True, glued=word.glued or i > 0)
                        for i, p in enumerate(split_word(word.text, budget, rate))
                    ]
                for part in parts:
                    if current and tokens + part.tokens > budget:
                        out.append(current)
                        current, tokens = [], 0
                    current.append(part)
                    tokens += part.tokens
            if current:
                out.append(current)
        return out

    # --- output ---------------------------------------------------------------------------

    def _emit(
        self,
        content: str,
        tokens: int,
        page_from: int,
        page_to: int,
        language: Locale | None,
        *,
        is_table: bool,
    ) -> None:
        header = context_header(self.context, self.path) if self.cfg.contextual_header else ""
        self.chunks.append(
            Chunk(
                chunk_no=len(self.chunks) + 1,
                content=content,
                context_header=header,
                heading_path=self.path,
                page_from=page_from,
                page_to=page_to,
                token_count=tokens,
                language=language,
                is_table=is_table,
            )
        )


__all__ = ["TABLE_CELL_SEPARATOR", "StructureChunker"]
