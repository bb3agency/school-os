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
from app.knowledge.tools.documents import NAME as SEARCH
from app.knowledge.tools.documents import DocumentSearch, SearchDocumentsTool
from app.knowledge.tools.students import FACTS, FIND, FindStudentsTool, GetStudentFactsTool

if TYPE_CHECKING:
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
    return tools


def offered(tools: dict[str, OfferedTool], ctx: UserContext) -> list[OfferedTool]:
    """The caller's tools, in a stable order (prompt caching: same order every request)."""
    return [tools[name] for name in sorted(tools) if tools[name].allowed(ctx)]


__all__ = ["OfferedTool", "build_tools", "offered"]
