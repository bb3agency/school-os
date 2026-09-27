"""Name matching for data-quality checks (docs/02 §6, FR-DQ-003).

:func:`classify` compares two name values (e.g. admission register vs Aadhaar-as-printed) and
returns the first match class that applies, in the specification's order::

    EXACT  ORDER  SPACING  INITIALS  VARIANT  TYPO  DIFFERENT     (+ MISSING for empty input)

Pipeline (normalisation is :mod:`app.core.textnorm`; this module adds the matching rules):

1. Tokens: NFC, zero-width characters dropped, split on spaces, dots, hyphens, underscores,
   commas and slashes (the textnorm separators); each piece becomes one
   :func:`~app.core.textnorm.comparison_key` token. A single letter is an initial; a two-letter
   digraph from the policy (``CH.``) and a single Telugu syllable (``కె.``) are initials when
   followed by a dot.
2. Telugu script: before transliteration, vowel length is folded (long ii, ee, uu, oo, aa become
   short; vocalic r becomes "ri") because Latin school spellings write short vowels
   ("KOMMINENI" for కొమ్మినేని, not "KOMMINEENI"). When
   exactly one side contains Telugu (cross-script), tokens are compared on their phonetic key,
   so transliteration differences never count as a mismatch (the SCRIPT case is EXACT).
3. Classes are decided by a token alignment: every token on each side is paired once. Pairs
   can be equal tokens, adjacent tokens joined on one side (SPACING), an initial with a word
   that starts with it (INITIALS), tokens equal after the variant dictionary and phonetic key
   (VARIANT), or tokens whose Jaro-Winkler or trigram similarity reaches the threshold (TYPO).
   Each stage allows the kinds of the stages before it, and order is free from ORDER on, so a
   name with several differences gets the most severe class among them ("VENKATASAI K" vs
   "KOMMINENI VENKATA SAI" is INITIALS). From INITIALS on, at least one full word must pair.
4. ``details`` explains the result with token positions, pair kinds, metrics and counts. It
   never contains the names themselves (safe to store with findings and to log).

The result is symmetric (``classify(a, b)`` and ``classify(b, a)`` give the same class) and
never raises for any string input.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import resources
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from rapidfuzz.distance import JaroWinkler

from app.core.textnorm import comparison_key, has_telugu, phonetic_key, transliterate_telugu
from app.dq.explanations import load_explanations
from app.dq.rules import Severity


class MatchClass(StrEnum):
    EXACT = "EXACT"
    ORDER = "ORDER"
    SPACING = "SPACING"
    INITIALS = "INITIALS"
    VARIANT = "VARIANT"
    TYPO = "TYPO"
    DIFFERENT = "DIFFERENT"
    MISSING = "MISSING"

    @property
    def explanation_code(self) -> str:
        return f"NM-{self.value}"


# Evaluation order of docs/02 §6 (MISSING is decided before any of these).
MATCH_CLASS_ORDER: Final[tuple[MatchClass, ...]] = (
    MatchClass.EXACT,
    MatchClass.ORDER,
    MatchClass.SPACING,
    MatchClass.INITIALS,
    MatchClass.VARIANT,
    MatchClass.TYPO,
    MatchClass.DIFFERENT,
)
_STAGE_RANK: Final[dict[MatchClass, int]] = {c: i for i, c in enumerate(MATCH_CLASS_ORDER)}


# --- policy -----------------------------------------------------------------------------------


class Thresholds(BaseModel):
    """Tunable matching parameters (``config/match_classes.yaml`` ``thresholds``)."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    jaro_winkler_min: float = Field(default=0.92, gt=0.5, le=1.0)
    trigram_min: float = Field(default=0.80, gt=0.2, le=1.0)
    max_tokens: int = Field(default=10, ge=2, le=16)
    max_join_tokens: int = Field(default=3, ge=2, le=6)
    digraph_initials: tuple[str, ...] = ("CH", "SH", "TH", "KH", "GH", "BH", "PH", "DH")
    require_full_word: bool = True

    @field_validator("digraph_initials")
    @classmethod
    def _digraphs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not re.fullmatch(r"[A-Z]{2}", item):
                raise ValueError("digraph initials are two upper-case Latin letters")
        return tuple(sorted(set(value)))


class ClassPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: Severity | None
    explanation_code: str


