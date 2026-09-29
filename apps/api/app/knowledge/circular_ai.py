"""Circular reading and parent-notice drafting over the gateway (M4; docs/06 §4.10, §10.4-10.5).

Exposed to other modules only through :mod:`app.knowledge.service`. The calling module (the
circulars module) owns the stored results, the review workflow and the audit events; this module
owns what the model sees and what comes back:

- :func:`circular_passages`: the indexed chunks of one document version, as numbered passages
  (read in the caller's ``tenant_session``; ingestion already masked Aadhaar numbers).
- :func:`read_circular`: one ``generate_json`` call (role ``circular``, feature ``circulars``,
  prompt ``circular_reading``) and the server-side validation of :mod:`.circulars.reading`.
  Called OUTSIDE a database transaction (the provider may take seconds); metering writes its
  own ``kb.llm_calls`` row.
- :func:`draft_notice`: one ``generate_json`` call (role ``notice``, feature ``notices``, prompt
  ``parent_notice``) from a circular's passages or staff text, validated by
  :mod:`.circulars.notice`.

Every provider refusal (AI switched off, budget used up, rate limit, outage, rejected request,
invalid output) becomes :class:`AiUnavailable` with the gateway's stable code, so the caller can
fall back to manual review. Nothing here logs or raises with passage, prompt or model text.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.core.logging import get_logger
from app.knowledge import composition, sources
from app.knowledge import repository as repo
from app.knowledge.circulars import notice as notice_rules
from app.knowledge.circulars import reading as reading_rules
from app.knowledge.config.circulars import CircularsConfig, load_circulars_config
from app.knowledge.domain import Metering, ModelRole
from app.knowledge.gateway.errors import GatewayError, GatewayMisuse
from app.knowledge.prompts.registry import load_prompt

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

NO_TEXT: Final = "no_text"
"""Reading code when the version has no indexed text (scanned image, unsupported file)."""


class AiUnavailable(Exception):
    """The model call did not produce a usable result; ``code`` says why (never text)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReadingOutcome:
    reading: reading_rules.CircularReading
    model: str
    prompt: str
    """``<id>.v<version>`` of the prompt used (stored with the reading)."""


def config() -> CircularsConfig:
    return load_circulars_config()


def circular_passages(
    session: Session, document_id: uuid.UUID, version_id: uuid.UUID, version_no: int
) -> tuple[reading_rules.Passage, ...]:
    chunks = repo.version_chunks(session, document_id, version_id)
    return tuple(
        reading_rules.Passage(
            number=i,
            chunk_no=c.chunk_no,
            page=c.page_from,
            source=sources.document_page(document_id, version_no=version_no, page=c.page_from or 1),
            text=c.content,
        )
        for i, c in enumerate(chunks, start=1)
    )


def _model(role: ModelRole) -> str:
    return composition.runtime().llm_config.roles[role].model


def read_circular(
    tenant_id: uuid.UUID,
    context: reading_rules.CircularContext,
    passages: Sequence[reading_rules.Passage],
) -> ReadingOutcome:
    """Read one circular version. Raises :class:`AiUnavailable` (``no_text`` when there is
    nothing to read, or the gateway's code)."""
    cfg = config().reading
    if not passages:
        raise AiUnavailable(NO_TEXT)
    request = reading_rules.build_request(context, passages, cfg)
    prompt = load_prompt(cfg.prompt.id, cfg.prompt.version)
    gateway = composition.runtime().gateway
    try:
        raw = gateway.generate_json(
            Metering(tenant_id=tenant_id, feature="circulars"),
            "circular",
            prompt.render(),
            request.text,
            reading_rules.SCHEMA,
        )
    except GatewayMisuse:
        raise
    except GatewayError as exc:
        log.warning("knowledge.circular.read_failed", tenant_id=tenant_id, error_code=exc.code)
        raise AiUnavailable(exc.code) from None
    result = reading_rules.validate_reading(raw, request, cfg)
    log.info(
        "knowledge.circular.read",
        tenant_id=tenant_id,
        count=len(result.deadlines),
        outcome="dropped" if result.dropped else "ok",
    )
    return ReadingOutcome(
        reading=result,
        model=_model("circular"),
        prompt=f"{cfg.prompt.id}.v{cfg.prompt.version}",
    )


def draft_notice(
    tenant_id: uuid.UUID, source: notice_rules.NoticeSource
) -> notice_rules.NoticeDraft:
    """Draft a bilingual parent notice. Raises :class:`AiUnavailable`."""
    cfg = config().notice
    prompt = load_prompt(cfg.prompt.id, cfg.prompt.version)
    gateway = composition.runtime().gateway
    try:
        raw = gateway.generate_json(
            Metering(tenant_id=tenant_id, feature="notices"),
            "notice",
            prompt.render(),
            notice_rules.build_request(source, cfg),
            notice_rules.SCHEMA,
        )
    except GatewayMisuse:
        raise
    except GatewayError as exc:
        log.warning("knowledge.notice.draft_failed", tenant_id=tenant_id, error_code=exc.code)
        raise AiUnavailable(exc.code) from None
    return notice_rules.validate_notice(raw, cfg)


__all__ = [
    "NO_TEXT",
    "AiUnavailable",
    "ReadingOutcome",
    "circular_passages",
    "config",
    "draft_notice",
    "read_circular",
]
