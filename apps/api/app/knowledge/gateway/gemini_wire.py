"""Domain values <-> Vertex AI Gemini ``generateContent`` dicts (ADR-0033; docs/06 §5, §7, §12).

Outgoing (the same redaction rule as :mod:`.wire`): every string of the request body passes
:func:`app.core.redaction.mask_aadhaar` before it leaves the process (invariant 4). Two values
are added after masking because they are not text and masking could corrupt them: thought
signatures (opaque provider state returned with each function call) and inline image bytes.

- ``systemInstruction``: the role's system prompt; for tool-use turns followed by the configured
  passage-marker instructions (``citations.marker_instructions``).
- ``contents``: the question (earlier questions of the session first, under the configured
  header; FR-KB-012), then each earlier model turn (text, ``functionCall`` parts with their
  ``thoughtSignature``) and each tool result as a ``functionResponse`` whose ``response`` holds
  numbered passages ``{"passages": [{"n", "title", "text"}]}`` (never the ``sos://`` source: the
  model needs the number, not our ids) or ``{"error": ...}`` for a failed tool.
- ``tools``: the offered read-only tools as ``functionDeclarations`` with
  ``parametersJsonSchema`` (ADR-0008 whitelist, invariant 9); ``toolConfig`` mode ``NONE`` once
  ``max_tool_rounds`` rounds are used (never ``ANY``: the gateway never forces a tool).
- ``generationConfig``: ``maxOutputTokens`` (SEC-020; thinking tokens included), the role's
  ``thinkingConfig``, and for strict JSON ``responseMimeType: application/json`` with
  ``responseJsonSchema`` (the gateway still validates the result, :mod:`.schema_check`).

Incoming: the first candidate's parts; ``thought`` parts are dropped; text becomes answer
segments with citations from ``[n]`` markers (:mod:`.citations`); ``functionCall`` parts become
:class:`ToolCall` values (only for tools offered in this request) carrying the part's thought
signature. ``finishReason`` maps to the gateway stop reasons: safety, recitation, blocklist,
prohibited content, SPII and a blocked prompt are refusals (the answer says "not found";
FR-KB-007); ``MAX_TOKENS`` is truncation; malformed or unexpected function calls are invalid
output. Model output is masked again (defence in depth for invariant 4).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final, cast

from app.core.redaction import mask_aadhaar
from app.knowledge.config.llm import Conversation, LlmConfig, RoleConfig
from app.knowledge.domain import (
    AnswerSegment,
    AssistantMessage,
    Citation,
    ConversationItem,
    ModelTurn,
    StopReason,
    ToolCall,
    ToolResultsMessage,
    ToolSpec,
    Usage,
    UserMessage,
)
from app.knowledge.gateway.citations import (
    MarkerStripper,
    Passage,
    number_passages,
    segments_from_markers,
)
from app.knowledge.gateway.codec import ImageInput, ParsedTurn, Prepared
from app.knowledge.gateway.errors import GatewayMisuse, InvalidModelOutput
from app.knowledge.gateway.transport import Wire
from app.knowledge.gateway.wire import (
    RawUsage,
    memory_block,
    redact_payload,
    tool_rounds_used,
    user_texts,
)

REFUSALS: Final = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "LANGUAGE",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
        "OTHER",
    }
)
"""Finish reasons that mean the model (or Google's filters) declined: treated as a refusal."""
INVALID: Final = frozenset(
    {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL", "TOO_MANY_TOOL_CALLS"}
)
SYNTHETIC_CALL_PREFIX: Final = "sos-fc-"
"""Call ids the gateway made up (the model sent none); never sent back as ``id``."""
TOOL_FAILED: Final = "The tool failed. Tell the user the records could not be fully checked."
STATIC_PREFIX: Final = ("systemInstruction", "tools")


def _thinking(role: RoleConfig) -> dict[str, Any] | None:
    if role.thinking == "disabled":
        return {"thinkingBudget": 0}
    if role.thinking == "level" and role.thinking_level is not None:
        return {"thinkingLevel": role.thinking_level.upper()}
    if role.thinking == "budget" and role.thinking_budget is not None:
        return {"thinkingBudget": role.thinking_budget}
    return None


def _generation(role: RoleConfig) -> dict[str, Any]:
    config: dict[str, Any] = {"maxOutputTokens": role.max_output_tokens, "candidateCount": 1}
    thinking = _thinking(role)
    if thinking is not None:
        config["thinkingConfig"] = thinking
    return config


def _user(item: UserMessage, rules: Conversation | None) -> dict[str, Any]:
    """The same layout as the Messages API (summary, recent turns, the question as written,
    then the question; :func:`app.knowledge.gateway.wire.user_texts`), one part per text."""
    return {"role": "user", "parts": [{"text": t} for t in user_texts(item, rules)]}


def _model(item: AssistantMessage) -> dict[str, Any] | None:
    parts: list[dict[str, Any]] = [
        {"text": seg.text} for seg in item.turn.segments if seg.text.strip()
    ]
    for call in item.turn.tool_calls:
        function: dict[str, Any] = {"name": call.name, "args": dict(call.arguments)}
        if not call.call_id.startswith(SYNTHETIC_CALL_PREFIX):
            function["id"] = call.call_id
        parts.append({"functionCall": function})
    return {"role": "model", "parts": parts} if parts else None


def _responses(
    item: ToolResultsMessage, names: Mapping[str, str], numbers: Mapping[int, int]
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    for outcome in item.outcomes:
        name = names.get(outcome.call_id)
        if name is None:
            raise GatewayMisuse("a tool result answers no tool call of this conversation")
        response: dict[str, Any]
        if outcome.is_error:
            response = {"error": TOOL_FAILED}
        else:
            response = {
                "passages": [
                    {"n": numbers[id(b)], "title": b.title, "text": b.text} for b in outcome.blocks
                ]
            }
        function: dict[str, Any] = {"name": name, "response": response}
        if not outcome.call_id.startswith(SYNTHETIC_CALL_PREFIX):
            function["id"] = outcome.call_id
        parts.append({"functionResponse": function})
    return {"role": "user", "parts": parts}


def contents(
    conversation: Sequence[ConversationItem],
    passages: Sequence[Passage],
    rules: Conversation | None = None,
) -> tuple[list[dict[str, Any]], list[str | None]]:
    """The ``contents`` list and the thought signature of every function call, in order."""
    names = {
        c.call_id: c.name
        for i in conversation
        if isinstance(i, AssistantMessage)
        for c in i.turn.tool_calls
    }
    numbers = {id(p.block): p.number for p in passages}
    out: list[dict[str, Any]] = []
    signatures: list[str | None] = []
    for item in conversation:
        if isinstance(item, UserMessage):
            out.append(_user(item, rules))
        elif isinstance(item, AssistantMessage):
            message = _model(item)
            if message is not None:
                out.append(message)
                signatures += [c.signature for c in item.turn.tool_calls]
        else:
            out.append(_responses(item, names, numbers))
    if not out or out[0]["role"] != "user":
        raise GatewayMisuse("a conversation starts with the user's question")
    return out, signatures


def _attach_signatures(body: dict[str, Any], signatures: Sequence[str | None]) -> None:
    """Put each call's thought signature back on its ``functionCall`` part (after masking)."""
    remaining = iter(signatures)
    for message in body["contents"]:
        if message.get("role") != "model":
            continue
        for part in message["parts"]:
            if "functionCall" in part:
                signature = next(remaining, None)
                if signature:
                    part["thoughtSignature"] = signature


def _system(text: str) -> dict[str, Any]:
    return {"parts": [{"text": text}]}


class GeminiCodec:
    wire: Wire = "gemini"

    def turn_request(
        self,
        config: LlmConfig,
        role: RoleConfig,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> Prepared:
        """A redacted tool-use turn. Function calling mode stays ``AUTO`` (never forced); once
        ``max_tool_rounds`` rounds are used it becomes ``NONE`` so the model must answer."""
        passages = number_passages(conversation)
        items, signatures = contents(conversation, passages, config.conversation)
        instruction = system
        if tools or passages:
            instruction = f"{system}\n\n{config.citations.marker_instructions}"
        system_parts = [{"text": instruction}]
        memory = memory_block(conversation, config.conversation)
        if memory is not None:
            # The user's own memory items (ADR-0034) go right after the static prompt, as on
            # the Messages API. They are about a person, so this prefix is never cached.
            system_parts.append({"text": memory["text"]})
        body: dict[str, Any] = {
            "systemInstruction": {"parts": system_parts},
            "contents": items,
            "generationConfig": _generation(role),
        }
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parametersJsonSchema": dict(t.input_schema),
                        }
                        for t in tools
                    ]
                }
            ]
            if tool_rounds_used(conversation) >= config.limits.max_tool_rounds:
                body["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
        redacted = cast(dict[str, Any], redact_payload(body))
        _attach_signatures(redacted, signatures)
        # Vertex takes no system instruction next to a cache, so with memory nothing is cached.
        prefix: tuple[str, ...] = STATIC_PREFIX if memory is None else ()
        return Prepared(redacted, prefix, passages)

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
        """A redacted structured-output call (``responseJsonSchema``; never a forced tool)."""
        generation = _generation(role)
        generation["responseMimeType"] = "application/json"
        generation["responseJsonSchema"] = dict(schema)
        body: dict[str, Any] = {
            "systemInstruction": _system(system),
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": generation,
        }
        redacted = cast(dict[str, Any], redact_payload(body))
        if images:
            inline = [{"inlineData": {"mimeType": i.mime_type, "data": i.base64}} for i in images]
            parts = redacted["contents"][0]["parts"]
            redacted["contents"][0]["parts"] = [*inline, *parts]
        # Never cached: a structured-output system instruction may carry tenant content (the
        # contextualize role puts the whole document there; ADR-0033 decision 4, amendment).
        return Prepared(redacted)

    def parse_turn(
        self,
        config: LlmConfig,
        response: Mapping[str, Any],
        offered: frozenset[str],
        fallback_model: str,
        prepared: Prepared,
    ) -> ParsedTurn:
        raw = self.usage(response)
        model = response.get("modelVersion")
        model_id = model if isinstance(model, str) and model else fallback_model
        usage = Usage(input_tokens=raw.total_input, output_tokens=raw.output_tokens)
        candidate = _candidate(response)
        if candidate is None:
            # The prompt was blocked (promptFeedback.blockReason) or nothing came back.
            return ParsedTurn(ModelTurn(model_id, "refusal", (), (), usage), raw)
        finish = str(candidate.get("finishReason") or "")
        if finish in INVALID:
            raise InvalidModelOutput("the model produced an invalid function call")
        text, calls = _parts(candidate, offered)
        stop: StopReason = "end_turn"
        if finish in REFUSALS:
            stop = "refusal"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        if calls and stop == "end_turn":
            stop = "tool_use"
        segments: tuple[AnswerSegment, ...] = ()
        dropped = 0
        if stop != "refusal" and text:
            marked = segments_from_markers(
                text,
                prepared.passages,
                require_numbers=config.citations.require_numbers_in_passage,
                sentences=config.answer_checks.sentences,
            )
            dropped = marked.dropped
            segments = tuple(
                AnswerSegment(
                    mask_aadhaar(s.text),
                    tuple(Citation(c.source, mask_aadhaar(c.cited_text)) for c in s.citations),
                )
                for s in marked.segments
            )
        turn = ModelTurn(model_id, stop, segments, tuple(calls), usage)
        return ParsedTurn(turn, raw, dropped)

    def usage(self, response: Mapping[str, Any]) -> RawUsage:
        return usage(response)

    def json_text(self, response: Mapping[str, Any]) -> str:
        candidate = _candidate(response)
        if candidate is None or str(candidate.get("finishReason") or "") in REFUSALS:
            raise InvalidModelOutput("the model declined")
        text, _ = _parts(candidate, frozenset(), allow_calls=False)
        return text

    def assembler(self, config: LlmConfig, prepared: Prepared) -> GeminiStreamAssembler:
        return GeminiStreamAssembler()


def _int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def usage(response: Mapping[str, Any]) -> RawUsage:
    """Billed tokens: prompt tokens minus cached ones are input; cached ones are cache reads;
    candidate plus thought tokens are output (thinking is billed as output)."""
    raw = response.get("usageMetadata")
    if not isinstance(raw, Mapping):
        return RawUsage(0, 0)
    prompt = _int(raw.get("promptTokenCount")) + _int(raw.get("toolUsePromptTokenCount"))
    cached = min(_int(raw.get("cachedContentTokenCount")), prompt)
    return RawUsage(
        input_tokens=prompt - cached,
        output_tokens=_int(raw.get("candidatesTokenCount")) + _int(raw.get("thoughtsTokenCount")),
        cache_read_tokens=cached,
    )


def _candidate(response: Mapping[str, Any]) -> Mapping[str, Any] | None:
    feedback = response.get("promptFeedback")
    if isinstance(feedback, Mapping) and feedback.get("blockReason"):
        return None
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0]
    return first if isinstance(first, Mapping) else None


