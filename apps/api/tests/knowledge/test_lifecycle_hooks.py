"""Where knowledge hooks into the documents lifecycle (FR-DOC-007, docs/06 §4.8).

Flagging verified answers that cite a deleted document is a ``DELETED_HOOKS`` entry (after the
delete, same transaction), not a delete guard: a guard runs before the delete and may be
followed by a refusal, and a guard that never refuses is not a guard. The database behaviour is
covered by ``test_index_lifecycle_db.py``.
"""

from __future__ import annotations

from app.documents import service as documents
from app.knowledge import lifecycle


def test_FR_DOC_007_flagging_runs_as_a_deleted_hook_not_a_delete_guard() -> None:
    lifecycle.install()
    lifecycle.install()  # idempotent
    assert documents.DELETED_HOOKS.count(lifecycle.flag_citing_answers) == 1
    assert lifecycle.flag_citing_answers not in documents.DELETE_GUARDS
    assert documents.STATUS_CHANGED_HOOKS.count(lifecycle.on_status_changed) == 1


def test_PRV_009_erasure_hooks_are_installed_once() -> None:
    """The embedding cache has no foreign key to chunks: its vectors are forgotten before the
    delete cascades (``DELETING_HOOKS``); a discarded version leaves the index at once
    (``VERSION_DISCARDED_HOOKS``; PRV-016). docs/08 §7 erasure chain."""
    lifecycle.install()
    lifecycle.install()
    assert documents.DELETING_HOOKS.count(lifecycle.forget_document_embeddings) == 1
    assert documents.VERSION_DISCARDED_HOOKS.count(lifecycle.on_version_discarded) == 1
