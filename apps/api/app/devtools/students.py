"""Synthetic students, guardians, enrolments and per-source values with KNOWN mismatches
(docs/12 §3; NFR-MNT-002; FR-DQ-001..006 measured at school scale). NEVER real data.

SYNTHETIC ONLY (CLAUDE.md invariant 11). :func:`build_students` is a pure function of
``(dataset_version, seed, tenant plan, profile, count)``: the same inputs always give the same
students, values and manifest. Nothing here touches the database; :mod:`app.devtools.student_seeder`
applies a :class:`SchoolStudents` through the students service.

Profiles (``--profile``): ``none`` (staff only), ``small`` (400 students per school; CI and
tests) and ``full`` (2,000 per school; docs/12 §3). ``count`` overrides the size.

What every student gets (dataset ``v1``):

- one active enrolment in a current-year (2026-27) section, spread evenly over all sections,
  roll numbers 1..n per section; a date of birth that fits the class age band (DQ-007) and is
  unique within the school (so no accidental DQ-008 pairs);
- admission-register values: ``full_name`` (title or upper case), ``dob``, ``gender``,
  ``father_name``, ``mother_name`` (parents carry the child's house name), ``admission_no``
  (``SYN-00001``...), ``admission_date``, ``mother_tongue``, ``nationality``, ``category`` (C3);
- Aadhaar-as-printed: ``aadhaar_last4`` taken from an Aadhaar-LIKE number that FAILS the
  Verhoeff check (:func:`app.devtools.fake_ids.invalid_aadhaar_like`; the 12-digit number
  itself is never kept, invariant 4) and the as-printed name, date of birth and gender;
- UDISE+ name, date of birth, gender and parents; board registration (classes IX-XII only)
  in upper case; a parent form (father and mother names) for about 30 %;
- guardians father (primary, with a synthetic address) and mother. No phone numbers: a random
  10-digit mobile number could belong to a real person;
- UDISE+ PEN (11 digits) for every student and an APAAR ID for about 60 % (ADR-0037), both from
  ``udise_plus`` and unverified. The APAAR-like ID starts with ``1`` and FAILS the Verhoeff check
  (:func:`app.devtools.fake_ids.synthetic_apaar_id`), so it never looks like an Aadhaar number.
  Drawn from their own random stream, so adding them changed no other synthetic value.

DQ-022 (UDISE+ vs Aadhaar-as-printed for students without a verified APAAR ID) is expected with
every injection that makes the UDISE+ and Aadhaar-as-printed name, date of birth or gender
differ (``also``), since the seeded APAAR IDs are unverified.

Injected mismatches (:data:`INJECTIONS`): each kind has a documented rate (share of the
school's students, at least one per kind), an eligibility rule and the findings it must
produce (:class:`Expect`: rule and whether it must be serious, i.e. blocker/high). Each student
carries at most one injection so that every finding can be attributed; a duplicate pair
(DQ-008) marks both records. Selection uses a school-level stream seeded with
``(dataset_version, seed, tenant index)``, so the counts are exact and the chosen students are
the same on every run. :meth:`SchoolStudents.manifest` lists the expected findings by admission
number (no names), which :mod:`app.devtools.dq_eval` scores against real findings.

Baseline not listed in the manifest: with an export profile, DQ-005 reports every identity field
as provisional (low, ``DQ-005-UNVERIFIED``) because synthetic register values are unverified.
"""

from __future__ import annotations

import datetime as dt
import random
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from app.devtools.fake_ids import (
    invalid_aadhaar_like,
    last4,
    synthetic_apaar_id,
    synthetic_udise_pen,
)
from app.devtools.names import (
    GIVEN_FIRST,
    GIVEN_SECOND,
    PersonName,
    VariantClass,
    generate_name,
    variants,
)
from app.devtools.plan import TenantPlan

ProfileName = Literal["none", "small", "full"]
PROFILES: Final[dict[str, int]] = {"none": 0, "small": 400, "full": 2000}
DEFAULT_PROFILE: Final = "full"
MAX_STUDENTS: Final = 5000

REG: Final = "admission_register"
AAD: Final = "aadhaar_as_printed"
UDISE: Final = "udise_plus"
BOARD: Final = "board_registration"
PFORM: Final = "parent_form"

