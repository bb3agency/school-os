"""Synthetic documents and questions for the contextual retrieval eval (docs/06 §13.6).

Deterministic (``python -m sos_evals generate`` writes ``datasets/contextual.jsonl``; ``generate
--check`` fails on drift). Made-up schools' circulars only: no real person, office or student.

Every visible circular has the same four pages, as office circulars often do: the subject and a
short introduction (page 1), then "2. Payment", "3. Date, time and venue" and "4. Other
instructions", whose text says "the above" / "the programme" and never repeats the subject. The
title is only a reference number. Questions name the subject and ask about page 1 (control: the
page names it), 2 or 3 (the page does not). Eight English circulars, six in Telugu script and four
in code-mixed Latin-script Telugu; three restricted memos (principal only) are the best lexical
match for English payment questions and must never reach a teacher or a reranker.
"""

from __future__ import annotations

from typing import Final

from sos_evals.contextual import ContextualSet, CtxDocument, CtxPage, CtxQuestion
from sos_evals.schema import Locale

_AMOUNTS: Final = (150, 200, 250, 300, 350, 400, 450, 500, 550, 600)
_DATES: Final = (
    "10/10/2026",
    "14/10/2026",
    "17/10/2026",
    "21/10/2026",
    "24/10/2026",
    "28/10/2026",
    "31/10/2026",
    "04/11/2026",
    "07/11/2026",
    "11/11/2026",
)
_EVENT_DATES: Final = (
    "12/11/2026",
    "15/11/2026",
    "18/11/2026",
    "20/11/2026",
    "23/11/2026",
    "26/11/2026",
    "28/11/2026",
    "01/12/2026",
    "03/12/2026",
    "05/12/2026",
)
_TIMES: Final = ("9:00", "9:30", "10:00", "10:30", "11:00", "2:00", "2:30", "3:00", "8:30", "8:45")

EN_TOPICS: Final = (
    ("science exhibition", "school auditorium"),
    ("sports day", "main playground"),
    ("annual day celebrations", "open-air stage"),
    ("educational tour to Araku", "front gate bus bay"),
    ("inter-school quiz", "library hall"),
    ("yoga camp", "assembly ground"),
    ("health check-up camp", "medical room"),
    ("drawing competition", "art room"),
)
TE_TOPICS: Final = (
    ("విజ్ఞాన ప్రదర్శన", "పాఠశాల సభా మందిరం"),
    ("క్రీడా దినోత్సవం", "ప్రధాన ఆట స్థలం"),
    ("వార్షికోత్సవ వేడుకలు", "బహిరంగ వేదిక"),
    ("విహార యాత్ర", "ముఖ ద్వారం బస్సు స్థలం"),
    ("క్విజ్ పోటీ", "గ్రంథాలయ హాలు"),
    ("యోగా శిబిరం", "ప్రార్థనా మైదానం"),
)
MX_TOPICS: Final = (
    ("cultural fest", "school auditorium"),
    ("chess tournament", "library hall"),
    ("rangoli competition", "assembly ground"),
    ("tree plantation drive", "back garden"),
)
RESTRICTED_TOPICS: Final = ("science exhibition", "sports day", "annual day celebrations")


def _en(n: int, topic: str, venue: str) -> CtxDocument:
    return CtxDocument(
        id=f"ctx-en-{n + 1:02d}",
        locale="en",
        title=f"Circular No. {21 + n}/2026-27",
        subject=f"Conduct of the {topic} 2026 for classes VI to X",
        pages=(
            CtxPage(
                text=f"The school will conduct the {topic} for classes VI to X this term. Class "
                "teachers shall read this circular in every section and collect the names of "
                "interested students."
            ),
            CtxPage(
                heading="2. Payment",
                text=f"Each student shall pay Rs. {_AMOUNTS[n]} towards the above by "
                f"{_DATES[n]} at the office counter. The class teacher will collect the receipts "
                "and keep a list of students who have paid.",
            ),
            CtxPage(
                heading="3. Date, time and venue",
                text=f"The programme will begin at {_TIMES[n]} on {_EVENT_DATES[n]} at the "
                f"{venue}. Students shall report fifteen minutes early with their identity cards "
                "and school diary.",
            ),
            CtxPage(
                heading="4. Other instructions",
                text="Parents may meet the class teacher on any working day for clarifications. "
                "Students should carry drinking water and wear the school uniform.",
            ),
        ),
    )


def _te(n: int, topic: str, venue: str) -> CtxDocument:
    return CtxDocument(
        id=f"ctx-te-{n + 1:02d}",
        locale="te",
        title=f"Circular No. {31 + n}/2026-27",
        subject=f"{topic} 2026 నిర్వహణ",
        pages=(
            CtxPage(
                text=f"ఈ పాఠశాలలో 6 నుండి 10వ తరగతుల విద్యార్థుల కోసం {topic} నిర్వహిస్తున్నాము. "
                "తరగతి ఉపాధ్యాయులు ప్రతి సెక్షన్‌లో ఈ సర్క్యులర్ చదివి ఆసక్తి ఉన్న విద్యార్థుల "
                "పేర్లు సేకరించాలి."
            ),
            CtxPage(
                heading="2. రుసుము",
                text=f"ప్రతి విద్యార్థి పైన తెలిపిన కార్యక్రమం కోసం రూ. {_AMOUNTS[n]} ను "
                f"{_DATES[n]} లోపు కార్యాలయ కౌంటర్‌లో చెల్లించాలి. తరగతి ఉపాధ్యాయులు రసీదులు "
                "సేకరించి చెల్లించిన విద్యార్థుల జాబితా ఉంచాలి.",
            ),
            CtxPage(
                heading="3. తేదీ, సమయం, స్థలం",
                text=f"కార్యక్రమం {_EVENT_DATES[n]} న {_TIMES[n]} గంటలకు {venue} లో "
                "ప్రారంభమవుతుంది. విద్యార్థులు పదిహేను నిమిషాలు ముందుగా గుర్తింపు కార్డులతో హాజరు "
                "కావాలి.",
            ),
            CtxPage(
                heading="4. ఇతర సూచనలు",
                text="తల్లిదండ్రులు ఏ పని దినమైనా తరగతి ఉపాధ్యాయుడిని కలవవచ్చు. విద్యార్థులు "
                "తాగునీరు తెచ్చుకోవాలి, పాఠశాల యూనిఫాం ధరించాలి.",
            ),
        ),
    )