class MatchPolicy(BaseModel):
    """Thresholds plus default severity per class (docs/02 §6); overridable per tenant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    thresholds: Thresholds = Thresholds()
    classes: dict[MatchClass, ClassPolicy]

    @model_validator(mode="after")
    def _complete(self) -> MatchPolicy:
        missing = [c.value for c in MatchClass if c not in self.classes]
        if missing:
            raise ValueError(f"match classes without a policy: {missing}")
        for cls, policy in self.classes.items():
            if policy.explanation_code != cls.explanation_code:
                raise ValueError(f"{cls}: explanation code must be {cls.explanation_code}")
        if self.classes[MatchClass.EXACT].severity is not None:
            raise ValueError("EXACT never raises a finding")
        return self

    def severity_for(self, match_class: MatchClass | str) -> Severity | None:
        return self.classes[MatchClass(match_class)].severity

    def with_overrides(self, overrides: Mapping[str, Any]) -> MatchPolicy:
        """A new policy with ``overrides`` deep-merged in and re-validated.

        Example: ``{"thresholds": {"trigram_min": 0.85}, "classes": {"INITIALS":
        {"severity": "low"}}}``. Unknown keys and invalid values raise ``ValidationError``.
        """
        merged = _deep_merge(self.model_dump(mode="json"), overrides)
        return MatchPolicy.model_validate(merged)


def _deep_merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overrides.items():
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            out[key] = _deep_merge(current, value)
        else:
            out[key] = value
    return out


def _read_config(name: str) -> Any:
    return yaml.safe_load(resources.files("app.dq").joinpath(f"config/{name}").read_text("utf-8"))


@functools.cache
def load_match_policy() -> MatchPolicy:
    """The packaged policy (``config/match_classes.yaml``); codes checked against the texts."""
    policy = MatchPolicy.model_validate(_read_config("match_classes.yaml"))
    catalog = load_explanations()
    for cls_policy in policy.classes.values():
        if cls_policy.explanation_code not in catalog.match_classes:
            raise ValueError(f"no EN/TE text for {cls_policy.explanation_code}")
    return policy


# --- variant dictionary -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VariantGroup:
    """Spellings of one name token; ``members[0]`` is the representative."""

    id: str
    members: tuple[str, ...]


_phonetic: Final = functools.lru_cache(maxsize=65536)(phonetic_key)


def _token_form(raw: str) -> str:
    key = comparison_key(raw)
    if not key or " " in key:
        raise ValueError(f"variant entries must be single name tokens: {raw!r}")
    return key


def _normalise_groups(groups: Iterable[VariantGroup | Sequence[str]]) -> list[VariantGroup]:
    ordered: list[VariantGroup] = []
    for n, group in enumerate(groups):
        if isinstance(group, VariantGroup):
            gid, members = group.id, group.members
        else:
            gid, members = f"extension-{n + 1}", tuple(group)
        forms = tuple(dict.fromkeys(_token_form(m) for m in members))
        if len(forms) < 2:
            raise ValueError(f"variant group {gid!r} needs two or more spellings")
        ordered.append(VariantGroup(gid, forms))
    return ordered


def _join_groups(ordered: list[VariantGroup]) -> tuple[VariantGroup, ...]:
    """Union of groups that share a phonetic key; the earliest group names the result."""
    parent = list(range(len(ordered)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for i, group in enumerate(ordered):
        for member in group.members:
            key = _phonetic(member)
            if key in owner:
                a, b = find(owner[key]), find(i)
                parent[max(a, b)] = min(a, b)
            else:
                owner[key] = i
    merged: dict[int, list[str]] = {}
    for i, group in enumerate(ordered):
        merged.setdefault(find(i), []).extend(group.members)
    return tuple(
        VariantGroup(ordered[root].id, tuple(dict.fromkeys(members)))
        for root, members in sorted(merged.items())
    )


class VariantDictionary:
    """Groups of equivalent name tokens, looked up by phonetic key (docs/02 §6 step 4).

    Groups that share a spelling (same phonetic key) are joined, so a tenant extension that
    adds ``[LAKSHMI, LACHMI]`` extends the built-in Lakshmi group. ``distinct`` pairs are never
    equivalent: a group may not contain both members, and :func:`classify` never accepts one
    as a variant or typo of the other.
    """

    def __init__(
        self,
        groups: Iterable[VariantGroup | Sequence[str]],
        distinct: Iterable[Sequence[str]] = (),
        *,
        version: str = "1",
    ) -> None:
        self.version = version
        self._groups = _join_groups(_normalise_groups(groups))

        self._distinct: frozenset[frozenset[str]] = frozenset(
            frozenset(_phonetic(_token_form(m)) for m in pair) for pair in distinct
        )
        self._distinct_pairs = tuple(tuple(_token_form(m) for m in pair) for pair in distinct)
        if any(len(pair) != 2 for pair in self._distinct_pairs):
            raise ValueError("distinct entries are pairs of tokens")

        self._index: dict[str, str] = {}
        for group in self._groups:
            keys = {_phonetic(m) for m in group.members}
            for pair in self._distinct:
                if len(pair) == 2 and pair <= keys:
                    raise ValueError(f"variant group {group.id!r} joins a distinct pair")
            for key in keys:
                self._index[key] = group.members[0]
        self._vkey_cache: dict[str, str] = {}
        # Per-name preparation cache used by classify (bounded; see _prepare).
        self._prepared: dict[tuple[str, bool, int], _Prepared] = {}

    # -- construction ---------------------------------------------------------------------

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> VariantDictionary:
        groups = [
            VariantGroup(str(g["id"]), tuple(str(m) for m in g["members"]))
            for g in raw.get("groups", [])
        ]
        distinct = [tuple(str(m) for m in pair) for pair in raw.get("distinct", [])]
        return cls(groups, distinct, version=str(raw.get("version", "1")))

    def merged(
        self,
        groups: Iterable[VariantGroup | Sequence[str]],
        distinct: Iterable[Sequence[str]] = (),
    ) -> VariantDictionary:
        """This dictionary plus tenant ``groups`` (joined where they share a spelling)."""
        return VariantDictionary(
            (*self._groups, *groups),
            (*self._distinct_pairs, *distinct),
            version=f"{self.version}+ext",
        )

    # -- lookups --------------------------------------------------------------------------

    @property
    def groups(self) -> tuple[VariantGroup, ...]:
        return self._groups

    def __len__(self) -> int:
        return len(self._groups)

    def representative(self, token: str) -> str | None:
        """The group representative for ``token`` (any spelling), or ``None``."""
        key = comparison_key(token)
        return self._index.get(_phonetic(key)) if key else None

    def variant_key(self, token_text: str) -> str:
        """Phonetic key of the token's representative (the token itself when not listed)."""
        cached = self._vkey_cache.get(token_text)
        if cached is None:
            rep = self._index.get(_phonetic(token_text), token_text)
            cached = _phonetic(rep)
            self._vkey_cache[token_text] = cached
        return cached

    def spelling(self, token_text: str) -> str:
        """The representative spelling of an already-normalised token."""
        return self._index.get(_phonetic(token_text), token_text)

    def are_variants(self, a: str, b: str) -> bool:
        ka, kb = comparison_key(a), comparison_key(b)
        if not ka or not kb or self.is_distinct(ka, kb):
            return False
        return self.variant_key(ka) == self.variant_key(kb)

    def is_distinct(self, a: str, b: str) -> bool:
        return frozenset((_phonetic(a), _phonetic(b))) in self._distinct