ADMISSION_PREFIX: Final = "SYN-"
YEAR_START: Final = dt.date(2026, 6, 1)  # first day of the current academic year (plan v1)
PREVIOUS_YEAR_START: Final = dt.date(2025, 6, 1)
CLASS_ORDER: Final = ("NUR", "LKG", "UKG", "I", "II", "III", "IV", "V", "VI", "VII", "VIII",
                      "IX", "X", "XI", "XII")  # fmt: skip
# Completed years on YEAR_START that every non-injected student has (inside the DQ-007 band).
CLASS_AGE: Final[dict[str, int]] = {code: 3 + i for i, code in enumerate(CLASS_ORDER)}
BOARD_CLASSES: Final = frozenset({"IX", "X", "XI", "XII"})
PARENT_FORM_RATE: Final = 0.30
APAAR_RATE: Final = 0.60  # share of students with an APAAR ID (ADR-0037)
UPPER_REGISTER_RATE: Final = 0.5

MOTHER_TONGUES: Final = (("Telugu", 80), ("Urdu", 8), ("Hindi", 6), ("Tamil", 3), ("Kannada", 2),
                         ("Odia", 1))  # fmt: skip
CATEGORIES: Final = (("obc", 45), ("general", 30), ("sc", 15), ("st", 6), ("ews", 4))
TOWNS: Final = ("Guntur", "Vijayawada", "Tenali", "Ongole", "Eluru", "Nellore", "Kakinada")
LETTERS: Final = "bcdfgklmnprstv"


# --- manifest types --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Expect:
    """A finding a kind must produce: ``serious`` = blocker/high, else low/medium/info."""

    rule_id: str
    serious: bool


@dataclass(frozen=True, slots=True)
class InjectionKind:
    key: str
    rule_id: str
    rate: float
    expects: tuple[Expect, ...]
    description: str


def _k(
    key: str,
    rule: str,
    rate: float,
    serious: bool | None,
    description: str,
    also: tuple[Expect, ...] = (),
) -> InjectionKind:
    expects = (() if serious is None else (Expect(rule, serious),)) + also
    return InjectionKind(key, rule, rate, expects, description)


# UDISE+ and Aadhaar-as-printed differ: APAAR generation would fail (ADR-0037, FR-DQ-022).
APAAR_BLOCKED: Final = (Expect("DQ-022", True),)


