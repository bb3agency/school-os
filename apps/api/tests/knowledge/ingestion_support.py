"""Synthetic inputs and fakes for the ingestion tests (invariant 11: nothing real).

Fakes stand in for the parts other packages own: the SQL chunk store (retrieval; here the
in-memory store), the tenant embedder (embeddings) and ``documents.service`` (a dict).
"""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, cast

from app.core.redaction import verhoeff_check_digit
from app.knowledge.config.chunking import load_chunking_config
from app.knowledge.domain import InputType
from app.knowledge.ingestion.extract import DOCX_MIME
from app.knowledge.ingestion.memory import InMemoryChunkStore
from app.knowledge.ingestion.pipeline import DocumentIngestionPipeline, SessionFactory
from app.knowledge.ingestion.ports import DocumentFacts, DocumentNotReady, VersionFacts

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_BODY = "73920184556"
SYNTHETIC_AADHAAR = _BODY + verhoeff_check_digit(_BODY)
"""A 12-digit number that passes the Verhoeff check (synthetic; not issued to anyone)."""
AADHAAR_SPACED = f"{SYNTHETIC_AADHAAR[:4]} {SYNTHETIC_AADHAAR[4:8]} {SYNTHETIC_AADHAAR[8:]}"
AADHAAR_MASK = f"XXXX XXXX {SYNTHETIC_AADHAAR[-4:]}"
SYNTHETIC_NAME = "Kommineni Synthetica Lalitha"
CONFIG = load_chunking_config()

TENANT_A = uuid.UUID("0190a000-0000-7000-8000-00000000000a")
TENANT_B = uuid.UUID("0190b000-0000-7000-8000-00000000000b")
SECTION_6A = uuid.UUID("0190c000-0000-7000-8000-000000000601")
CLASS_7 = uuid.UUID("0190c000-0000-7000-8000-000000000700")


# --- DOCX -----------------------------------------------------------------------------------------


def docx(body: str, styles: str | None = None, *, extra: dict[str, bytes] | None = None) -> bytes:
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document)
        if styles is not None:
            zf.writestr(
                "word/styles.xml",
                f'<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="{W_NS}">{styles}'
                "</w:styles>",
            )
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def p(text: str, style: str | None = None, *, numbered: bool = False) -> str:
    ppr = ""
    if style or numbered:
        inner = f'<w:pStyle w:val="{style}"/>' if style else ""
        inner += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if numbered else ""
        ppr = f"<w:pPr>{inner}</w:pPr>"
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def tbl(*rows: Sequence[str]) -> str:
    out = "<w:tbl>"
    for row in rows:
        out += "<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in row) + "</w:tr>"
    return out + "</w:tbl>"


HEADING_STYLES = (
    '<w:style w:type="paragraph" w:styleId="Kop1"><w:name w:val="heading 1"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>'
)


def circular_docx() -> bytes:
    """A synthetic circular: title, headings, numbered paragraphs, a table, a page break and a
    synthetic Aadhaar-like number (Verhoeff-valid) in the text."""
    filler = " ".join(f"instruction{i}" for i in range(40))
    return docx(
        p("Half-yearly examinations 2026", "Title")
        + p("Sub: Conduct of half-yearly examinations")
        + p("Ref: DEO Guntur Rc.No.123/B/2026")
        + p("1. Timings", "Kop1")
        + p(f"Exams run from 10:00 to 13:00. {filler}")
        + p(f"Candidate helpdesk reference {AADHAAR_SPACED} must be quoted.")
        + page_break()
        + p("2. Seating", "Heading2")
        + tbl(["Class", "Room"], ["6", "R1"], ["7", "R2"])
        + p("విద్యార్థులు సమయానికి పాఠశాలకు రావాలి.", numbered=True),
        HEADING_STYLES,
    )


# --- fakes ----------------------------------------------------------------------------------------


@dataclass
class FakeSession:
    tenant_id: uuid.UUID


class SessionLog:
    def __init__(self) -> None:
        self.opened: list[uuid.UUID] = []

    @contextmanager
    def __call__(self, tenant_id: uuid.UUID) -> Iterator[FakeSession]:
        self.opened.append(tenant_id)
        yield FakeSession(tenant_id)


