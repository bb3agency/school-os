"""Visibility oracle: who may see which corpus item (docs/06 §6 `<ALLOWED>`, FR-KB-002).

The harness judges leakage with this oracle, never with the system under test: an item is
visible when it belongs to the asker's tenant and one ACL key overlaps the asker's role,
sections or classes. `retrievable` adds `is_latest`, which the retrieval filter also applies.
"""

from __future__ import annotations

from sos_evals.schema import Asker, CorpusItem


def visible(asker: Asker, item: CorpusItem) -> bool:
    if item.tenant != asker.tenant:
        return False
    acl = item.acl
    return (
        asker.role in acl.roles
        or bool(set(asker.sections) & set(acl.sections))
        or bool(set(asker.classes) & set(acl.classes))
    )


def retrievable(asker: Asker, item: CorpusItem) -> bool:
    return item.is_latest and visible(asker, item)