# Order matters: kinds with small eligible pools pick first. Rates are shares of all students.
INJECTIONS: Final[tuple[InjectionKind, ...]] = (
    _k("board_name_variant", "DQ-010", 0.010, True, "board name: spacing/initials/spelling/order"),
    _k("board_name_typo", "DQ-010", 0.005, True, "board name: one letter wrong"),
    _k("board_dob_mismatch", "DQ-010", 0.005, True, "board date of birth off by one day"),
    _k("board_gender_mismatch", "DQ-010", 0.002, True, "board gender differs"),
    _k("board_father_initials", "DQ-010", 0.005, True, "board father's name with initial"),
    _k(
        "aadhaar_dob_day_month_swap",
        "DQ-002",
        0.010,
        True,
        "Aadhaar DOB day and month swapped",
        APAAR_BLOCKED,
    ),
    _k("aadhaar_name_script", "DQ-001", 0.005, None, "Aadhaar name in Telugu script (no finding)"),
    _k(
        "aadhaar_name_spacing",
        "DQ-001",
        0.015,
        False,
        "Aadhaar name: given names joined",
        APAAR_BLOCKED,
    ),
    _k(
        "aadhaar_name_initials",
        "DQ-001",
        0.020,
        False,
        "Aadhaar name: house name as initial",
        APAAR_BLOCKED,
    ),
    _k(
        "aadhaar_name_spelling",
        "DQ-001",
        0.020,
        False,
        "Aadhaar name: spelling variant",
        APAAR_BLOCKED,
    ),
    _k(
        "aadhaar_name_order",
        "DQ-001",
        0.010,
        False,
        "Aadhaar name: given names first",
        APAAR_BLOCKED,
    ),
    _k("aadhaar_name_typo", "DQ-001", 0.020, True, "Aadhaar name: one letter wrong", APAAR_BLOCKED),
    _k(
        "aadhaar_name_different",
        "DQ-001",
        0.010,
        True,
        "Aadhaar of a different person",
        APAAR_BLOCKED,
    ),
    _k(
        "aadhaar_dob_off_by_one_day",
        "DQ-002",
        0.010,
        True,
        "Aadhaar DOB one day later",
        APAAR_BLOCKED,
    ),
    _k(
        "aadhaar_dob_off_by_one_year",
        "DQ-002",
        0.005,
        True,
        "Aadhaar DOB one year later",
        APAAR_BLOCKED,
    ),
    _k("aadhaar_gender_mismatch", "DQ-003", 0.005, True, "Aadhaar gender differs", APAAR_BLOCKED),
    _k("parent_form_father_initials", "DQ-004", 0.010, False, "parent form: father as initial"),
    _k("parent_form_mother_typo", "DQ-004", 0.010, True, "parent form: mother's name typo"),
    _k("missing_mother_name", "DQ-005", 0.005, True, "no mother's name in any source"),
    _k("name_with_digit", "DQ-006", 0.004, True, "digit typed into the name"),
    _k("name_too_long", "DQ-006", 0.003, True, "name longer than 50 characters (CISCE)"),
    _k("name_in_telugu_script", "DQ-006", 0.003, True, "register name in Telugu script"),
    _k("age_out_of_band", "DQ-007", 0.005, False, "born two years earlier: outside the band"),
    _k("duplicate_record", "DQ-008", 0.005, True, "second record of the same child"),
    _k("no_aadhaar", "DQ-009", 0.010, False, "no Aadhaar details (APAAR, UDISE+ profile)"),
    _k(
        "udise_name_spacing",
        "DQ-011",
        0.010,
        False,
        "UDISE+ name: given names joined",
        APAAR_BLOCKED,
    ),
    _k(
        "udise_dob_off_by_one_day",
        "DQ-011",
        0.005,
        False,
        "UDISE+ DOB one day later",
        APAAR_BLOCKED,
    ),
    _k("udise_mother_name_spelling", "DQ-011", 0.005, False, "UDISE+ mother's name spelling"),
    _k("enrolled_twice", "DQ-012", 0.003, True, "still active in last year's section"),
)
INJECTION_KINDS: Final[dict[str, InjectionKind]] = {k.key: k for k in INJECTIONS}
DUPLICATE_OF: Final = "duplicate_of"  # the second record of a duplicate pair


# --- specs -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GuardianSpec:
    relationship: Literal["father", "mother"]
    full_name: str
    is_primary: bool
    address: str | None = None


@dataclass(frozen=True, slots=True)
class StudentSpec:
    ordinal: int
    admission_no: str
    section: tuple[str, str]
    """``(class_code, section_name)`` in the current academic year."""
    roll_no: str
    values: tuple[tuple[str, str, str], ...]
    """``(attribute_key, source, value)``, in a stable order."""
    guardians: tuple[GuardianSpec, ...]
    previous_section: tuple[str, str] | None = None
    """An extra ACTIVE enrolment in the previous year (injection ``enrolled_twice``)."""
    injection: str | None = None
    related: str | None = None
    """Admission number of the other record of a duplicate pair."""

    def value(self, attribute_key: str, source: str) -> str | None:
        for key, src, val in self.values:
            if key == attribute_key and src == source:
                return val
        return None


@dataclass(frozen=True, slots=True)
class SchoolStudents:
    tenant_code: str
    profile: str
    students: tuple[StudentSpec, ...]
    counts: Mapping[str, int] = field(default_factory=dict)
    """Injected students per kind (both records of a duplicate pair count)."""

    def expected(self) -> dict[str, tuple[Expect, ...]]:
        """Expected findings per admission number (students without any are left out)."""
        out: dict[str, tuple[Expect, ...]] = {}
        for s in self.students:
            if s.injection is None:
                continue
            key = "duplicate_record" if s.injection == DUPLICATE_OF else s.injection
            expects = INJECTION_KINDS[key].expects
            if s.injection == DUPLICATE_OF:
                # The second record has only register values: no Aadhaar details either.
                expects = (*expects, Expect("DQ-009", False))
            if expects:
                out[s.admission_no] = expects
        return out

    def manifest(self) -> dict[str, Any]:
        """JSON-ready manifest of the injected mismatches (IDs and codes only, no names)."""
        expected = self.expected()
        return {
            "tenant_code": self.tenant_code,
            "profile": self.profile,
            "students": len(self.students),
            "baseline": {
                "rule_id": "DQ-005",
                "explanation_code": "DQ-005-UNVERIFIED",
                "note": "every provisional identity field under an export profile (low)",
            },
            "kinds": [
                {
                    "key": k.key,
                    "rule_id": k.rule_id,
                    "rate": k.rate,
                    "count": self.counts.get(k.key, 0),
                    "expects": [{"rule_id": e.rule_id, "serious": e.serious} for e in k.expects],
                    "description": k.description,
                }
                for k in INJECTIONS
            ],
            "injections": [
                {
                    "admission_no": s.admission_no,
                    "kind": s.injection,
                    "related": s.related,
                    "expects": [
                        {"rule_id": e.rule_id, "serious": e.serious}
                        for e in expected.get(s.admission_no, ())
                    ],
                }
                for s in self.students
                if s.injection is not None
            ],
        }