@functools.cache
def load_variant_dictionary() -> VariantDictionary:
    """The packaged dictionary (``config/variants.yaml``)."""
    return VariantDictionary.from_mapping(_read_config("variants.yaml"))


# --- similarity metrics -----------------------------------------------------------------------

_WORD_RE: Final = re.compile(r"[^\W_]+")


@functools.lru_cache(maxsize=65536)
def _trigrams(text: str) -> frozenset[str]:
    grams: set[str] = set()
    for word in _WORD_RE.findall(text.lower()):
        padded = f"  {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return frozenset(grams)


def trigram_similarity(a: str, b: str) -> float:
    """PostgreSQL ``pg_trgm`` ``similarity()``: shared / all padded word trigrams."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 1.0 if ta == tb and a == b else 0.0
    return len(ta & tb) / len(ta | tb)


def jaro_winkler(a: str, b: str) -> float:
    """Jaro-Winkler similarity (rapidfuzz, prefix weight 0.1)."""
    return float(JaroWinkler.similarity(a, b))


# --- tokenisation -----------------------------------------------------------------------------

# Same separators as textnorm.comparison_key; kept per piece so a trailing dot is visible.
_PIECE_RE: Final = re.compile(r"([^\s.\-_,/]+)(\.?)")
_SPLIT_AFTER_UPPER: Final = re.compile(r"[\s.\-_,/]+")
# Zero-width space/joiners, word joiner, soft hyphen, BOM: invisible, dropped before matching.
_ZERO_WIDTH: Final = dict.fromkeys((0x200B, 0x200C, 0x200D, 0x2060, 0x00AD, 0xFEFF))
# Vowel length folded before transliteration (module docstring, step 2). Code points, as the
# combining signs are unreadable on their own: long vowel sign / letter -> short one.
_TELUGU_FOLD: Final = str.maketrans(
    {
        chr(0x0C3E): "",  # vowel sign AA -> inherent a
        chr(0x0C40): chr(0x0C3F),  # vowel sign II -> I
        chr(0x0C47): chr(0x0C46),  # vowel sign EE -> E
        chr(0x0C42): chr(0x0C41),  # vowel sign UU -> U
        chr(0x0C4B): chr(0x0C4A),  # vowel sign OO -> O
        chr(0x0C06): chr(0x0C05),  # letter AA -> A
        chr(0x0C08): chr(0x0C07),  # letter II -> I
        chr(0x0C0F): chr(0x0C0E),  # letter EE -> E
        chr(0x0C0A): chr(0x0C09),  # letter UU -> U
        chr(0x0C13): chr(0x0C12),  # letter OO -> O
        # vocalic R sign -> virama + RA + sign I ("kri" as in Krishna, not "kru")
        chr(0x0C43): chr(0x0C4D) + chr(0x0C30) + chr(0x0C3F),
        chr(0x0C0B): chr(0x0C30) + chr(0x0C3F),  # letter vocalic R -> RA + sign I
    }
)
_VIRAMA: Final = chr(0x0C4D)


def _is_telugu_consonant(ch: str) -> bool:
    code = ord(ch)
    return 0x0C15 <= code <= 0x0C39 or 0x0C58 <= code <= 0x0C5A


def _is_telugu_vowel_sign(ch: str) -> bool:
    code = ord(ch)
    return 0x0C3E <= code <= 0x0C4C or code in (0x0C62, 0x0C63)


@dataclass(frozen=True, slots=True)
class _Token:
    text: str  # comparison-key form (Latin, upper case)
    initial: bool
    phon: str  # textnorm.phonetic_key(text)


@dataclass(frozen=True, slots=True)
class _Name:
    tokens: tuple[_Token, ...]
    telugu: bool
    key: str  # match_key form: tokens joined by spaces, initials as "K."

    @classmethod
    def of(cls, tokens: Sequence[_Token], telugu: bool) -> _Name:
        key = " ".join(f"{t.text}." if t.initial else t.text for t in tokens)
        return cls(tuple(tokens), telugu, key)


_EMPTY: Final = _Name((), False, "")


def _make_token(text: str, *, initial: bool) -> _Token:
    return _Token(text, initial, _phonetic(text))


@functools.lru_cache(maxsize=32768)
def _analyse(text: str, digraphs: frozenset[str]) -> _Name:
    text = unicodedata.normalize("NFC", text).translate(_ZERO_WIDTH)
    telugu = has_telugu(text)
    tokens: list[_Token] = []
    for match in _PIECE_RE.finditer(text):
        piece, dotted = match.group(1), bool(match.group(2))
        if telugu and has_telugu(piece):
            if (
                dotted
                and _is_telugu_consonant(piece[0])
                and (len(piece) == 1 or (len(piece) == 2 and _is_telugu_vowel_sign(piece[1])))
            ):
                letters = transliterate_telugu(piece[0] + _VIRAMA).upper()
                if letters.isascii() and letters.isalpha():
                    tokens.append(_make_token(letters, initial=True))
                    continue
            piece = piece.translate(_TELUGU_FOLD)
            keys = comparison_key(piece).split(" ")
        else:
            # comparison_key without Telugu is upper-case + split on its separators; the piece
            # has none, and the text is NFC already.
            keys = _SPLIT_AFTER_UPPER.split(piece.upper())
        for key in keys:
            if not key:
                continue
            initial = len(key) == 1 or (dotted and key in digraphs)
            tokens.append(_make_token(key, initial=initial))
    return _Name.of(tokens, telugu)


@functools.lru_cache(maxsize=64)
def _digraph_set(digraphs: tuple[str, ...]) -> frozenset[str]:
    return frozenset(digraphs)


def match_key(text: str, *, thresholds: Thresholds | None = None) -> str:
    """Normalised comparison form used by :func:`classify` (initials shown as ``K.``).

    Idempotent: ``match_key(match_key(x)) == match_key(x)``. For matching only, not display.
    """
    th = thresholds or load_match_policy().thresholds
    return _analyse(text, _digraph_set(th.digraph_initials)).key


# --- result -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Outcome of :func:`classify`.

    ``similarity`` is an informative 0..1 score of the whole names (Jaro-Winkler of the
    token-sorted variant keys; 1.0 for EXACT, 0.0 for MISSING). Classes are decided by the
    token alignment, not by this score (whole-name scores over-rate names that share a long
    surname: "RAVI KUMAR" / "RAJU KUMAR" scores 0.92 but is DIFFERENT).
    ``details`` holds token positions, pair kinds, metrics and counts, never name text.
    """

    match_class: MatchClass
    similarity: float
    explanation_code: str
    details: Mapping[str, Any] = field(default_factory=dict)


