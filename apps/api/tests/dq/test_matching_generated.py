"""Generated AP name variants map to the expected match class; performance (FR-DQ-003, FR-DQ-005).

``app.devtools.names`` produces synthetic names with register-style variants tagged SPACING,
INITIALS, SPELLING, ORDER and SCRIPT (docs/02 §6). This suite classifies every variant against
its canonical form plus unrelated name pairs (NEGATIVE), prints the confusion matrix (run
``pytest -s`` to see it) and asserts at least 95 % precision and recall for the structural
classes. Synthetic data only.
"""

from __future__ import annotations

import random
import time
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest

from app.devtools.names import VariantClass, generate_name, variants
from app.dq import matching
from app.dq.matching import MatchClass, classify

EXPECTED: dict[str, MatchClass] = {
    VariantClass.SPACING.value: MatchClass.SPACING,
    VariantClass.INITIALS.value: MatchClass.INITIALS,
    VariantClass.SPELLING.value: MatchClass.VARIANT,
    VariantClass.ORDER.value: MatchClass.ORDER,
    VariantClass.SCRIPT.value: MatchClass.EXACT,  # EXACT after transliteration (step 3)
    "NEGATIVE": MatchClass.DIFFERENT,
}
MIN_RATE = 0.95
SEED = 20260926


def _labelled_pairs(count: int, seed: int = SEED) -> list[tuple[str, str, str]]:
    """(canonical, other, label): every variant of ``count`` names plus one unrelated name."""
    rng = random.Random(seed)
    out: list[tuple[str, str, str]] = []
    for i in range(count):
        name = generate_name(rng, telugu_only=i % 2 == 0)
        out.extend((name.canonical, v.text, v.variant_class.value) for v in variants(name))
        other = generate_name(rng)
        if other.canonical != name.canonical:
            out.append((name.canonical, other.canonical, "NEGATIVE"))
    return out


def _confusion(pairs: list[tuple[str, str, str]]) -> Counter[tuple[str, MatchClass]]:
    return Counter((label, classify(a, b).match_class) for a, b, label in pairs)


def _render(matrix: Counter[tuple[str, MatchClass]]) -> str:
    columns = [c for c in MatchClass if any(k[1] is c for k in matrix)]
    width = max(len(c.value) for c in columns) + 1
    lines = ["variant \\ class".ljust(16) + "".join(c.value.rjust(width) for c in columns)]
    for label in EXPECTED:
        cells = "".join(str(matrix[(label, c)]).rjust(width) for c in columns)
        lines.append(label.ljust(16) + cells)
    return "\n".join(lines)


@pytest.fixture(scope="module")
def matrix() -> Counter[tuple[str, MatchClass]]:
    result = _confusion(_labelled_pairs(1500))
    print("\nName-matching confusion matrix (rows: generated variant, columns: class)")  # noqa: T201 (report)
    print(_render(result))  # noqa: T201 (report)
    return result


@pytest.mark.parametrize(
    "label",
    [
        VariantClass.SPACING.value,
        VariantClass.INITIALS.value,
        VariantClass.ORDER.value,
        VariantClass.SCRIPT.value,
        VariantClass.SPELLING.value,
        "NEGATIVE",
    ],
)
def test_FR_DQ_003_generated_variants_recall(
    matrix: Counter[tuple[str, MatchClass]], label: str
) -> None:
    """Share of each generated variant that lands in its expected class."""
    total = sum(n for (row, _), n in matrix.items() if row == label)
    hits = matrix[(label, EXPECTED[label])]
    assert total > 0
    assert hits / total >= MIN_RATE, (label, hits, total)


@pytest.mark.parametrize(
    "match_class",
    [MatchClass.SPACING, MatchClass.INITIALS, MatchClass.ORDER, MatchClass.EXACT],
)
def test_FR_DQ_003_structural_class_precision(
    matrix: Counter[tuple[str, MatchClass]], match_class: MatchClass
) -> None:
    """Of the pairs put in a structural class, how many were generated as that case."""
    predicted = sum(n for (_, cls), n in matrix.items() if cls is match_class)
    correct = sum(
        n
        for (label, cls), n in matrix.items()
        if cls is match_class and EXPECTED[label] is match_class
    )
    assert predicted > 0
    assert correct / predicted >= MIN_RATE, (match_class, correct, predicted)


def test_unrelated_names_are_never_structural_matches(
    matrix: Counter[tuple[str, MatchClass]],
) -> None:
    lenient = (MatchClass.EXACT, MatchClass.ORDER, MatchClass.SPACING, MatchClass.INITIALS)
    assert sum(matrix[("NEGATIVE", c)] for c in lenient) == 0


def test_FR_DQ_005_twenty_thousand_pairs_under_two_seconds(
    coverage_paused: Callable[[], AbstractContextManager[None]],
) -> None:
    """20,000 classifications (register name vs other sources) in < 2 s of CPU time.

    Workload: generated variants of synthetic names plus unrelated names, all caches cleared
    before every run, so each run does the same cold work. CPU time (not wall time) so waiting
    for the CPU does not count.

    The requirement is about the speed of the code, not about machine noise. Even CPU time grows
    when the host is busy (cache and frequency contention, other processes on the same core), so
    one unlucky run could fail a correct build. The test therefore takes the best of 3
    identical runs: noise only ever makes a run slower, so the fastest run is the
    closest measure of the code itself. The 2-second limit is unchanged; a real slowdown makes
    every run slow and still fails. Coverage tracing is paused while timing (``make test-api``
    runs with ``--cov``; branch tracing alone added ~25% and, on a busy host, pushed a 1.0 s
    workload to 2.3 s): the budget is for the code, not the tracer.
    """
    runs = 3
    pairs = [(a, b) for a, b, _ in _labelled_pairs(3500, seed=SEED + 1)][:20_000]
    assert len(pairs) == 20_000
    matching.load_match_policy()
    timings: list[float] = []
    for _ in range(runs):
        matching.load_variant_dictionary()._prepared.clear()
        matching._analyse.cache_clear()
        with coverage_paused():
            started = time.process_time()
            for a, b in pairs:
                classify(a, b)
            timings.append(time.process_time() - started)
    best = min(timings)
    shown = ", ".join(f"{t:.2f}" for t in timings)
    print(f"\n20,000 name classifications: best {best:.2f} s CPU of {shown}")  # noqa: T201
    assert best < 2.0