# --- helpers ---------------------------------------------------------------------------------


def admission_no_for(ordinal: int) -> str:
    return f"{ADMISSION_PREFIX}{ordinal:05d}"


def target_count(kind: InjectionKind, total: int) -> int:
    """How many students a kind is injected into (exact; at least one per kind)."""
    if total <= 0:
        return 0
    return max(1, round(kind.rate * total))


def _weighted(rng: random.Random, options: tuple[tuple[str, int], ...]) -> str:
    return rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]


def _typo(rng: random.Random, text: str) -> str:
    """One letter replaced inside the longest word (a keyboard slip)."""
    words = text.split()
    i = max(range(len(words)), key=lambda k: len(words[k]))
    word = words[i]
    pos = rng.randrange(2, len(word) - 1)
    choices = [c for c in LETTERS if c != word[pos].lower()]
    letter = rng.choice(choices)
    words[i] = word[:pos] + (letter.upper() if word[pos].isupper() else letter) + word[pos + 1 :]
    return " ".join(words)


def _variant(name: PersonName, cls: VariantClass) -> str | None:
    found = [v.text for v in variants(name) if v.variant_class is cls]
    if cls is VariantClass.INITIALS:
        dotted = [t for t in found if "." in t]
        found = dotted or found
    return found[0] if found else None


def _has_spelling(name: PersonName) -> bool:
    return _variant(name, VariantClass.SPELLING) is not None


def _gender(g: str) -> str:
    return "female" if g == "f" else "male"


def _flip(gender: str) -> str:
    return "male" if gender == "female" else "female"


def _shift_year(d: dt.date, years: int) -> dt.date:
    return d.replace(year=d.year + years)


def _long_name(rng: random.Random, name: PersonName) -> str:
    """A name longer than 50 characters (CISCE limit) and at most 100 (UDISE+ limit)."""
    pool = [
        t for t in (*GIVEN_FIRST[name.gender], *GIVEN_SECOND[name.gender]) if t not in name.given
    ]
    tokens = [name.surname, *name.given]
    while len(" ".join(tokens)) <= 50:
        tokens.append(rng.choice(pool))
    return " ".join(tokens)


@dataclass
class _Draft:
    """Mutable working copy of one student while the school is generated."""

    ordinal: int
    section: tuple[str, str]
    roll_no: int
    name: PersonName
    father: PersonName
    mother: PersonName
    dob: dt.date
    admission_date: dt.date
    upper: bool
    mother_tongue: str
    category: str
    aadhaar_last4: str
    parent_form: bool
    address: str
    udise_pen: str = ""
    apaar_id: str | None = None
    injection: str | None = None
    related: str | None = None

    @property
    def class_code(self) -> str:
        return self.section[0]

    @property
    def admission_no(self) -> str:
        return admission_no_for(self.ordinal)


def _eligible(kind: str, d: _Draft) -> bool:  # noqa: PLR0911 - one rule per kind reads best
    if kind.startswith("board_"):
        return d.class_code in BOARD_CLASSES
    if kind == "aadhaar_dob_day_month_swap":
        return d.dob.day <= 12 and d.dob.day != d.dob.month
    if kind in ("aadhaar_dob_off_by_one_year",):
        return not (d.dob.month == 2 and d.dob.day == 29)
    if kind in ("aadhaar_name_script", "name_in_telugu_script"):
        return d.name.telugu is not None
    if kind == "aadhaar_name_spelling":
        return _has_spelling(d.name)
    if kind == "udise_mother_name_spelling":
        return _has_spelling(d.mother)
    if kind == "enrolled_twice":
        return True
    return True