# --- alignment --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Pair:
    a: tuple[int, ...]
    b: tuple[int, ...]
    kind: str  # same | joined | initial | variant | typo
    word: bool  # both sides are words (not initials)
    via: str | None = None  # variant: dictionary | phonetic
    metric: str | None = None  # typo: jaro_winkler | trigram | both
    score: float | None = None

    def as_detail(self) -> dict[str, Any]:
        out: dict[str, Any] = {"a": list(self.a), "b": list(self.b), "kind": self.kind}
        if self.via is not None:
            out["via"] = self.via
        if self.metric is not None:
            out["metric"] = self.metric
            out["score"] = self.score
        return out


_KIND_COST: Final[dict[str, int]] = {"same": 0, "joined": 1, "initial": 2, "variant": 3, "typo": 4}

_Span = tuple[int, int]  # (start, length)


class _SideIndex:
    """One side's runs of up to ``max_join`` adjacent words, with keys (built once per name).

    ``base``: run -> base key: the text (the phonetic key across scripts), a joined run being
    compared as one word. ``variant`` (built on first use, only VARIANT/TYPO need it): run ->
    phonetic key of the run spelt with the variant groups' representatives.
    """

    __slots__ = ("_by_variant", "_variant", "base", "by_base", "has_initial", "runs", "variants")

    def __init__(
        self,
        tokens: tuple[_Token, ...],
        *,
        cross: bool,
        variants: VariantDictionary,
        max_join: int,
    ) -> None:
        self.variants = variants
        self.has_initial = any(t.initial for t in tokens)
        self.runs: dict[_Span, tuple[_Token, ...]] = {}
        self.base: dict[_Span, str] = {}
        self.by_base: dict[str, list[_Span]] = {}
        for start in range(len(tokens)):
            texts = ""
            for length in range(1, max_join + 1):
                end = start + length
                if end > len(tokens) or tokens[end - 1].initial:
                    break
                token = tokens[end - 1]
                texts += token.text
                if length == 1:
                    base = token.phon if cross else token.text
                else:
                    base = _phonetic(texts) if cross else texts
                span = (start, length)
                self.runs[span] = tokens[start:end]
                self.base[span] = base
                self.by_base.setdefault(base, []).append(span)
        self._variant: dict[_Span, str] | None = None
        self._by_variant: dict[str, list[_Span]] = {}

    @property
    def variant(self) -> dict[_Span, str]:
        if self._variant is None:
            self._variant = {}
            for span, run in self.runs.items():
                spelled = "".join(self.variants.spelling(t.text) for t in run)
                key = _phonetic(spelled)
                self._variant[span] = key
                self._by_variant.setdefault(key, []).append(span)
        return self._variant

    @property
    def by_variant(self) -> dict[str, list[_Span]]:
        _ = self.variant
        return self._by_variant