def _mx(n: int, topic: str, venue: str) -> CtxDocument:
    return CtxDocument(
        id=f"ctx-mx-{n + 1:02d}",
        locale="mixed",
        title=f"Circular No. {41 + n}/2026-27",
        subject=f"School lo {topic} 2026 nirvahana",
        pages=(
            CtxPage(
                text=f"Ee term lo 6 nundi 10 va taragathi vidyarthula kosam school lo {topic} "
                "nirvahistunnamu. Class teachers prathi section lo ee circular chadivi aasakthi "
                "unna vidyarthula perlu sekarinchali."
            ),
            CtxPage(
                heading="2. Rusumu",
                text=f"Prathi vidyarthi paina cheppina karyakramam kosam Rs. {_AMOUNTS[n + 5]} ni "
                f"{_DATES[n + 5]} lopu office counter lo kattali. Class teacher receipts "
                "sekarinchi kattina vidyarthula list unchali.",
            ),
            CtxPage(
                heading="3. Tedi, samayam, sthalam",
                text=f"Karyakramam {_EVENT_DATES[n + 5]} na {_TIMES[n + 5]} ki {venue} lo "
                "modalavuthundi. Vidyarthulu padihenu nimishalu mundu identity cards tho raavali.",
            ),
            CtxPage(
                heading="4. Itara suchanalu",
                text="Thallidandrulu e pani dinam aina class teacher ni kalavavachu. Vidyarthulu "
                "water bottle techukovali, school uniform vesukovali.",
            ),
        ),
    )


def _restricted(n: int, topic: str) -> CtxDocument:
    return CtxDocument(
        id=f"ctx-rs-{n + 1:02d}",
        locale="en",
        title=f"Memo No. {61 + n}/2026-27",
        subject=f"Staff contribution for the {topic}",
        restricted=True,
        pages=(
            CtxPage(
                text=f"This memo is for the principal only. It records the staff contribution for "
                f"the {topic} and must not be shared."
            ),
            CtxPage(
                heading="2. Payment",
                text=f"Each teacher shall pay Rs. {_AMOUNTS[n] + 1000} towards the {topic} fund. "
                f"The amount must be paid for the {topic} before the programme begins.",
            ),
            CtxPage(
                heading="3. Date, time and venue",
                text=f"The {topic} planning meeting will begin at 4:00 on {_DATES[n]} in the "
                f"principal's chamber. Where the {topic} will begin is decided there.",
            ),
        ),
    )


def build() -> ContextualSet:
    documents: list[CtxDocument] = []
    documents += [_en(i, t, v) for i, (t, v) in enumerate(EN_TOPICS)]
    documents += [_te(i, t, v) for i, (t, v) in enumerate(TE_TOPICS)]
    documents += [_mx(i, t, v) for i, (t, v) in enumerate(MX_TOPICS)]
    documents += [_restricted(i, t) for i, t in enumerate(RESTRICTED_TOPICS)]
    questions: list[CtxQuestion] = []

    def ask(locale: Locale, text: str, topic: str, doc: str, *, page: int, fast: bool) -> None:
        questions.append(
            CtxQuestion(
                id=f"ctxq-{len(questions) + 1:03d}",
                locale=locale,
                question=text,
                topic=topic,
                document=doc,
                page=page,
                fast=fast,
            )
        )

    for i, (topic, _) in enumerate(EN_TOPICS):
        doc, fast = f"ctx-en-{i + 1:02d}", i < 2
        ask("en", f"Which classes take part in the {topic}?", topic, doc, page=1, fast=fast)
        ask("en", f"How much must be paid for the {topic}?", topic, doc, page=2, fast=fast)
        ask("en", f"When and where will the {topic} begin?", topic, doc, page=3, fast=fast)
    for i, (topic, _) in enumerate(TE_TOPICS):
        doc, fast = f"ctx-te-{i + 1:02d}", i < 2
        ask("te", f"{topic} లో ఏ తరగతుల విద్యార్థులు పాల్గొంటారు?", topic, doc, page=1, fast=fast)
        ask("te", f"{topic} కోసం ఎంత రుసుము చెల్లించాలి?", topic, doc, page=2, fast=fast)
        ask("te", f"{topic} ఎప్పుడు, ఎక్కడ ప్రారంభమవుతుంది?", topic, doc, page=3, fast=fast)
    for i, (topic, _) in enumerate(MX_TOPICS):
        doc, fast = f"ctx-mx-{i + 1:02d}", i < 1
        ask(
            "mixed",
            f"{topic} lo e taragathula vidyarthulu palgontaru?",
            topic,
            doc,
            page=1,
            fast=fast,
        )
        ask("mixed", f"{topic} kosam entha rusumu kattali?", topic, doc, page=2, fast=fast)
        ask("mixed", f"{topic} eppudu, ekkada modalavuthundi?", topic, doc, page=3, fast=fast)
    return ContextualSet(documents=tuple(documents), questions=tuple(questions))


CASES: Final = build()

__all__ = ["CASES", "build"]