def _section_layout(plan: TenantPlan, total: int) -> list[tuple[tuple[str, str], int]]:
    """``((class, section), roll_no)`` for every slot: even spread, remainder to the first."""
    sections = list(plan.sections)
    base, extra = divmod(total, len(sections))
    out: list[tuple[tuple[str, str], int]] = []
    for i, section in enumerate(sections):
        for roll in range(1, base + (1 if i < extra else 0) + 1):
            out.append((section, roll))
    return out


def _dob_for(
    rng: random.Random, class_code: str, used: set[dt.date], age: int | None = None
) -> dt.date:
    """A date of birth giving ``age`` completed years on :data:`YEAR_START`, unused if possible."""
    years = CLASS_AGE[class_code] if age is None else age
    latest = _shift_year(YEAR_START, -years)  # exactly ``years`` old on YEAR_START
    earliest = _shift_year(YEAR_START, -(years + 1)) + dt.timedelta(days=1)
    span = (latest - earliest).days + 1
    for _ in range(span):
        candidate = earliest + dt.timedelta(days=rng.randrange(span))
        if candidate not in used:
            return candidate
    return earliest + dt.timedelta(days=rng.randrange(span))  # pragma: no cover - > 365 in a class


def _draft(
    stream: str, ordinal: int, slot: tuple[tuple[str, str], int], used: set[dt.date]
) -> _Draft:
    rng = random.Random(f"{stream}:student:{ordinal}")  # noqa: S311 - synthetic test data
    section, roll = slot
    name = generate_name(rng)
    father_given = generate_name(rng, gender="m").given
    mother_given = generate_name(rng, gender="f").given
    father = PersonName(surname=name.surname, given=father_given, gender="m")
    mother = PersonName(surname=name.surname, given=mother_given, gender="f")
    dob = _dob_for(rng, section[0], used)
    used.add(dob)
    class_index = CLASS_ORDER.index(section[0])
    joined = rng.randint(0, class_index)
    admitted_year = YEAR_START.year - (class_index - joined)
    admission_date = dt.date(admitted_year, 6, rng.randint(1, 20))
    number = invalid_aadhaar_like(rng)  # Verhoeff-INVALID look-alike; only last 4 are kept
    town = rng.choice(TOWNS)
    # ADR-0037: own stream, so the PEN and APAAR ID leave every other value unchanged.
    ids = random.Random(f"{stream}:national-ids:{ordinal}")  # noqa: S311 - synthetic test data
    udise_pen = synthetic_udise_pen(ids)
    apaar_id = synthetic_apaar_id(ids) if ids.random() < APAAR_RATE else None
    return _Draft(
        ordinal=ordinal,
        section=section,
        roll_no=roll,
        name=name,
        father=father,
        mother=mother,
        dob=dob,
        admission_date=admission_date,
        upper=rng.random() < UPPER_REGISTER_RATE,
        mother_tongue=_weighted(rng, MOTHER_TONGUES),
        category=_weighted(rng, CATEGORIES),
        aadhaar_last4=last4(number),
        parent_form=rng.random() < PARENT_FORM_RATE,
        address=f"H.No {rng.randint(1, 40)}-{rng.randint(1, 300)}, Synthetic Colony, {town}",
        udise_pen=udise_pen,
        apaar_id=apaar_id,
    )


def _assign(rng: random.Random, drafts: list[_Draft]) -> Counter[str]:
    counts: Counter[str] = Counter()
    total = len(drafts)
    for kind in INJECTIONS:
        want = target_count(kind, total)
        if kind.key == "duplicate_record":
            _assign_duplicates(rng, drafts, want, counts)
            continue
        pool = [d for d in drafts if d.injection is None and _eligible(kind.key, d)]
        for d in rng.sample(pool, min(want, len(pool))):
            d.injection = kind.key
            counts[kind.key] += 1
    return counts


def _assign_duplicates(
    rng: random.Random, drafts: list[_Draft], want: int, counts: Counter[str]
) -> None:
    """Pairs in the same class: the second slot becomes another record of the first child."""
    free = [d for d in drafts if d.injection is None]
    rng.shuffle(free)
    pairs = 0
    for first in free:
        if pairs >= want:
            break
        if first.injection is not None:
            continue
        partner = next(
            (
                d
                for d in free
                if d is not first and d.injection is None and d.class_code == first.class_code
            ),
            None,
        )
        if partner is None:
            continue
        first.injection = "duplicate_record"
        partner.injection = DUPLICATE_OF
        first.related, partner.related = partner.admission_no, first.admission_no
        counts["duplicate_record"] += 2
        pairs += 1


