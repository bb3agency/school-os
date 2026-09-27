"""Knowledge module skeleton: boundaries that hold before any feature code lands (docs/06).

- Every subpackage docs/06 and CLAUDE.md §4 name exists and states its responsibility.
- No routes yet: a route would need ``require("kb.ask")`` (invariant 2); ``api.py`` arrives with
  the ask endpoint.
- Provider SDKs are imported only under ``knowledge/gateway`` (CLAUDE.md §11, ADR-0005). The
  semgrep rule ``sos-llm-sdk-outside-gateway`` covers the whole repo; this AST scan is the fast
  in-suite copy for the knowledge tree.
- ``knowledge.service`` is the public surface other modules use (CLAUDE.md §4).
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import app.knowledge

KNOWLEDGE = Path(app.knowledge.__file__).resolve().parent

SUBPACKAGES = (
    "gateway",
    "embeddings",
    "retrieval",
    "ingestion",
    "chunking",
    "tools",
    "prompts",
    "config",
)

PROVIDER_SDKS = ("anthropic", "openai", "voyageai")


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_FR_KB_001_subpackage_exists_with_a_responsibility_docstring(name: str) -> None:
    module = importlib.import_module(f"app.knowledge.{name}")
    doc = module.__doc__ or ""
    assert len(doc.strip()) > 80, f"app.knowledge.{name} must say what it owns and may import"
    assert "Boundary" in doc, f"app.knowledge.{name}: state the import boundary"


def test_SEC_020_knowledge_has_no_routes_yet() -> None:
    assert not (KNOWLEDGE / "api.py").exists(), (
        "routes land with the ask endpoint; each needs Depends(require('kb.ask'))"
    )
    from app.main import create_app

    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert not [p for p in paths if p.startswith("/api/v1/knowledge")]


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_SEC_020_provider_sdks_only_inside_the_gateway() -> None:
    offenders = []
    for path in KNOWLEDGE.rglob("*.py"):
        if (KNOWLEDGE / "gateway") in path.parents:
            continue
        found = _imported_roots(path) & set(PROVIDER_SDKS)
        if found:
            offenders.append(f"{path.relative_to(KNOWLEDGE)}: {sorted(found)}")
    assert not offenders, offenders


def test_knowledge_never_reads_the_environment() -> None:
    """Settings come only from app.core.config (CLAUDE.md §4; semgrep sos-env-outside-config)."""
    for path in KNOWLEDGE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "os.environ" not in text, path
        assert "getenv(" not in text, path


def test_FR_KB_005_service_exposes_the_public_interface() -> None:
    from app.knowledge import service

    for name in (
        "KnowledgeService",
        "IngestionPipeline",
        "AskRequest",
        "AskEvent",
        "MetaEvent",
        "TokenEvent",
        "CitationEvent",
        "DoneEvent",
        "ErrorEvent",
        "AnswerSegment",
        "Citation",
        "RankedChunk",
        "SearchFilters",
    ):
        assert hasattr(service, name), name
    assert set(service.__all__) >= {"KnowledgeService", "IngestionPipeline"}


def test_FR_KB_008_sse_event_names_match_the_streaming_protocol() -> None:
    """docs/06 §5.1: meta, token, citation, done, error."""
    from app.knowledge import service

    events = (
        service.MetaEvent,
        service.TokenEvent,
        service.CitationEvent,
        service.DoneEvent,
        service.ErrorEvent,
    )
    assert [e.event for e in events] == ["meta", "token", "citation", "done", "error"]