def _parts(
    candidate: Mapping[str, Any], offered: frozenset[str], *, allow_calls: bool = True
) -> tuple[str, list[ToolCall]]:
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, Mapping) else None
    texts: list[str] = []
    calls: list[ToolCall] = []
    for index, part in enumerate(parts if isinstance(parts, list) else ()):
        if not isinstance(part, Mapping) or part.get("thought") is True:
            continue
        if isinstance(part.get("text"), str):
            texts.append(part["text"])
        call = part.get("functionCall")
        if isinstance(call, Mapping):
            if not allow_calls:
                raise InvalidModelOutput("structured output came back as a function call")
            calls.append(_call(call, part, offered, index))
    return "".join(texts), calls


def _call(
    call: Mapping[str, Any], part: Mapping[str, Any], offered: frozenset[str], index: int
) -> ToolCall:
    name, arguments = call.get("name"), call.get("args", {})
    if not isinstance(name, str) or name not in offered:
        raise InvalidModelOutput("the model called a tool that was not offered")
    if not isinstance(arguments, Mapping):
        raise InvalidModelOutput("tool arguments are not an object")
    given = call.get("id")
    call_id = given if isinstance(given, str) and given else f"{SYNTHETIC_CALL_PREFIX}{index}"
    signature = part.get("thoughtSignature")
    return ToolCall(
        call_id,
        name,
        redact_payload(arguments),
        signature=signature if isinstance(signature, str) and signature else None,
    )