def _register_name(d: _Draft, text: str) -> str:
    return text.upper() if d.upper else text


# --- building values -------------------------------------------------------------------------


Values = dict[tuple[str, str], str]


def _base_values(d: _Draft) -> Values:
    gender = _gender(d.name.gender)
    dob = d.dob.isoformat()
    v: Values = {
        ("full_name", REG): _register_name(d, d.name.canonical),
        ("dob", REG): dob,
        ("gender", REG): gender,
        ("father_name", REG): _register_name(d, d.father.canonical),
        ("mother_name", REG): _register_name(d, d.mother.canonical),
        ("admission_no", REG): d.admission_no,
        ("admission_date", REG): d.admission_date.isoformat(),
        ("mother_tongue", REG): d.mother_tongue,
        ("nationality", REG): "Indian",
        ("category", REG): d.category,
        ("aadhaar_last4", AAD): d.aadhaar_last4,
        ("aadhaar_name_as_printed", AAD): d.name.canonical,
        ("aadhaar_dob_as_printed", AAD): dob,
        ("aadhaar_gender_as_printed", AAD): gender,
        ("full_name", UDISE): d.name.canonical,
        ("dob", UDISE): dob,
        ("gender", UDISE): gender,
        ("father_name", UDISE): d.father.canonical,
        ("mother_name", UDISE): d.mother.canonical,
        ("udise_pen", UDISE): d.udise_pen,
    }
    if d.apaar_id is not None:
        v[("apaar_id", UDISE)] = d.apaar_id
    if d.class_code in BOARD_CLASSES:
        v |= {
            ("full_name", BOARD): d.name.canonical.upper(),
            ("dob", BOARD): dob,
            ("gender", BOARD): gender,
            ("father_name", BOARD): d.father.canonical.upper(),
            ("mother_name", BOARD): d.mother.canonical.upper(),
        }
    if d.parent_form or d.injection in ("parent_form_father_initials", "parent_form_mother_typo"):
        v |= {
            ("father_name", PFORM): d.father.canonical,
            ("mother_name", PFORM): d.mother.canonical,
        }
    return v


def _set_all(v: Values, attribute_key: str, value: str, *, aadhaar_key: str | None = None) -> None:
    for key, source in list(v):
        if key == attribute_key:
            v[(key, source)] = value
    if aadhaar_key is not None and (aadhaar_key, AAD) in v:
        v[(aadhaar_key, AAD)] = value


def _other_person(rng: random.Random, name: PersonName) -> str:
    while True:
        other = generate_name(rng, gender=name.gender)
        if other.surname != name.surname and not set(other.given) & set(name.given):
            return other.canonical


Injector = Callable[[random.Random, _Draft, Values], None]


def _set(key: tuple[str, str], make: Callable[[random.Random, _Draft], str]) -> Injector:
    def inject(rng: random.Random, d: _Draft, v: Values) -> None:
        v[key] = make(rng, d)

    return inject


def _everywhere(attribute_key: str, make: Callable[[random.Random, _Draft], str]) -> Injector:
    """The same value in every source (so only the format/age rule reacts)."""
    aadhaar_key = {"full_name": "aadhaar_name_as_printed", "dob": "aadhaar_dob_as_printed"}

    def inject(rng: random.Random, d: _Draft, v: Values) -> None:
        _set_all(v, attribute_key, make(rng, d), aadhaar_key=aadhaar_key.get(attribute_key))

    return inject


def _drop(predicate: Callable[[tuple[str, str]], bool]) -> Injector:
    def inject(rng: random.Random, d: _Draft, v: Values) -> None:
        for key in [k for k in v if predicate(k)]:
            del v[key]

    return inject


def _variant_or(cls: VariantClass, fallback: Callable[[PersonName], str]) -> Callable[
    [random.Random, _Draft], str
]:  # fmt: skip
    return lambda rng, d: _variant(d.name, cls) or fallback(d.name)