class _Aligner:
    """Pairs every token of ``a`` and ``b`` exactly once, using the kinds a stage allows.

    Depth-first over ``a``'s tokens in order (``b``'s in any order), cheapest pair kinds
    first, with failed states memoised; names are short, so the search stays small.
    """

    def __init__(
        self,
        a: _Name,
        b: _Name,
        stage: MatchClass,
        *,
        variants: VariantDictionary,
        thresholds: Thresholds,
        index: tuple[_SideIndex, _SideIndex],
    ) -> None:
        self.a, self.b = a.tokens, b.tokens
        self.cross = a.telugu != b.telugu
        rank = _STAGE_RANK[stage]
        self.allow_initials = rank >= _STAGE_RANK[MatchClass.INITIALS]
        self.allow_variant = rank >= _STAGE_RANK[MatchClass.VARIANT]
        self.allow_typo = rank >= _STAGE_RANK[MatchClass.TYPO]
        self.need_word = thresholds.require_full_word and self.allow_initials
        self.variants = variants
        self.jw_min = thresholds.jaro_winkler_min
        self.tri_min = thresholds.trigram_min
        self.max_join = thresholds.max_join_tokens
        self.full_b = (1 << len(self.b)) - 1
        self.failed: set[tuple[int, int, bool]] = set()
        self.ia, self.ib = index
        empty: dict[Any, Any] = {}
        self.var_a = self.ia.variant if self.allow_variant else empty
        self.var_b = self.ib.variant if self.allow_variant else empty
        self.by_var_b = self.ib.by_variant if self.allow_variant else empty

    def run(self) -> list[_Pair] | None:
        if len(self.a) == len(self.b):
            positional = self._positional()
            if positional is not None:
                return positional
        return self._step(0, 0, False)

    def _positional(self) -> list[_Pair] | None:
        """Token i with token i on both sides (the usual case); ``None`` if any pair fails."""
        pairs: list[_Pair] = []
        has_word = False
        for i, (ta, tb) in enumerate(zip(self.a, self.b, strict=True)):
            if ta.initial or tb.initial:
                initial, other = (ta, tb) if ta.initial else (tb, ta)
                kind = self._initial_kind(initial, other)
                if kind is None:
                    return None
                pairs.append(_Pair((i,), (i,), kind, False))
                continue
            pair = self._single_word_pair(i, i)
            if pair is None:
                return None
            pairs.append(pair)
            has_word = True
        return pairs if has_word or not self.need_word else None

    def _single_word_pair(self, i: int, j: int) -> _Pair | None:
        """The cheapest pair of word ``a[i]`` with word ``b[j]`` (no joins)."""
        if self.ia.base[(i, 1)] == self.ib.base[(j, 1)]:
            return _Pair((i,), (j,), "same", True)
        if not self.allow_variant:
            return None
        var_a, var_b = self.var_a[(i, 1)], self.var_b[(j, 1)]
        if var_a == var_b:
            return self._variant_pair((i,), j, 1)
        return self._typo_pair((i,), j, 1, var_a, var_b) if self.allow_typo else None

    def _step(self, ia: int, used: int, has_word: bool) -> list[_Pair] | None:
        if ia == len(self.a):
            if used == self.full_b and (has_word or not self.need_word):
                return []
            return None
        state = (ia, used, has_word)
        if state in self.failed:
            return None
        for pair in self._candidates(ia, used):
            mask = 0
            for j in pair.b:
                mask |= 1 << j
            rest = self._step(ia + len(pair.a), used | mask, has_word or pair.word)
            if rest is not None:
                return [pair, *rest]
        self.failed.add(state)
        return None

    def _candidates(self, ia: int, used: int) -> list[_Pair]:
        """Pairs for ``a[ia]`` with free tokens of ``b``, cheapest kind first."""
        by_kind: dict[str, list[_Pair]] = {kind: [] for kind in _KIND_COST}
        tok = self.a[ia]
        if tok.initial or self.ib.has_initial:
            for j, other in enumerate(self.b):
                if used >> j & 1 or not (tok.initial or other.initial):
                    continue
                initial, word = (tok, other) if tok.initial else (other, tok)
                kind = self._initial_kind(initial, word)
                if kind is not None:
                    by_kind[kind].append(_Pair((ia,), (j,), kind, False))
        if not tok.initial:
            for length_a in range(1, self.max_join + 1):
                if (ia, length_a) not in self.ia.base:
                    break
                self._word_candidates(ia, length_a, used, by_kind)
        return [pair for kind in _KIND_COST for pair in by_kind[kind]]

    def _word_candidates(
        self, ia: int, la: int, used: int, by_kind: dict[str, list[_Pair]]
    ) -> None:
        """Word pairs for the run ``a[ia:ia+la]`` with free runs of ``b``."""
        span_a = (ia, la)
        base_a = self.ia.base[span_a]
        a_idx = tuple(range(ia, ia + la))

        def free(j: int, lb: int) -> bool:
            return not (la > 1 and lb > 1) and not (used >> j) & ((1 << lb) - 1)

        for j, lb in self.ib.by_base.get(base_a, ()):
            if free(j, lb):
                kind = "joined" if la > 1 or lb > 1 else "same"
                by_kind[kind].append(_Pair(a_idx, tuple(range(j, j + lb)), kind, True))
        if not self.allow_variant:
            return
        var_a = self.var_a[span_a]
        for j, lb in self.by_var_b.get(var_a, ()):
            if free(j, lb) and self.ib.base[(j, lb)] != base_a:
                pair = self._variant_pair(a_idx, j, lb)
                if pair is not None:
                    by_kind["variant"].append(pair)
        if self.allow_typo:
            for (j, lb), var_b in self.var_b.items():
                if var_b == var_a or self.ib.base[(j, lb)] == base_a or not free(j, lb):
                    continue
                pair = self._typo_pair(a_idx, j, lb, var_a, var_b)
                if pair is not None:
                    by_kind["typo"].append(pair)

    def _initial_kind(self, initial: _Token, other: _Token) -> str | None:
        if other.initial:
            same = initial.phon == other.phon if self.cross else initial.text == other.text
            return "same" if same else None
        if other.text == initial.text:  # "CH." vs "CH"
            return "same"
        if not self.allow_initials:
            return None
        if other.text.startswith(initial.text):
            return "initial"
        return "initial" if self.cross and other.phon.startswith(initial.phon) else None

    def _distinct(self, a_idx: tuple[int, ...], j: int, lb: int) -> bool:
        return (
            len(a_idx) == 1
            and lb == 1
            and self.variants.is_distinct(self.a[a_idx[0]].text, self.b[j].text)
        )

    def _variant_pair(self, a_idx: tuple[int, ...], j: int, lb: int) -> _Pair | None:
        if self._distinct(a_idx, j, lb):
            return None
        tokens = (*(self.a[i] for i in a_idx), *self.b[j : j + lb])
        via = (
            "dictionary"
            if any(self.variants.spelling(t.text) != t.text for t in tokens)
            else "phonetic"
        )
        return _Pair(a_idx, tuple(range(j, j + lb)), "variant", True, via=via)

    def _typo_pair(
        self, a_idx: tuple[int, ...], j: int, lb: int, var_a: str, var_b: str
    ) -> _Pair | None:
        jw = JaroWinkler.similarity(var_a, var_b, score_cutoff=self.jw_min)
        jw_ok = jw >= self.jw_min
        # A word of n letters has n + 1 padded trigrams, so the shorter/longer ratio bounds
        # the trigram similarity; skip the set arithmetic when it cannot reach the threshold.
        shorter, longer = sorted((len(var_a), len(var_b)))
        tri = 0.0
        if jw_ok or (shorter + 1) >= self.tri_min * (longer + 1):
            tri = trigram_similarity(var_a, var_b)
        tri_ok = tri >= self.tri_min
        if not (jw_ok or tri_ok) or self._distinct(a_idx, j, lb):
            return None
        metric = "both" if jw_ok and tri_ok else ("jaro_winkler" if jw_ok else "trigram")
        score = round(max(jw if jw_ok else 0.0, tri if tri_ok else 0.0), 4)
        return _Pair(a_idx, tuple(range(j, j + lb)), "typo", True, metric=metric, score=score)


