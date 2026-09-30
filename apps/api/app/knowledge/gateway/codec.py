"""Wire formats: domain values <-> one provider's request and response dicts (ADR-0033).

A :class:`Codec` builds the redacted request body for a role and parses what comes back, so the
gateway's controls (switches, budgets, retries, breaker, metering, Aadhaar masking of streamed
text) are the same for every provider:

- :class:`AnthropicCodec`: the Messages API (:mod:`.wire`), native ``search_result`` citations;
- :class:`~app.knowledge.gateway.gemini_wire.GeminiCodec`: Vertex AI ``generateContent``,
  numbered passages with ``[n]`` markers (:mod:`.citations`), thought signatures replayed.

The gateway picks the codec by the wire format of the transport that serves the role
(:func:`app.knowledge.gateway.transport.wire_of`); the live factory pairs each provider with the
transport of the same wire, and a role's provider comes from ``models.yaml``.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.knowledge.config.llm import LlmConfig, RoleConfig
from app.knowledge.domain import ConversationItem, ModelTurn, ToolSpec
from app.knowledge.gateway import wire as messages_api
from app.knowledge.gateway.citations import Passage
from app.knowledge.gateway.errors import InvalidModelOutput
from app.knowledge.gateway.transport import Wire
from app.knowledge.gateway.wire import RawUsage

ImageType = Literal["image/png", "image/jpeg", "image/webp"]
MAX_IMAGE_BYTES = 7 * 1024 * 1024
"""Per image sent inline (base64 grows it by a third; Vertex inline requests stay < 20 MB)."""


@dataclass(frozen=True, slots=True)
class ImageInput:
    """A page image for a vision role (register extraction, docs/06 §10.2).

    Caller contract (invariant 4, PRV-016): the image must already have every Aadhaar number
    blacked out, because the gateway can mask text but not pixels. Images are never cached and
    never logged; they are added to the request after text redaction so masking can never
    corrupt their bytes."""

    mime_type: ImageType
    data: bytes

    def __post_init__(self) -> None:
        if not self.data or len(self.data) > MAX_IMAGE_BYTES:
            raise ValueError("an image must be 1 byte to 7 MiB")

    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


@dataclass(frozen=True, slots=True)
class Prepared:
    """A redacted request body plus what parsing its response needs."""

    body: dict[str, Any]
    static_prefix: tuple[str, ...] = ()
    """Body keys a transport MAY move into a provider-side cache (static, non-personal)."""
    passages: tuple[Passage, ...] = ()
    """Numbered passages of this request (marker citations)."""


@dataclass(frozen=True, slots=True)
class ParsedTurn:
    turn: ModelTurn
    usage: RawUsage
    dropped_markers: int = 0
    """Citation markers dropped by the gateway (no such passage, or not supported by it)."""


class Assembler(Protocol):
    """Rebuilds one response from its stream events; ``feed`` returns display text or None."""

    complete: bool

    def feed(self, event: Mapping[str, Any]) -> str | None: ...

    def flush(self) -> str:
        """Display text still held back at the end of the stream."""
        ...

    def response(self) -> dict[str, Any]: ...


class Codec(Protocol):
    @property
    def wire(self) -> Wire: ...

    def turn_request(
        self,
        config: LlmConfig,
        role: RoleConfig,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> Prepared: ...

    def json_request(
        self,
        config: LlmConfig,
        role: RoleConfig,
        system: str,
        text: str,
        schema: Mapping[str, object],
        *,
        images: Sequence[ImageInput] = (),
    ) -> Prepared: ...

    def parse_turn(
        self,
        config: LlmConfig,
        response: Mapping[str, Any],
        offered: frozenset[str],
        fallback_model: str,
        prepared: Prepared,
    ) -> ParsedTurn: ...

    def usage(self, response: Mapping[str, Any]) -> RawUsage: ...

    def json_text(self, response: Mapping[str, Any]) -> str:
        """The structured-output text; :class:`InvalidModelOutput` when the model declined or
        was blocked."""
        ...

    def assembler(self, config: LlmConfig, prepared: Prepared) -> Assembler: ...


class _AnthropicAssembler(messages_api.StreamAssembler):
    def flush(self) -> str:
        return ""


class AnthropicCodec:
    """The Messages API (ADR-0005; the fallback provider since ADR-0033)."""

    wire: Wire = "anthropic"

    def turn_request(
        self,
        config: LlmConfig,
        role: RoleConfig,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> Prepared:
        return Prepared(messages_api.turn_request(config, role, system, conversation, tools))

    def json_request(
        self,
        config: LlmConfig,
        role: RoleConfig,
        system: str,
        text: str,
        schema: Mapping[str, object],
        *,
        images: Sequence[ImageInput] = (),
    ) -> Prepared:
        body = messages_api.json_request(role, system, text, schema)
        if images:
            blocks = [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": i.mime_type, "data": i.base64},
                }
                for i in images
            ]
            message = body["messages"][0]
            message["content"] = [*blocks, *message["content"]]
        return Prepared(body)

    def parse_turn(
        self,
        config: LlmConfig,
        response: Mapping[str, Any],
        offered: frozenset[str],
        fallback_model: str,
        prepared: Prepared,
    ) -> ParsedTurn:
        turn, usage = messages_api.parse_turn(response, offered, fallback_model)
        return ParsedTurn(turn, usage)

    def usage(self, response: Mapping[str, Any]) -> RawUsage:
        return messages_api.usage(response)

    def json_text(self, response: Mapping[str, Any]) -> str:
        if response.get("stop_reason") == "refusal":
            raise InvalidModelOutput("the model declined")
        return messages_api.response_text(response)

    def assembler(self, config: LlmConfig, prepared: Prepared) -> Assembler:
        return _AnthropicAssembler()


__all__ = [
    "MAX_IMAGE_BYTES",
    "AnthropicCodec",
    "Assembler",
    "Codec",
    "ImageInput",
    "ParsedTurn",
    "Prepared",
]
