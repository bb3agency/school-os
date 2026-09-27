# ADR-0027: PDF text-layer extraction library for knowledge ingestion

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-27 |
| Deciders | Product owner (accepted 2026-09-27) |
| Amends / supersedes | none. Adds one dependency under ADR-0004's stack. |

## Context

Knowledge ingestion (docs/06 §4.2, M2 package K5) must read the text layer of PDFs, keeping page numbers for citations (`sos://doc/{id}/v{n}#p{page}`, FR-DOC-006). Circulars from the DEO and board arrive mostly as PDFs; DOCX and plain text are already handled with the standard library and `defusedxml` (no new dependency). No PDF library is a dependency of `sos-api` today (`apps/api/pyproject.toml`, checked 2026-09-27), and CLAUDE.md §11 forbids adding one without checking licence (no AGPL in core), maintenance and known CVEs.

Forces:

- **Licence.** Core is proprietary SaaS code: AGPL is excluded (CLAUDE.md §11).
- **Hostile input.** Files are malware-scanned (FR-DOC-002) but a PDF parser still sees attacker-controlled bytes. The parser runs in the worker, which holds `sos_app` credentials; parse with limits (pages, objects, time) and prefer a library with active security maintenance.
- **Telugu.** Many Telugu PDFs use legacy non-Unicode fonts or lack `ToUnicode` maps, so the "text layer" is mojibake. Extraction must detect this (e.g. share of Telugu-block or replacement characters per page) and route such pages to OCR (docs/06 §4.2 scanned path) instead of indexing garbage.
- **Layout.** Page numbers are required; block order and table detection help chunking (docs/06 §4.5) but are secondary to correct text.

## Decision

1. Use **pypdfium2** (Python bindings to Google's PDFium; Apache-2.0 / BSD-3-Clause, PDFium itself BSD-3-Clause) for the PDF text layer: per-page text with page numbers, run in the `ingest` worker only.
2. Pages whose extracted text fails a quality check (to be set in `knowledge/config/chunking.yaml`, e.g. too few letters per page, or Telugu-looking text outside the Telugu Unicode block) MUST NOT be indexed from the text layer; they are marked `needs attention` and later go to OCR.
3. Limits in config: maximum pages per version and a per-version time budget; a PDF over either is not indexed (`error` code, never text).
4. The exact version is pinned in `apps/api/pyproject.toml` / `uv.lock` when this ADR is accepted, after `pip-audit` and a check of the project's release activity on that date.

## Consequences

- Good: fast native extraction with a permissive licence; wheels for Linux and Windows (developer machines); no AGPL.
- Bad / costs: a native binary in the worker image (image size, a C/C++ attack surface updated through wheel releases); table structure is not extracted (pdfplumber would be needed for that).
- Follow-up work: `extract.py` PDF branch and tests with synthetic PDFs (text layer, empty text layer, mojibake page); config keys for the quality check and limits; add `application/pdf` to `extraction.supported_mime_types`; trivy/pip-audit in CI cover the new wheel.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| PyMuPDF (fitz) | AGPL-3.0 (or a commercial licence): excluded from core by CLAUDE.md §11. |
| pdfplumber (on pdfminer.six), MIT | Good table detection, but pure Python and markedly slower on large circular bundles; a candidate later for table-heavy documents, alongside pypdfium2. |
| pypdf, BSD-3-Clause | Pure Python and simple, but weaker text ordering on multi-column layouts; acceptable fallback if a native wheel is unwanted. |
| OCR every PDF | Slower and costlier than reading an existing text layer, and loses exact text when the layer is good. OCR remains the path for scanned pages. |

## Related requirements

FR-DOC-001, FR-DOC-002, FR-DOC-006, FR-KB-001, PRV-013 (redaction still runs on PDF text); docs/06 §4.2, §4.3; CLAUDE.md §11 (dependency checks).

## Acceptance (2026-09-27)

Accepted by the product owner on 2026-09-27 ("go with your recommendations"). pypdfium2 adopted as proposed.