# --- classification ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Prepared:
    """Per-name data that does not depend on the other side (cached per dictionary)."""

    name: _Name
    sorted_variant_keys: str
    has_initial: bool
    texts: list[str]
    phons: list[str]
    sorted_texts: list[str]
    sorted_phons: list[str]
    _letters: str | None = None
    _joined_phon: str | None = None
    index: dict[bool, _SideIndex] = field(default_factory=dict)  # keyed by cross-script flag

    def keys(self, cross: bool) -> list[str]:
        return self.phons if cross else self.texts

    def sorted_keys(self, cross: bool) -> list[str]:
        return self.sorted_phons if cross else self.sorted_texts

    def joined(self, cross: bool) -> str:
        """The name without spaces (phonetic key of it across scripts)."""
        if not cross:
            return "".join(self.texts)
        if self._joined_phon is None:
            self._joined_phon = _phonetic("".join(self.texts))
        return self._joined_phon

    @property
    def letters(self) -> str:
        if self._letters is None:
            self._letters = "".join(sorted("".join(self.texts)))
        return self._letters

    def index_for(self, cross: bool, variants: VariantDictionary, max_join: int) -> _SideIndex:
        index = self.index.get(cross)
        if index is None:
            index = _SideIndex(self.name.tokens, cross=cross, variants=variants, max_join=max_join)
            self.index[cross] = index
        return index


