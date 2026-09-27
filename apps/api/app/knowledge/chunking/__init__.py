"""Structure-aware chunking (docs/06 §4.5): pure functions, no I/O.

Responsibility: implement :class:`app.knowledge.interfaces.Chunker`: split an
:class:`app.knowledge.domain.ExtractedDocument` on headings, numbered paragraphs,
"Sub:/Ref:/Order:" blocks, table boundaries and page breaks; target and overlap sizes and the
table limit come from ``knowledge/config/chunking.yaml``; tables are kept whole or split by row
groups with the header repeated; a contextual header (doc type, issuer, reference, date,
subject, section) is prepended for embedding and full-text search but never shown to users.

Boundary: pure. May import only ``app.core`` helpers (never ``app.core.db``),
``app.knowledge.domain`` and ``app.knowledge.config``; no database, web, network or other module
(import-linter ``knowledge-pure-foundations`` and ``knowledge-layers``).
"""
