"""Domain values <-> Messages API dicts, with redaction on the way out (docs/06 §5, §7).

Outgoing: every string value of the request body (system prompt, questions, earlier answers,
tool arguments, search-result titles, sources and text, tool descriptions and schemas, even ids)
passes :func:`app.core.redaction.mask_aadhaar` (invariant 4; docs/06 §4.3). Masking is
deterministic, so a tool call id and the ``tool_use_id`` of its result still pair.
``mask_aadhaar`` (not ``redact``) is the rule for prompts: phone numbers and emails can be the
legitimate answer to a permitted question, Aadhaar numbers never are (core.redaction docs).

Record and document content reaches the model only as ``search_result`` blocks with citations
enabled (docs/06 §7), so answers carry ``search_result_location`` citations the service
validates (§9). Nothing here decides what the user may see: callers pass exactly the blocks the
user's scope allows (invariant 8; import-linter ``knowledge-gateway-isolated``).

Incoming: text blocks become :class:`AnswerSegment` values with their citations; ``tool_use``
blocks become :class:`ToolCall` values (only for tools offered in this request); thinking
blocks are dropped. Model output is masked too (defence in depth for invariant 4).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from app.core.redaction import mask_aadhaar
from app.knowledge.config.llm import Conversation, LlmConfig, RoleConfig
from app.knowledge.domain import (
    AnswerSegment,
    AssistantMessage,
    Citation,
    ConversationItem,
    ModelTurn,
    SearchResultBlock,
    StopReason,
    ToolCall,
    ToolResultsMessage,
    ToolSpec,
    Usage,
    UserMessage,
)
from app.knowledge.gateway.errors import GatewayMisuse, InvalidModelOutput

_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}
_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "pause_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "refusal": "refusal",
}


def redact_payload(value: Any) -> Any:
    """Mask Aadhaar numbers in every string value of a JSON-like value (keys are schema names)."""
    if isinstance(value, str):
        return mask_aadhaar(value)
    if isinstance(value, Mapping):
        return {k: redact_payload(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_payload(v) for v in value]
    return value


# --- outgoing -----------------------------------------------------------------------------------


def search_result(block: SearchResultBlock) -> dict[str, Any]:
    return {
        "type": "search_result",
        "source": block.source,
        "title": block.title,
        "content": [{"type": "text", "text": block.text}],
        "citations": {"enabled": True},
    }


def _assistant(item: AssistantMessage) -> dict[str, Any] | None:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": seg.text} for seg in item.turn.segments if seg.text.strip()
    ]
    content += [
        {"type": "tool_use", "id": c.call_id, "name": c.name, "input": dict(c.arguments)}
        for c in item.turn.tool_calls
    ]
    return {"role": "assistant", "content": content} if content else None


def _tool_results(item: ToolResultsMessage) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for outcome in item.outcomes:
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": outcome.call_id}
        if outcome.blocks:
            block["content"] = [search_result(b) for b in outcome.blocks]
        if outcome.is_error:
            block["is_error"] = True
        blocks.append(block)
    return {"role": "user", "content": blocks}


def _earlier(item: UserMessage, rules: Conversation) -> str | None:
    """The recent turns (questions, and checked answers still visible to the caller) or, for
    older callers, the earlier questions: one block under the configured header."""
    if item.earlier_turns:
        lines: list[str] = []
        for turn in item.earlier_turns:
            lines.append(f"- {turn.question}")
            if turn.answer:
                lines.append(f"  {rules.earlier_answer_label} {turn.answer}")
        return f"{rules.earlier_questions_header}\n" + "\n".join(lines)
    if item.earlier_questions:
        listed = "\n".join(f"- {q}" for q in item.earlier_questions)
        return f"{rules.earlier_questions_header}\n{listed}"
    return None


def user_texts(item: UserMessage, rules: Conversation | None) -> list[str]:
    """The texts of a user turn, the question LAST. Before it, in this order (docs/06 §5 prompt
    layout): the rolling summary, the recent turns of the conversation (FR-KB-012) and, when the
    question was rewritten as a standalone question, the question as the user wrote it; each
    introduced by its configured header (prompt text, invariant 13). The user's memory goes in
    the system prompt (:func:`memory_block`). Shared by every wire format (ADR-0033), so the
    layout is the same on every provider."""
    texts: list[str] = []
    context = (item.summary, item.earlier_turns, item.earlier_questions, item.asked_as)
    if any(context) and rules is None:
        raise GatewayMisuse("conversation context needs the configured conversation headers")
    if rules is not None:
        if item.summary:
            texts.append(f"{rules.summary_header}\n{item.summary}")
        earlier = _earlier(item, rules)
        if earlier is not None:
            texts.append(earlier)
        if item.asked_as:
            texts.append(f"{rules.rewritten_header}\n{item.asked_as}")
    texts.append(item.text)
    return texts


def _user(item: UserMessage, rules: Conversation | None) -> dict[str, Any]:
    content = [{"type": "text", "text": t} for t in user_texts(item, rules)]
    return {"role": "user", "content": content}


def memory_block(
    conversation: Sequence[ConversationItem], rules: Conversation
) -> dict[str, Any] | None:
    """The user's memory items as one system block (ADR-0034), right after the static prompt so
    the cached prefix stays the same for every user; None when there are none."""
    first = next((i for i in conversation if isinstance(i, UserMessage)), None)
    if first is None or not first.memory:
        return None
    listed = "\n".join(f"- {m}" for m in first.memory)
    return {"type": "text", "text": f"{rules.memory_header}\n{listed}"}


def messages(
    conversation: Sequence[ConversationItem], rules: Conversation | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in conversation:
        if isinstance(item, UserMessage):
            out.append(_user(item, rules))
        elif isinstance(item, AssistantMessage):
            message = _assistant(item)
            if message is not None:
                out.append(message)
        else:
            out.append(_tool_results(item))
    if not out or out[0]["role"] != "user":
        raise GatewayMisuse("a conversation starts with the user's question")
    return out


def tool_rounds_used(conversation: Sequence[ConversationItem]) -> int:
    return sum(1 for i in conversation if isinstance(i, AssistantMessage) and i.turn.tool_calls)


def tool_result_tokens(conversation: Sequence[ConversationItem], chars_per_token: int) -> int:
    chars = sum(
        len(b.title) + len(b.text)
        for i in conversation
        if isinstance(i, ToolResultsMessage)
        for o in i.outcomes
        for b in o.blocks
    )
    return math.ceil(chars / chars_per_token)


def _common(role: RoleConfig, system: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": role.model,
        "max_tokens": role.max_output_tokens,
        # Static prompt first and cached (docs/06 §12 caching); never per-request values after it.
        "system": [{"type": "text", "text": system, "cache_control": dict(_EPHEMERAL)}],
    }
    if role.thinking == "disabled":
        body["thinking"] = {"type": "disabled"}
    if role.effort is not None:
        body["output_config"] = {"effort": role.effort}
    return body


def turn_request(
    config: LlmConfig,
    role: RoleConfig,
    system: str,
    conversation: Sequence[ConversationItem],
    tools: Sequence[ToolSpec],
) -> dict[str, Any]:
    """A redacted tool-use turn. Tool choice is ``auto`` (never forced: some models reject it);
    once ``max_tool_rounds`` rounds are used it becomes ``none`` so the model must answer."""
    body = _common(role, system)
    memory = memory_block(conversation, config.conversation)
    if memory is not None:
        body["system"].append(memory)
    body["messages"] = messages(conversation, config.conversation)
    if tools:
        definitions: list[dict[str, Any]] = [
            {"name": t.name, "description": t.description, "input_schema": dict(t.input_schema)}
            for t in tools
        ]
        definitions[-1]["cache_control"] = dict(_EPHEMERAL)
        body["tools"] = definitions
        if tool_rounds_used(conversation) >= config.limits.max_tool_rounds:
            body["tool_choice"] = {"type": "none"}
    return cast(dict[str, Any], redact_payload(body))


def json_request(
    role: RoleConfig, system: str, text: str, schema: Mapping[str, object]
) -> dict[str, Any]:
    """A redacted structured-output call (``output_config.format``, not a forced tool)."""
    body = _common(role, system)
    body["messages"] = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    output_config = dict(body.get("output_config", {}))
    output_config["format"] = {"type": "json_schema", "schema": dict(schema)}
    body["output_config"] = output_config
    return cast(dict[str, Any], redact_payload(body))


# --- incoming -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawUsage:
    """Token counts as billed (cache writes and reads priced separately; NFR-CST-001)."""

    input_tokens: int
    output_tokens: int
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_write_tokens + self.cache_read_tokens


def _int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def usage(response: Mapping[str, Any]) -> RawUsage:
    raw = response.get("usage")
    if not isinstance(raw, Mapping):
        return RawUsage(0, 0)
    return RawUsage(
        input_tokens=_int(raw.get("input_tokens")),
        output_tokens=_int(raw.get("output_tokens")),
        cache_write_tokens=_int(raw.get("cache_creation_input_tokens")),
        cache_read_tokens=_int(raw.get("cache_read_input_tokens")),
    )


def _citations(block: Mapping[str, Any]) -> tuple[Citation, ...]:
    found: list[Citation] = []
    for c in block.get("citations") or ():
        if isinstance(c, Mapping) and c.get("type") == "search_result_location":
            source, cited = c.get("source"), c.get("cited_text")
            if isinstance(source, str) and isinstance(cited, str):
                found.append(Citation(source=source, cited_text=mask_aadhaar(cited)))
    return tuple(found)


def parse_turn(
    response: Mapping[str, Any], offered: frozenset[str], fallback_model: str
) -> tuple[ModelTurn, RawUsage]:
    segments: list[AnswerSegment] = []
    calls: list[ToolCall] = []
    for block in response.get("content") or ():
        if not isinstance(block, Mapping):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            segments.append(AnswerSegment(mask_aadhaar(block["text"]), _citations(block)))
        elif kind == "tool_use":
            name, call_id, arguments = block.get("name"), block.get("id"), block.get("input")
            if name not in offered or not isinstance(call_id, str):
                raise InvalidModelOutput("the model called a tool that was not offered")
            if not isinstance(arguments, Mapping):
                raise InvalidModelOutput("tool arguments are not an object")
            calls.append(ToolCall(call_id, str(name), redact_payload(arguments)))
    stop = _STOP_REASONS.get(str(response.get("stop_reason")), "end_turn")
    if calls and stop != "tool_use":
        stop = "tool_use"
    raw = usage(response)
    model = response.get("model")
    turn = ModelTurn(
        model=model if isinstance(model, str) and model else fallback_model,
        stop_reason=stop,
        segments=tuple(segments),
        tool_calls=tuple(calls),
        usage=Usage(input_tokens=raw.total_input, output_tokens=raw.output_tokens),
    )
    return turn, raw


class StreamAssembler:
    """Rebuilds a Messages API response dict from its stream events (docs/06 §5.1).

    :meth:`feed` returns the raw text of a ``text_delta`` (else None); :meth:`response` is the
    response as ``send`` would have returned it (text blocks with their citations, ``tool_use``
    blocks with their parsed input, stop reason, usage so far), so the same :func:`parse_turn`
    and metering apply to streamed and unstreamed calls. Unknown events and block types are
    ignored (thinking blocks are dropped by ``parse_turn`` anyway).
    """

    def __init__(self) -> None:
        self._model: str | None = None
        self._blocks: dict[int, dict[str, Any]] = {}
        self._json: dict[int, list[str]] = {}
        self._usage: dict[str, Any] = {}
        self._stop: str | None = None
        self.complete = False

    def feed(self, event: Mapping[str, Any]) -> str | None:
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message")
            if isinstance(message, Mapping):
                model = message.get("model")
                self._model = model if isinstance(model, str) else None
                self._add_usage(message.get("usage"))
        elif kind == "content_block_start":
            self._start_block(event)
        elif kind == "content_block_delta":
            return self._delta(event)
        elif kind == "content_block_stop":
            self._stop_block(event)
        elif kind == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, Mapping) and isinstance(delta.get("stop_reason"), str):
                self._stop = delta["stop_reason"]
            self._add_usage(event.get("usage"))
        elif kind == "message_stop":
            self.complete = True
        return None

    def _add_usage(self, usage: object) -> None:
        if isinstance(usage, Mapping):
            self._usage.update({k: v for k, v in usage.items() if v is not None})

    @staticmethod
    def _index(event: Mapping[str, Any]) -> int | None:
        index = event.get("index")
        return index if isinstance(index, int) else None

    def _start_block(self, event: Mapping[str, Any]) -> None:
        index, raw = self._index(event), event.get("content_block")
        if index is None or not isinstance(raw, Mapping):
            return
        block = dict(raw)
        if block.get("type") == "text":
            block["text"] = str(block.get("text") or "")
            block["citations"] = list(block.get("citations") or ())
        elif block.get("type") == "tool_use":
            self._json[index] = []
        self._blocks[index] = block

    def _delta(self, event: Mapping[str, Any]) -> str | None:
        index, delta = self._index(event), event.get("delta")
        block = self._blocks.get(index) if index is not None else None
        if block is None or not isinstance(delta, Mapping):
            return None
        kind = delta.get("type")
        if kind == "text_delta" and block.get("type") == "text":
            text = str(delta.get("text") or "")
            block["text"] += text
            return text or None
        if kind == "citations_delta" and block.get("type") == "text":
            block["citations"].append(delta.get("citation"))
        elif kind == "input_json_delta" and index in self._json:
            self._json[index].append(str(delta.get("partial_json") or ""))
        return None

    def _stop_block(self, event: Mapping[str, Any]) -> None:
        index = self._index(event)
        if index is None or index not in self._json:
            return
        raw = "".join(self._json.pop(index)).strip()
        try:
            self._blocks[index]["input"] = json.loads(raw) if raw else {}
        except ValueError:
            raise InvalidModelOutput("tool arguments are not valid JSON") from None

    def response(self) -> dict[str, Any]:
        return {
            "model": self._model,
            "content": [self._blocks[i] for i in sorted(self._blocks)],
            "stop_reason": self._stop,
            "usage": dict(self._usage),
        }


def response_text(response: Mapping[str, Any]) -> str:
    """Concatenated text blocks (structured output arrives as one JSON text block)."""
    return "".join(
        b["text"]
        for b in response.get("content") or ()
        if isinstance(b, Mapping) and b.get("type") == "text" and isinstance(b.get("text"), str)
    )


__all__ = [
    "RawUsage",
    "StreamAssembler",
    "json_request",
    "messages",
    "parse_turn",
    "redact_payload",
    "response_text",
    "search_result",
    "tool_result_tokens",
    "tool_rounds_used",
    "turn_request",
    "usage",
    "user_texts",
]
