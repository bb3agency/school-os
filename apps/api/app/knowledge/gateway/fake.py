"""Offline, deterministic provider for local development and CI (``SOS_KB_PROVIDER_MODE=fake``).

Refused in staging/prod (``Settings`` guard and :func:`app.knowledge.gateway.build_gateway`).
It speaks the Messages API shape, so the gateway's redaction, parsing, metering, budgets and
breaker run exactly as in live mode, and the same inputs always give the same outputs:

- A question with tools offered (and ``tool_choice`` not ``none``): calls ``search_documents``
  with the question as ``query`` when that tool is offered; otherwise answers "not found".
- After tool results: a grounded answer, one text block per ``search_result`` (at most three,
  in order), each citing its source with the block's full text as ``cited_text``
  (``search_result_location``, docs/06 §7, §9). No results: an honest "not found" in the
  question's script (Telugu or English).
- Structured output (``output_config.format``): circular readings and parent notices from the
  deterministic rules of :mod:`.fake_circulars`, chunk contexts from :mod:`.fake_contextual`
  (title, subject line and nearest heading of the document in the system prompt); any other
  schema gets a minimal instance.

Token counts are estimates (4 characters per token) so metering and budgets can be exercised.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from app.knowledge.contextual.rules import SCHEMA_TAG as CONTEXT_SCHEMA_TAG
from app.knowledge.gateway.fake_circulars import structured_reply
from app.knowledge.gateway.fake_contextual import contextual_reply
from app.knowledge.gateway.schema_check import example
from app.knowledge.gateway.transport import MessagesRequest

NOT_FOUND_EN = "I could not find this in the school records you can access."
NOT_FOUND_TE = "మీరు చూడగలిగే పాఠశాల రికార్డుల్లో ఇది కనబడలేదు."
_TELUGU = re.compile(r"[ఀ-౿]")
_MAX_CITED = 3


def _tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _blocks(message: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    content = message.get("content")
    return [b for b in content if isinstance(b, Mapping)] if isinstance(content, list) else []


def _question(messages: Sequence[Mapping[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        texts = [b["text"] for b in _blocks(message) if b.get("type") == "text"]
        if texts:
            return str(texts[-1])
    return ""


def _search_results(messages: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    for message in messages:
        for block in _blocks(message):
            if block.get("type") != "tool_result":
                continue
            content = block.get("content")
            if isinstance(content, list):
                found += [
                    r
                    for r in content
                    if isinstance(r, Mapping) and r.get("type") == "search_result"
                ]
    return found


def _system(body: Mapping[str, Any]) -> str:
    system = body.get("system")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "".join(str(b.get("text", "")) for b in system if isinstance(b, Mapping))
    return ""


def _first_sentence(text: str) -> str:
    head = re.split(r"(?<=[.!?।])\s", text.strip(), maxsplit=1)[0]
    return head[:200]


class FakeTransport:
    """Deterministic Messages API stand-in. ``record=True`` keeps every request body sent."""

    name = "fake"

    def __init__(self, *, record: bool = False) -> None:
        self._record = record
        self.sent: list[Mapping[str, Any]] = []

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        body = request.body
        if self._record:
            self.sent.append(body)
        messages: list[Mapping[str, Any]] = list(body.get("messages") or ())
        output_format = (body.get("output_config") or {}).get("format")
        if isinstance(output_format, Mapping):
            schema = output_format["schema"]
            if schema.get("description") == CONTEXT_SCHEMA_TAG:
                reply: dict[str, Any] | None = contextual_reply(_system(body), _question(messages))
            else:
                reply = structured_reply(schema, _question(messages))
            value = reply if reply is not None else example(schema)
            text = json.dumps(value, ensure_ascii=False)
            content: list[dict[str, Any]] = [{"type": "text", "text": text}]
            stop = "end_turn"
        else:
            content, stop = self._turn(body, messages)
        out_text = json.dumps(content, ensure_ascii=False)
        return {
            "id": "msg_fake",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "fake"),
            "content": content,
            "stop_reason": stop,
            "usage": {
                "input_tokens": _tokens(json.dumps(body, ensure_ascii=False, default=str)),
                "output_tokens": _tokens(out_text),
            },
        }

    def stream(self, request: MessagesRequest) -> Iterator[Mapping[str, Any]]:
        """The same response as :meth:`send`, as Messages API stream events: text in pieces
        of at most :data:`STREAM_WORDS` words, citations after their text, tool input as
        JSON in two pieces. Deterministic: the same request always streams the same events."""
        return _stream_events(self.send(request))

    def _turn(
        self, body: Mapping[str, Any], messages: Sequence[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], str]:
        question = _question(messages)
        results = _search_results(messages)
        answered_tools = any(b.get("type") == "tool_result" for m in messages for b in _blocks(m))
        tools = {t.get("name") for t in body.get("tools") or () if isinstance(t, Mapping)}
        forbidden = (body.get("tool_choice") or {}).get("type") == "none"
        if not answered_tools and "search_documents" in tools and not forbidden:
            rounds = sum(1 for m in messages if m.get("role") == "assistant")
            call = {
                "type": "tool_use",
                "id": f"toolu_fake_{rounds + 1:02d}",
                "name": "search_documents",
                "input": {"query": question},
            }
            return [call], "tool_use"
        if not results:
            text = NOT_FOUND_TE if _TELUGU.search(question) else NOT_FOUND_EN
            return [{"type": "text", "text": text}], "end_turn"
        content: list[dict[str, Any]] = []
        for index, result in enumerate(results[:_MAX_CITED]):
            text = "".join(
                str(b.get("text", ""))
                for b in result.get("content") or ()
                if isinstance(b, Mapping)
            )
            citation = {
                "type": "search_result_location",
                "source": result.get("source"),
                "title": result.get("title"),
                "cited_text": text,
                "search_result_index": index,
                "start_block_index": 0,
                "end_block_index": 1,
            }
            content.append(
                {
                    "type": "text",
                    "text": f"{result.get('title')}: {_first_sentence(text)}",
                    "citations": [citation],
                }
            )
        return content, "end_turn"


STREAM_WORDS = 3
_PIECE = re.compile(r"(?:\S+\s*){1,3}|\s+")


def text_pieces(text: str) -> list[str]:
    """``text`` cut after every :data:`STREAM_WORDS` words (whitespace kept, nothing lost)."""
    return _PIECE.findall(text) or ([text] if text else [])


def _stream_events(response: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    usage = response.get("usage") or {}
    yield {
        "type": "message_start",
        "message": {
            "id": response.get("id"),
            "type": "message",
            "role": "assistant",
            "model": response.get("model"),
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": 1},
        },
    }
    for index, block in enumerate(response.get("content") or ()):
        if block.get("type") == "text":
            yield {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            }
            for piece in text_pieces(str(block.get("text", ""))):
                yield {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": piece},
                }
            for citation in block.get("citations") or ():
                yield {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "citations_delta", "citation": citation},
                }
        else:
            start = {k: v for k, v in block.items() if k != "input"}
            yield {
                "type": "content_block_start",
                "index": index,
                "content_block": {**start, "input": {}},
            }
            raw = json.dumps(block.get("input") or {}, ensure_ascii=False)
            for piece in (raw[: len(raw) // 2], raw[len(raw) // 2 :]):
                yield {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": piece},
                }
        yield {"type": "content_block_stop", "index": index}
    yield {
        "type": "message_delta",
        "delta": {"stop_reason": response.get("stop_reason")},
        "usage": {"output_tokens": usage.get("output_tokens", 0)},
    }
    yield {"type": "message_stop"}


__all__ = ["NOT_FOUND_EN", "NOT_FOUND_TE", "STREAM_WORDS", "FakeTransport", "text_pieces"]