def _board_variant(rng: random.Random, d: _Draft) -> str:
    cls = rng.choice([VariantClass.SPACING, VariantClass.INITIALS, VariantClass.ORDER])
    return (_variant(d.name, cls) or d.name.given_first).upper()


def _later(days: int = 0, years: int = 0) -> Callable[[random.Random, _Draft], str]:
    return lambda rng, d: (_shift_year(d.dob, years) + dt.timedelta(days=days)).isoformat()


def _older_by_two(rng: random.Random, d: _Draft) -> str:
    born = d.dob.replace(day=28) if (d.dob.month, d.dob.day) == (2, 29) else d.dob
    return _shift_year(born, -2).isoformat()


def _canonical(name: PersonName) -> str:
    return name.canonical


def _flipped(rng: random.Random, d: _Draft) -> str:
    return _flip(_gender(d.name.gender))


AAD_NAME: Final = ("aadhaar_name_as_printed", AAD)
AAD_DOB: Final = ("aadhaar_dob_as_printed", AAD)

INJECTORS: Final[dict[str, Injector]] = {
    "board_name_variant": _set(("full_name", BOARD), _board_variant),
    "board_name_typo": _set(("full_name", BOARD), lambda r, d: _typo(r, d.name.canonical).upper()),
    "board_dob_mismatch": _set(("dob", BOARD), _later(days=1)),
    "board_gender_mismatch": _set(("gender", BOARD), _flipped),
    "board_father_initials": _set(
        ("father_name", BOARD), lambda r, d: d.father.initials_form.upper()
    ),
    "aadhaar_dob_day_month_swap": _set(
        AAD_DOB, lambda r, d: d.dob.replace(month=d.dob.day, day=d.dob.month).isoformat()
    ),
    "aadhaar_name_script": _set(AAD_NAME, lambda r, d: d.name.telugu or d.name.canonical),
    "aadhaar_name_spacing": _set(AAD_NAME, _variant_or(VariantClass.SPACING, _canonical)),
    "aadhaar_name_initials": _set(AAD_NAME, _variant_or(VariantClass.INITIALS, _canonical)),
    "aadhaar_name_spelling": _set(AAD_NAME, _variant_or(VariantClass.SPELLING, _canonical)),
    "aadhaar_name_order": _set(AAD_NAME, lambda r, d: d.name.given_first),
    "aadhaar_name_typo": _set(AAD_NAME, lambda r, d: _typo(r, d.name.canonical)),
    "aadhaar_name_different": _set(AAD_NAME, lambda r, d: _other_person(r, d.name)),
    "aadhaar_dob_off_by_one_day": _set(AAD_DOB, _later(days=1)),
    "aadhaar_dob_off_by_one_year": _set(AAD_DOB, _later(years=1)),
    "aadhaar_gender_mismatch": _set(("aadhaar_gender_as_printed", AAD), _flipped),
    "parent_form_father_initials": _set(
        ("father_name", PFORM), lambda r, d: d.father.initials_form
    ),
    "parent_form_mother_typo": _set(
        ("mother_name", PFORM), lambda r, d: _typo(r, d.mother.canonical)
    ),
    "missing_mother_name": _drop(lambda k: k[0] == "mother_name"),
    "name_with_digit": _everywhere(
        "full_name", lambda r, d: f"{d.name.canonical}{r.randint(1, 9)}"
    ),
    "name_too_long": _everywhere("full_name", lambda r, d: _long_name(r, d.name)),
    "name_in_telugu_script": _everywhere(
        "full_name", lambda r, d: d.name.telugu or d.name.canonical
    ),
    "age_out_of_band": _everywhere("dob", _older_by_two),
    "no_aadhaar": _drop(lambda k: k[1] == AAD),
    "udise_name_spacing": _set(
        ("full_name", UDISE), _variant_or(VariantClass.SPACING, lambda n: n.given_first)
    ),
    "udise_dob_off_by_one_day": _set(("dob", UDISE), _later(days=1)),
    "udise_mother_name_spelling": _set(
        ("mother_name", UDISE),
        lambda r, d: _variant(d.mother, VariantClass.SPELLING) or d.mother.canonical,
    ),
}


def _inject(rng: random.Random, d: _Draft, v: Values) -> None:
    injector = INJECTORS.get(d.injection or "")
    if injector is not None:
        injector(rng, d, v)