@dataclass
class FakeSource:
    """``documents.service`` stand-in: per-tenant documents and version bytes."""

    docs: dict[tuple[uuid.UUID, uuid.UUID], DocumentFacts] = field(default_factory=dict)
    files: dict[tuple[uuid.UUID, uuid.UUID, int], bytes] = field(default_factory=dict)
    reads: int = 0

    def document(
        self, session: Any, tenant_id: uuid.UUID, document_id: uuid.UUID
    ) -> DocumentFacts | None:
        assert session.tenant_id == tenant_id  # always inside the tenant's own session
        return self.docs.get((tenant_id, document_id))

    def read_version(self, session: Any, document_id: uuid.UUID, version_no: int) -> bytes:
        self.reads += 1
        facts = self.docs.get((session.tenant_id, document_id))
        version = (
            next((v for v in facts.versions if v.version_no == version_no), None) if facts else None
        )
        if version is None or version.status != "ready":
            raise DocumentNotReady("document_not_ready")
        return self.files[(session.tenant_id, document_id, version_no)]

    def put(self, tenant_id: uuid.UUID, facts: DocumentFacts, files: dict[int, bytes]) -> None:
        self.docs[(tenant_id, facts.id)] = facts
        for version_no, data in files.items():
            self.files[(tenant_id, facts.id, version_no)] = data

    def update(self, tenant_id: uuid.UUID, document_id: uuid.UUID, **changes: Any) -> None:
        key = (tenant_id, document_id)
        self.docs[key] = replace(self.docs[key], **changes)

    def delete(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        self.docs.pop((tenant_id, document_id), None)


class FakeEmbedder:
    """Deterministic 4-dimensional vectors; records every text it was given."""

    model = "fake-embed-v1"

    def __init__(self, before: Callable[[], None] | None = None) -> None:
        self.calls: list[tuple[uuid.UUID, list[str], InputType]] = []
        self.before = before

    def embed(
        self, session: Any, tenant_id: uuid.UUID, texts: Sequence[str], input_type: InputType
    ) -> list[list[float]]:
        assert getattr(session, "tenant_id", tenant_id) == tenant_id  # fakes carry it
        if self.before is not None:
            self.before()
        self.calls.append((tenant_id, list(texts), input_type))
        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            out.append([b / 255 for b in digest[:4]])
        return out

    @property
    def texts(self) -> list[str]:
        return [t for _, texts, _ in self.calls for t in texts]


def version(no: int, *, mime: str = DOCX_MIME, status: str = "ready") -> VersionFacts:
    return VersionFacts(
        id=uuid.UUID(f"0190d000-0000-7000-8000-{no:012d}"),
        version_no=no,
        mime_type=mime,
        status=status,
    )


def facts(
    document_id: uuid.UUID,
    versions: Sequence[VersionFacts],
    *,
    current: int | None = None,
    acl: Sequence[tuple[str, str]] = (("role", "office_admin"),),
    purpose: str = "circular",
    sensitivity: str = "C1",
    title: str = "Half-yearly exam circular",
) -> DocumentFacts:
    current_no = current if current is not None else versions[-1].version_no
    return DocumentFacts(
        id=document_id,
        purpose=purpose,
        doc_type="circular",
        title=title,
        issuer="DEO Guntur",
        issued_on=date(2026, 8, 12),
        academic_year_id=None,
        sensitivity=sensitivity,
        current_version_id=next(v.id for v in versions if v.version_no == current_no),
        acl=tuple(acl),
        versions=tuple(versions),
    )


@dataclass
class World:
    source: FakeSource
    store: InMemoryChunkStore
    embedder: FakeEmbedder
    sessions: SessionLog
    pipeline: DocumentIngestionPipeline


def world(embedder: FakeEmbedder | None = None) -> World:
    source, store, sessions = FakeSource(), InMemoryChunkStore(), SessionLog()
    embedder = embedder or FakeEmbedder()
    pipeline = DocumentIngestionPipeline(
        source=source,
        store=store,
        embedder=embedder,
        config=CONFIG,
        session_factory=cast("SessionFactory", sessions),
    )
    return World(source, store, embedder, sessions, pipeline)