_PREPARED_CACHE_LIMIT: Final = 32768


def _prepare(name: _Name, variants: VariantDictionary, max_join: int) -> _Prepared:
    cache = variants._prepared
    key = (name.key, name.telugu, max_join)
    prepared = cache.get(key)
    if prepared is None:
        if len(cache) >= _PREPARED_CACHE_LIMIT:
            cache.clear()
        texts = [t.text for t in name.tokens]
        phons = [t.phon for t in name.tokens]
        prepared = _Prepared(
            name,
            " ".join(sorted(variants.variant_key(t) for t in texts)),
            any(t.initial for t in name.tokens),
            texts,
            phons,
            sorted(texts),
            sorted(phons),
        )
        cache[key] = prepared
    return prepared


def _similarity(a: _Prepared, b: _Prepared) -> float:
    sa, sb = a.sorted_variant_keys, b.sorted_variant_keys
    return round(jaro_winkler(sa, sb), 4)


def _spacing_groups(keys_a: Sequence[str], keys_b: Sequence[str]) -> list[_Pair]:
    """Consecutive groups with equal text, for two key lists whose concatenations are equal."""
    pairs: list[_Pair] = []
    i = j = 0
    while i < len(keys_a) and j < len(keys_b):
        ga, gb = [i], [j]
        la, lb = len(keys_a[i]), len(keys_b[j])
        i, j = i + 1, j + 1
        while la != lb:
            if la < lb:
                ga.append(i)
                la += len(keys_a[i])
                i += 1
            else:
                gb.append(j)
                lb += len(keys_b[j])
                j += 1
        kind = "same" if len(ga) == 1 and len(gb) == 1 else "joined"
        pairs.append(_Pair(tuple(ga), tuple(gb), kind, True))
    return pairs


def _result(cls: MatchClass, similarity: float, details: dict[str, Any]) -> MatchResult:
    return MatchResult(cls, similarity, cls.explanation_code, details)


def _classify(
    a: _Name, b: _Name, variants: VariantDictionary, thresholds: Thresholds
) -> MatchResult:
    if not a.tokens or not b.tokens:
        side = "both" if not a.tokens and not b.tokens else ("a" if not a.tokens else "b")
        return _result(MatchClass.MISSING, 0.0, {"missing": side})

    cross = a.telugu != b.telugu
    details: dict[str, Any] = {
        "script": "cross" if cross else "same",
        "tokens": {"a": len(a.tokens), "b": len(b.tokens)},
    }
    if not cross and a.key == b.key:
        return _result(MatchClass.EXACT, 1.0, details)
    join = thresholds.max_join_tokens
    pa, pb = _prepare(a, variants, join), _prepare(b, variants, join)
    whole = _whole_name_class(pa, pb, cross, details)
    if whole is not None:
        return whole
    similarity = _similarity(pa, pb)
    if max(len(a.tokens), len(b.tokens)) > thresholds.max_tokens:
        return _result(MatchClass.DIFFERENT, similarity, {**details, "reason": "token_limit"})
    aligned = _aligned_class(pa, pb, cross, variants, thresholds)
    if aligned is not None:
        stage, pair_details = aligned
        return _result(stage, similarity, {**details, **pair_details})
    common = Counter(variants.variant_key(t.text) for t in a.tokens if not t.initial) & Counter(
        variants.variant_key(t.text) for t in b.tokens if not t.initial
    )
    return _result(
        MatchClass.DIFFERENT, similarity, {**details, "common_words": sum(common.values())}
    )