def _duplicate_values(d: _Draft, original: _Draft, v_original: Values) -> Values:
    """The second record of a child: register values only, the name written another way."""
    alt = _variant(original.name, VariantClass.INITIALS) or original.name.canonical
    out: Values = {k: val for k, val in v_original.items() if k[1] == REG}
    out[("full_name", REG)] = _register_name(d, alt)
    out[("admission_no", REG)] = d.admission_no
    out[("admission_date", REG)] = d.admission_date.isoformat()
    return out


_ORDER: Final = (
    "full_name",
    "dob",
    "gender",
    "father_name",
    "mother_name",
    "admission_no",
    "admission_date",
    "mother_tongue",
    "nationality",
    "category",
    "aadhaar_last4",
    "aadhaar_name_as_printed",
    "aadhaar_dob_as_printed",
    "aadhaar_gender_as_printed",
    "udise_pen",
    "apaar_id",
)
_SOURCES: Final = (REG, AAD, UDISE, BOARD, PFORM)


def _ordered(v: Values) -> tuple[tuple[str, str, str], ...]:
    def rank(item: tuple[tuple[str, str], str]) -> tuple[int, int]:
        (key, source), _ = item
        return (_SOURCES.index(source), _ORDER.index(key))

    return tuple((k, s, val) for (k, s), val in sorted(v.items(), key=rank))


def _previous_section(section: tuple[str, str], sections: set[tuple[str, str]]) -> tuple[str, str]:
    class_index = CLASS_ORDER.index(section[0])
    previous = CLASS_ORDER[max(class_index - 1, 0)]
    return (previous, section[1]) if (previous, section[1]) in sections else (previous, "A")


def build_students(
    plan: TenantPlan,
    *,
    dataset_version: str,
    seed: int,
    profile: str = DEFAULT_PROFILE,
    count: int | None = None,
) -> SchoolStudents:
    """Every synthetic student of one school (pure and deterministic)."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; known: {sorted(PROFILES)}")
    total = PROFILES[profile] if count is None else count
    if not 0 <= total <= MAX_STUDENTS:
        raise ValueError(f"students per school must be between 0 and {MAX_STUDENTS}")
    used: set[dt.date] = set()
    stream = f"{dataset_version}:{seed}:tenant:{plan.index}"
    drafts = [
        _draft(stream, i + 1, slot, used) for i, slot in enumerate(_section_layout(plan, total))
    ]
    school_rng = random.Random(f"{stream}:injections")  # noqa: S311 - synthetic test data
    counts = _assign(school_rng, drafts)
    by_no = {d.admission_no: d for d in drafts}
    sections = set(plan.sections)
    built: dict[str, Values] = {}
    specs: list[StudentSpec] = []
    # Originals first so a duplicate can copy its partner's register values.
    for d in sorted(drafts, key=lambda x: (x.injection == DUPLICATE_OF, x.ordinal)):
        rng = random.Random(f"{stream}:inject:{d.ordinal}")  # noqa: S311 - synthetic test data
        if d.injection == DUPLICATE_OF and d.related is not None:
            original = by_no[d.related]
            d.name, d.father, d.mother, d.dob = (
                original.name,
                original.father,
                original.mother,
                original.dob,
            )
            v = _duplicate_values(d, original, built[original.admission_no])
        else:
            v = _base_values(d)
            _inject(rng, d, v)
        built[d.admission_no] = v
    for d in drafts:
        v = built[d.admission_no]
        guardians: list[GuardianSpec] = []
        if ("father_name", REG) in v:
            guardians.append(GuardianSpec("father", v[("father_name", REG)], True, d.address))
        if ("mother_name", REG) in v:
            guardians.append(GuardianSpec("mother", v[("mother_name", REG)], False))
        specs.append(
            StudentSpec(
                ordinal=d.ordinal,
                admission_no=d.admission_no,
                section=d.section,
                roll_no=str(d.roll_no),
                values=_ordered(v),
                guardians=tuple(guardians),
                previous_section=(
                    _previous_section(d.section, sections)
                    if d.injection == "enrolled_twice"
                    else None
                ),
                injection=d.injection,
                related=d.related,
            )
        )
    return SchoolStudents(
        tenant_code=plan.code, profile=profile, students=tuple(specs), counts=dict(counts)
    )


StudentBuilder = Callable[[TenantPlan], SchoolStudents]
