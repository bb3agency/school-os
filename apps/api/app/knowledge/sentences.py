"""Sentences of an answer and which of them state a fact (docs/06 §9 rule 3; FR-KB-005, FR-KB-007).

:func:`split_sentences` cuts text into sentences with spans into the text. It is script-neutral
(no ASCII-only letter classes; Telugu splits on the same rules; nothing depends on letter case
except the English rule below, and Telugu has no case):

- a sentence ends at a run of ``terminators`` (``.``, ``?``, ``!``, ``।`` ...), plus any closing
  quotes or brackets, followed by whitespace or the end of the text; so ``12,500.50``,
  ``Rc.No.12`` and ``22.09.2026`` never end one;
- a single ``.`` after a configured abbreviation ("Rs. 500", "No. 12") or after a single capital
  letter that is an initial ("K. Rao") does not end one; after a class numeral ("Class X.") it
  does; a ``.`` followed by a lower-case letter ("9 a.m. on Monday") does not;
- every line break ends one: a list item (``-``, ``*``, ``+``, ``•``, ``1.``, ``1)``) starts a
  new sentence whose marker is not a sentence end, and a table row (a line starting with ``|``)
  is one unit, never split (``table_header`` when a ``|---|`` rule follows it).

:func:`is_factual` says whether a sentence must carry a citation. Every sentence is factual unless
it is markup only, a table rule or a header without digits, a short lead-in ending with ``:``, a
configured connective ("In summary.") or a "not found" sentence; a digit (any script) always makes
it factual. Negation does not: "Fees are not due on Sunday." is a claim like any other.

The rules come from ``models.yaml`` (``answer_checks.sentences``; invariant 13). Pure: no I/O.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from app.knowledge.config.llm import SentenceRules

Kind = Literal["prose", "list_item", "table_header", "table_rule", "table_row"]

# Typographic quotes close and open sentences too (models write them).
_CLOSERS: Final = "\"')]}»*_”’"  # noqa: RUF001
_OPENERS: Final = "\"'([{«*_“‘"  # noqa: RUF001
_LIST: Final = re.compile(r"[ \t]*(?:[-*+•]|\d{1,3}[.)])[ \t]+")
_TABLE_RULE: Final = re.compile(r"^\|?(?:\s*:?-{3,}:?\s*\|)*\s*:?-{3,}:?\s*\|?$")
_DIGIT: Final = re.compile(r"\d")
_WS: Final = re.compile(r"\s+")
_EMPHASIS: Final = re.compile(r"\*\*|__|`")
_HEADING: Final = re.compile(r"^(?:#{1,6}|>)\s*")


@dataclass(frozen=True, slots=True)
class Sentence:
    start: int
    end: int
    text: str
    """``source[start:end]``, without surrounding whitespace."""
    kind: Kind = "prose"


def _is_table(line: str) -> bool:
    return line.strip().startswith("|")


def _is_rule(line: str) -> bool:
    return _is_table(line) and bool(_TABLE_RULE.match(line.strip()))


def _next_visible(line: str, pos: int) -> str:
    rest = line[pos:].lstrip()
    return rest[:1]


def _ends(
    line: str, begin: int, first: int, *, run_end: int, after: int, rules: SentenceRules
) -> bool:
    """Whether the terminator run ``line[first:run_end]`` (closers up to ``after``) ends a
    sentence that began at ``begin``."""
    nxt = _next_visible(line, after)
    if nxt and unicodedata.category(nxt) == "Ll":
        return False  # "9 a.m. on Monday", "approx. five": the sentence goes on
    if line[first:run_end] != ".":
        return True
    words = line[begin:first].split()
    if not words:
        return True
    token = words[-1].lstrip(_OPENERS)
    if token.casefold() in rules.abbreviations:
        return False
    if len(token) == 1 and token.isalpha() and token.isupper():
        before = words[-2].strip(_OPENERS + _CLOSERS).casefold() if len(words) > 1 else ""
        return before in rules.class_words  # "Class X." ends; "K. Rao" is an initial
    return True


def _emit(out: list[Sentence], source: str, start: int, end: int, kind: Kind) -> bool:
    piece = source[start:end]
    stripped = piece.strip()
    if not stripped:
        return False
    lead = len(piece) - len(piece.lstrip())
    begin = start + lead
    out.append(Sentence(begin, begin + len(stripped), stripped, kind))
    return True


def _prose_line(
    out: list[Sentence], source: str, offset: int, line: str, rules: SentenceRules
) -> None:
    marker = _LIST.match(line)
    kind: Kind = "list_item" if marker else "prose"
    begin = 0
    i = marker.end() if marker else 0
    n = len(line)
    while i < n:
        if line[i] not in rules.terminators:
            i += 1
            continue
        run_end = i
        while run_end < n and line[run_end] in rules.terminators:
            run_end += 1
        after = run_end
        while after < n and line[after] in _CLOSERS:
            after += 1
        at_space = after == n or line[after].isspace()
        if at_space and _ends(line, begin, i, run_end=run_end, after=after, rules=rules):
            if _emit(out, source, offset + begin, offset + after, kind):
                kind = "prose"
            begin = after
        i = max(after, i + 1)
    _emit(out, source, offset + begin, offset + n, kind)


def split_sentences(text: str, rules: SentenceRules) -> tuple[Sentence, ...]:
    """The sentences of ``text`` in order, with spans into it (see the module docstring)."""
    out: list[Sentence] = []
    lines = text.split("\n")
    offset = 0
    for number, line in enumerate(lines):
        if _is_table(line):
            if _is_rule(line):
                kind: Kind = "table_rule"
            elif number + 1 < len(lines) and _is_rule(lines[number + 1]):
                kind = "table_header"
            else:
                kind = "table_row"
            _emit(out, text, offset, offset + len(line), kind)
        else:
            _prose_line(out, text, offset, line, rules)
        offset += len(line) + 1
    return tuple(out)


def body(sentence: Sentence) -> str:
    """The words of a sentence without list markers, table pipes or emphasis markup."""
    text = sentence.text
    if sentence.kind == "list_item":
        marker = _LIST.match(text)
        if marker:
            text = text[marker.end() :]
    elif sentence.kind.startswith("table"):
        text = " ".join(cell.strip() for cell in text.split("|") if cell.strip())
    text = _HEADING.sub("", _EMPHASIS.sub("", text))
    return _WS.sub(" ", text).strip()


def is_factual(sentence: Sentence, rules: SentenceRules) -> bool:
    """Whether the sentence states a fact and so needs a valid citation (module docstring)."""
    if sentence.kind == "table_rule":
        return False
    words = body(sentence)
    if not any(ch.isalnum() for ch in words):
        return False  # markup only
    if _DIGIT.search(words):
        return True  # dates, amounts, counts, class and admission numbers
    folded = unicodedata.normalize("NFC", words).casefold()
    return not (
        sentence.kind == "table_header"
        or any(phrase in folded for phrase in rules.not_found_phrases)
        or folded.rstrip(" .,;:!?…।॥") in rules.connectives
        or (folded.endswith(":") and len(folded.split()) <= rules.lead_in_max_words)
    )


__all__ = ["Kind", "Sentence", "body", "is_factual", "split_sentences"]
