"""The tools offered to the answer model, built from ``tools.yaml`` (docs/06 §7; ADR-0008).

Only tools that are implemented AND described in ``tools.yaml`` are built; per request only
those whose permission the caller holds are offered (a tool the caller may not use is never
shown to the model, and each tool re-checks its permission when it runs). The gateway refuses
any tool outside the ADR-0008 whitelist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.knowledge.config.tools import ToolsConfig
from app.knowledge.interfaces import RecordTool
from app.knowledge.tools.conversations import NAME as CHATS
from app.knowledge.tools.conversations import SearchMyConversationsTool
from app.knowledge.tools.counts import NAME as COUNT
from app.knowledge.tools.counts import CountStudentsTool
from app.knowledge.tools.documents import LIST_NAME as LIST_DOCUMENTS
from app.knowledge.tools.documents import NAME as SEARCH
from app.knowledge.tools.documents import DocumentSearch, ListDocumentsTool, SearchDocumentsTool
from app.knowledge.tools.fees import NAME as FEES
from app.knowledge.tools.fees import GetFeeDuesTool
from app.knowledge.tools.findings import NAME as FINDINGS
from app.knowledge.tools.findings import ListFindingsTool
from app.knowledge.tools.history import NAME as HISTORY
from app.knowledge.tools.history import GetValueHistoryTool
from app.knowledge.tools.students import FACTS, FIND, FindStudentsTool, GetStudentFactsTool

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext


@runtime_checkable
class OfferedTool(RecordTool, Protocol):
    def allowed(self, ctx: UserContext) -> bool:
        """Whether the caller holds the tool's permission (the tool re-checks when run)."""
        ...


def build_tools(config: ToolsConfig, search: DocumentSearch) -> dict[str, OfferedTool]:
    tools: dict[str, OfferedTool] = {}
    specs = config.tools
    if specs[SEARCH].description:
        tools[SEARCH] = SearchDocumentsTool(search, specs[SEARCH])
    if specs[FIND].description:
        tools[FIND] = FindStudentsTool(specs[FIND])
    if specs[FACTS].description:
        tools[FACTS] = GetStudentFactsTool(specs[FACTS])
    if specs[HISTORY].description:
        tools[HISTORY] = GetValueHistoryTool(specs[HISTORY])
    if specs[COUNT].description:
        tools[COUNT] = CountStudentsTool(specs[COUNT])
    if specs[FINDINGS].description:
        tools[FINDINGS] = ListFindingsTool(specs[FINDINGS])
    if specs[LIST_DOCUMENTS].description:
        tools[LIST_DOCUMENTS] = ListDocumentsTool(specs[LIST_DOCUMENTS])
    if specs[FEES].description:
        tools[FEES] = GetFeeDuesTool(specs[FEES])
    if specs[CHATS].description:
        tools[CHATS] = SearchMyConversationsTool(specs[CHATS])
    return tools


def _available(tool: OfferedTool, ctx: UserContext, session: Session | None) -> bool:
    """``allowed``, and for a tool behind a school flag (``get_fee_dues``, ADR-0032) its
    ``available`` check too; without a session such a tool is never offered (fail closed)."""
    if not tool.allowed(ctx):
        return False
    check = getattr(tool, "available", None)
    if check is None:
        return True
    return session is not None and bool(check(session, ctx))


def offered(
    tools: dict[str, OfferedTool], ctx: UserContext, session: Session | None = None
) -> list[OfferedTool]:
    """The caller's tools, in a stable order (prompt caching: same order every request)."""
    return [tools[name] for name in sorted(tools) if _available(tools[name], ctx, session)]


__all__ = ["OfferedTool", "build_tools", "offered"]