def _whole_name_class(
    pa: _Prepared, pb: _Prepared, cross: bool, details: dict[str, Any]
) -> MatchResult | None:
    """EXACT, ORDER or SPACING decided on the whole token lists (no alignment needed)."""
    keys_a, keys_b = pa.keys(cross), pb.keys(cross)
    if keys_a == keys_b:
        return _result(MatchClass.EXACT, 1.0, details)
    if pa.sorted_keys(cross) == pb.sorted_keys(cross):
        return _result(MatchClass.ORDER, _similarity(pa, pb), {**details, "reordered": True})
    # docs/02 §6: SPACING is "equal after removing spaces". Across scripts the joined text is
    # compared on its phonetic key.
    if pa.joined(cross) == pb.joined(cross):
        extra: dict[str, Any] = {"reordered": False}
        if not cross:
            extra["pairs"] = [p.as_detail() for p in _spacing_groups(keys_a, keys_b)]
        return _result(MatchClass.SPACING, _similarity(pa, pb), {**details, **extra})
    return None


def _aligned_class(
    pa: _Prepared,
    pb: _Prepared,
    cross: bool,
    variants: VariantDictionary,
    thresholds: Thresholds,
) -> tuple[MatchClass, dict[str, Any]] | None:
    """The first stage (SPACING..TYPO) whose token alignment succeeds, with pair details."""
    join = thresholds.max_join_tokens
    index = (pa.index_for(cross, variants, join), pb.index_for(cross, variants, join))
    # Stages that cannot succeed are skipped (same result, less work): SPACING pairs equal
    # words or joined runs only, so both sides hold the same letters; without any initial,
    # the INITIALS stage allows exactly what SPACING allows.
    skip = set()
    if not cross and pa.letters != pb.letters:
        skip.add(MatchClass.SPACING)
    if not (pa.has_initial or pb.has_initial):
        skip.add(MatchClass.INITIALS)
    for stage in MATCH_CLASS_ORDER[2:-1]:
        if stage in skip:
            continue
        aligner = _Aligner(
            pa.name, pb.name, stage, variants=variants, thresholds=thresholds, index=index
        )
        pairs = aligner.run()
        if pairs is None:
            continue
        pairs.sort(key=lambda p: p.a[0])
        b_order = [p.b[0] for p in pairs]
        details: dict[str, Any] = {
            "reordered": b_order != sorted(b_order),
            "pairs": [p.as_detail() for p in pairs],
        }
        metrics = {p.metric for p in pairs if p.metric is not None}
        if "both" in metrics or metrics >= {"jaro_winkler", "trigram"}:
            details["typo_metric"] = "both"
        elif metrics:
            details["typo_metric"] = metrics.pop()
        return stage, details
    return None


def _swap_sides(details: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(details)
    if "missing" in out and out["missing"] in ("a", "b"):
        out["missing"] = "b" if out["missing"] == "a" else "a"
    if "tokens" in out:
        out["tokens"] = {"a": out["tokens"]["b"], "b": out["tokens"]["a"]}
    if "pairs" in out:
        swapped = [{**p, "a": p["b"], "b": p["a"]} for p in out["pairs"]]
        swapped.sort(key=lambda p: p["a"][0])
        out["pairs"] = swapped
        b_order = [p["b"][0] for p in swapped]
        out["reordered"] = b_order != sorted(b_order)
    return out


def classify(
    a: str | None,
    b: str | None,
    *,
    variants: VariantDictionary | None = None,
    thresholds: Thresholds | None = None,
) -> MatchResult:
    """Match class of two name values (docs/02 §6); see the module docstring.

    ``variants`` defaults to the packaged dictionary and ``thresholds`` to the packaged policy;
    pass tenant-specific ones (``VariantDictionary.merged``, ``MatchPolicy.with_overrides``).
    ``None``, empty and punctuation-only values give ``MISSING``.
    """
    th = thresholds if thresholds is not None else load_match_policy().thresholds
    vd = variants if variants is not None else load_variant_dictionary()
    digraphs = _digraph_set(th.digraph_initials)
    na = _analyse(a, digraphs) if a else _EMPTY
    nb = _analyse(b, digraphs) if b else _EMPTY
    # Symmetry by construction: always classify in a canonical side order, then map back.
    if (na.key, na.telugu) > (nb.key, nb.telugu):
        result = _classify(nb, na, vd, th)
        return MatchResult(
            result.match_class,
            result.similarity,
            result.explanation_code,
            _swap_sides(result.details),
        )
    return _classify(na, nb, vd, th)
