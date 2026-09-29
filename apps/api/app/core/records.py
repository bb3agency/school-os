"""Record tables for the school's full data export (FR-ADM-001, US-1201).

A :class:`RecordTable` is one table of the export archive: a file name, its column names and its
rows, as plain Python values. Each module builds its own tables through a public
``export_records`` function in its ``service.py`` (CLAUDE.md §4: the admin module never reads
another module's tables), usually with :func:`app.core.record_tables.dump_table`, which reads
every row of one of the module's tables in the caller's ``tenant_session`` (RLS: this school
only) and leaves out the columns the module names (ciphertext, blind indexes, search vectors):
restricted (C3) values are decrypted and masked, or left out, by the owning module only.

Pure value type: no database, no web, no writing (the archive is written by ``app.admin``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

TABLE_NAME_RE: Final = r"^[a-z][a-z0-9_]{0,62}$"


@dataclass(frozen=True, slots=True)
class RecordTable:
    """One table of the export: ``name`` is the file stem (``records/<name>.csv``/``.json``).

    ``notes`` are codes the archive's manifest lists for this table (e.g. ``c3_masked``: the
    restricted values are shown as ``••••``; ``aadhaar_as_printed_withheld``)."""

    name: str
    columns: tuple[str, ...]
    rows: list[tuple[object, ...]]
    notes: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if not re.match(TABLE_NAME_RE, self.name):
            raise ValueError(f"record table name must match {TABLE_NAME_RE}")
        width = len(self.columns)
        if any(len(r) != width for r in self.rows):
            raise ValueError(f"every row of {self.name} needs {width} values")


__all__ = ["TABLE_NAME_RE", "RecordTable"]