class GeminiStreamAssembler:
    """Rebuilds a ``generateContent`` response from ``streamGenerateContent`` chunks (§5.1).

    Each chunk is a partial response: text parts arrive in pieces, function calls whole, the
    ``finishReason`` and final ``usageMetadata`` with the last chunk. :meth:`feed` returns the
    new text with ``[n]`` markers removed (the preview never shows passage numbers; the final
    answer is renumbered); :meth:`response` is the whole response for :meth:`GeminiCodec
    .parse_turn` and metering."""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._calls: list[dict[str, Any]] = []
        self._usage: Mapping[str, Any] = {}
        self._finish: str | None = None
        self._model: str | None = None
        self._feedback: Mapping[str, Any] | None = None
        self._stripper = MarkerStripper()
        self.complete = False

    def feed(self, event: Mapping[str, Any]) -> str | None:
        usage = event.get("usageMetadata")
        if isinstance(usage, Mapping):
            self._usage = usage
        model = event.get("modelVersion")
        if isinstance(model, str) and model:
            self._model = model
        feedback = event.get("promptFeedback")
        if isinstance(feedback, Mapping) and feedback.get("blockReason"):
            self._feedback = feedback
            self.complete = True
            return None
        candidates = event.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return None
        candidate = candidates[0]
        if not isinstance(candidate, Mapping):
            return None
        new: list[str] = []
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, Mapping) else None
        for part in parts if isinstance(parts, list) else ():
            if not isinstance(part, Mapping) or part.get("thought") is True:
                continue
            if isinstance(part.get("text"), str) and part["text"]:
                new.append(part["text"])
            if isinstance(part.get("functionCall"), Mapping):
                kept = {k: v for k, v in part.items() if k in ("functionCall", "thoughtSignature")}
                self._calls.append(kept)
        finish = candidate.get("finishReason")
        if isinstance(finish, str) and finish:
            self._finish = finish
            self.complete = True
        if not new:
            return None
        text = "".join(new)
        self._text.append(text)
        return self._stripper.feed(text) or None

    def flush(self) -> str:
        return self._stripper.flush()

    def response(self) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        if self._text:
            parts.append({"text": "".join(self._text)})
        parts += self._calls
        out: dict[str, Any] = {"modelVersion": self._model, "usageMetadata": dict(self._usage)}
        if self._feedback is not None:
            out["promptFeedback"] = dict(self._feedback)
            return out
        out["candidates"] = [
            {"content": {"role": "model", "parts": parts}, "finishReason": self._finish}
        ]
        return out


def response_text(response: Mapping[str, Any]) -> str:
    candidate = _candidate(response)
    if candidate is None:
        return ""
    text, _ = _parts(candidate, frozenset(), allow_calls=False)
    return text


def dumps(body: Mapping[str, Any]) -> bytes:
    """The request body as sent (compact UTF-8 JSON; Telugu is not escaped)."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


__all__ = [
    "INVALID",
    "REFUSALS",
    "STATIC_PREFIX",
    "SYNTHETIC_CALL_PREFIX",
    "GeminiCodec",
    "GeminiStreamAssembler",
    "contents",
    "dumps",
    "response_text",
    "usage",
]
