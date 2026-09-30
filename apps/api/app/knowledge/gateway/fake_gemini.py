"""Offline Gemini wire format over the Messages-API fakes (local/CI; ADR-0033).

:class:`GeminiWireFake` speaks Vertex AI ``generateContent`` / ``streamGenerateContent`` to the
gateway and delegates the "thinking" to any Messages-API stand-in (:class:`.fake.FakeTransport`,
the circular stand-in, the eval bridge's stand-in), so local development, CI and ``make eval``
exercise the Gemini codec end to end: numbered passages, ``[n]`` markers and their server-side
mapping, function calls with thought signatures, structured output, finish reasons and usage.

Translation both ways is mechanical:

- request: ``systemInstruction`` -> ``system``; ``contents`` -> ``messages`` (``model`` ->
  ``assistant``; ``functionCall`` -> ``tool_use``; ``functionResponse`` passages ->
  ``search_result`` blocks whose ``source`` is ``passage:<n>``); ``functionDeclarations`` ->
  ``tools``; ``toolConfig`` ``NONE`` -> ``tool_choice`` ``none``; ``responseJsonSchema`` ->
  ``output_config.format``;
- response: each text block's ``search_result_location`` citations become ``[n]`` markers after
  its text; ``tool_use`` -> ``functionCall`` with a synthetic ``thoughtSignature``; stop reasons
  -> ``finishReason`` (``refusal`` -> ``SAFETY``); usage -> ``usageMetadata``.

Like Vertex AI for Gemini 3 models, a request that replays a function call without the thought
signature this fake issued is rejected (``TransportError("rejected")``), so a gateway that drops
signatures fails every offline test instead of the first live call. Refused in staging/prod.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Final

from app.knowledge.gateway.fake import text_pieces
from app.knowledge.gateway.transport import MessagesRequest, Transport, TransportError, Wire

PASSAGE_SOURCE: Final = "passage:"
_SOURCE: Final = re.compile(r"^passage:(\d+)$")
_FINISH: Final = {
    "end_turn": "STOP",
    "tool_use": "STOP",
    "stop_sequence": "STOP",
    "max_tokens": "MAX_TOKENS",
    "refusal": "SAFETY",
}


def signature_for(call_id: str) -> str:
    """The synthetic thought signature of a call (base64-like, deterministic)."""
    return "c2ln" + hashlib.sha256(call_id.encode()).hexdigest()[:24]


def _parts(message: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    parts = message.get("parts")
    return [p for p in parts if isinstance(p, Mapping)] if isinstance(parts, list) else []


def _text(value: object) -> str:
    if isinstance(value, Mapping):
        return "\n".join(str(p.get("text", "")) for p in _parts(value) if "text" in p)
    return ""


class GeminiWireFake:
    """A Gemini-wire transport around a Messages-API stand-in (see the module docstring)."""

    wire: Wire = "gemini"

    def __init__(self, inner: Transport) -> None:
        self._inner = inner
        self.sent: list[Mapping[str, Any]] = []
        """Gemini request bodies as the gateway sent them."""

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def inner(self) -> Transport:
        return self._inner

    # --- request ------------------------------------------------------------------------------

    def _messages(self, contents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for message in contents:
            role = "assistant" if message.get("role") == "model" else "user"
            blocks: list[dict[str, Any]] = []
            for index, part in enumerate(_parts(message)):
                if isinstance(part.get("text"), str):
                    blocks.append({"type": "text", "text": part["text"]})
                call = part.get("functionCall")
                if isinstance(call, Mapping):
                    call_id = str(call.get("id") or f"sos-fc-{index}")
                    if part.get("thoughtSignature") != signature_for(call_id):
                        raise TransportError("rejected", status=400)  # like Vertex, Gemini 3
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call_id,
                            "name": call.get("name"),
                            "input": dict(call.get("args") or {}),
                        }
                    )
                answer = part.get("functionResponse")
                if isinstance(answer, Mapping):
                    blocks.append(self._tool_result(answer, index))
            out.append({"role": role, "content": blocks})
        return out

    @staticmethod
    def _tool_result(answer: Mapping[str, Any], index: int) -> dict[str, Any]:
        response = answer.get("response")
        response = response if isinstance(response, Mapping) else {}
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": str(answer.get("id") or f"sos-fc-{index}"),
        }
        passages = response.get("passages")
        if isinstance(passages, list) and passages:
            block["content"] = [
                {
                    "type": "search_result",
                    "source": f"{PASSAGE_SOURCE}{p.get('n')}",
                    "title": p.get("title", ""),
                    "content": [{"type": "text", "text": p.get("text", "")}],
                    "citations": {"enabled": True},
                }
                for p in passages
                if isinstance(p, Mapping)
            ]
        if "error" in response:
            block["is_error"] = True
        return block

    def _messages_request(self, request: MessagesRequest) -> MessagesRequest:
        body = request.body
        generation = body.get("generationConfig") or {}
        out: dict[str, Any] = {
            "model": request.model or "fake",
            "max_tokens": generation.get("maxOutputTokens", 1000),
            "system": [{"type": "text", "text": _text(body.get("systemInstruction"))}],
            "messages": self._messages(body.get("contents") or ()),
        }
        declarations = [
            d
            for tool in body.get("tools") or ()
            if isinstance(tool, Mapping)
            for d in tool.get("functionDeclarations") or ()
        ]
        if declarations:
            out["tools"] = [
                {
                    "name": d.get("name"),
                    "description": d.get("description", ""),
                    "input_schema": d.get("parametersJsonSchema", {}),
                }
                for d in declarations
            ]
        mode = ((body.get("toolConfig") or {}).get("functionCallingConfig") or {}).get("mode")
        if mode == "NONE":
            out["tool_choice"] = {"type": "none"}
        schema = generation.get("responseJsonSchema")
        if isinstance(schema, Mapping):
            out["output_config"] = {"format": {"type": "json_schema", "schema": dict(schema)}}
        return MessagesRequest(body=out, timeout_s=request.timeout_s)

    # --- response -----------------------------------------------------------------------------

    @staticmethod
    def _markers(block: Mapping[str, Any]) -> str:
        numbers: list[str] = []
        for citation in block.get("citations") or ():
            match = _SOURCE.match(str((citation or {}).get("source", "")))
            if match and match.group(1) not in numbers:
                numbers.append(match.group(1))
        return "".join(f"[{n}]" for n in numbers)

    def _response(self, messages: Mapping[str, Any], model: str | None) -> dict[str, Any]:
        texts: list[str] = []
        calls: list[dict[str, Any]] = []
        for block in messages.get("content") or ():
            if not isinstance(block, Mapping):
                continue
            if block.get("type") == "text":
                text = str(block.get("text", ""))
                markers = self._markers(block)
                if markers:
                    text = f"{text.rstrip()} {markers}"
                if texts and text and not text[0].isspace() and text[0] not in ".,;:!?)":
                    text = " " + text
                texts.append(text)
            elif block.get("type") == "tool_use":
                call_id = str(block.get("id"))
                calls.append(
                    {
                        "functionCall": {
                            "id": call_id,
                            "name": block.get("name"),
                            "args": dict(block.get("input") or {}),
                        },
                        "thoughtSignature": signature_for(call_id),
                    }
                )
        parts: list[dict[str, Any]] = [{"text": "".join(texts)}] if texts else []
        parts += calls
        usage = messages.get("usage") or {}
        prompt, output = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        return {
            "candidates": [
                {
                    "content": {"role": "model", "parts": parts},
                    "finishReason": _FINISH.get(str(messages.get("stop_reason")), "STOP"),
                }
            ],
            "usageMetadata": {
                "promptTokenCount": prompt,
                "candidatesTokenCount": output,
                "totalTokenCount": prompt + output,
            },
            "modelVersion": model or messages.get("model") or "fake",
        }

    # --- Transport ----------------------------------------------------------------------------

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        self.sent.append(request.body)
        inner = self._inner.send(self._messages_request(request))
        return self._response(inner, request.model)

    def stream(self, request: MessagesRequest) -> Iterator[Mapping[str, Any]]:
        """The same response as :meth:`send` as ``streamGenerateContent`` chunks: text in
        pieces of a few words, function calls whole, then ``finishReason`` and usage."""
        return stream_chunks(self.send(request))


def stream_chunks(response: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    candidate = response["candidates"][0]
    parts = candidate["content"]["parts"]
    model = response.get("modelVersion")
    for part in parts:
        if "text" in part:
            for piece in text_pieces(str(part["text"])):
                yield {
                    "candidates": [{"content": {"role": "model", "parts": [{"text": piece}]}}],
                    "modelVersion": model,
                }
        else:
            yield {
                "candidates": [{"content": {"role": "model", "parts": [dict(part)]}}],
                "modelVersion": model,
            }
    yield {
        "candidates": [
            {"content": {"role": "model", "parts": []}, "finishReason": candidate["finishReason"]}
        ],
        "usageMetadata": response.get("usageMetadata"),
        "modelVersion": model,
    }


__all__ = ["PASSAGE_SOURCE", "GeminiWireFake", "signature_for", "stream_chunks"]
