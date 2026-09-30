"""The scripted Ask conversations of ``datasets/conversations.jsonl`` (ADR-0034; docs/06 §13.5).

Synthetic schools, documents and people only; every document carries a unique marker so a leak
is visible wherever it lands. ``python -m sos_evals generate`` writes them; ``generate --check``
fails when the committed file drifts.
"""

from __future__ import annotations

from typing import Final

from sos_evals.conversations import ConvDoc, ConversationCase, ConvPerson, ConvStep

U1 = ConvPerson(key="U1", role="office_staff")
U2 = ConvPerson(key="U2", role="office_staff")
T9A = ConvPerson(key="U3", role="class_teacher", section="9A")

KESTREL = ConvDoc(
    key="D1",
    title="Kestrel science fair",
    content="The Kestrel science fair is on 14/11/2026 in hall K2. Reference KES-101.",
    marker="KES-101",
)
SWAN = ConvDoc(
    key="D2",
    title="Swan concert",
    content="The Swan concert for classes 6 to 10 is on 21/11/2026 at 17:00. Reference SWN-102.",
    marker="SWN-102",
)


def _with_marker(doc: ConvDoc, marker: str) -> ConvDoc:
    """The same document with another marker (every case has its own markers)."""
    return doc.model_copy(
        update={"marker": marker, "content": doc.content.replace(doc.marker, marker)}
    )


def _doc(key: str, title: str, content: str, marker: str, **kw: object) -> ConvDoc:
    return ConvDoc.model_validate(
        {"key": key, "title": title, "content": content, "marker": marker, **kw}
    )


def ask(question: str, **kw: object) -> ConvStep:
    return ConvStep.model_validate({"action": "ask", "question": question, **kw})


CASES: Final[tuple[ConversationCase, ...]] = (
    ConversationCase(
        id="conv-en-follow-up",
        category="context",
        locale="en",
        docs=(KESTREL, SWAN),
        people=(U1,),
        steps=(
            ask("When is the Kestrel science fair?", expect_sources=("D1",)),
            ask("Where is it held?", expect_sources=("D1",)),
        ),
        note="A follow-up with a pronoun is understood through the conversation (rewrite).",
    ),
    ConversationCase(
        id="conv-te-follow-up",
        category="context",
        locale="te",
        docs=(
            _doc(
                "D1",
                "కెస్ట్రెల్ సైన్స్ ఫెయిర్",
                "కెస్ట్రెల్ సైన్స్ ఫెయిర్ 14/11/2026న హాల్ K2లో జరుగుతుంది. సూచిక KTE-103.",
                "KTE-103",
            ),
        ),
        people=(U1,),
        steps=(
            ask("కెస్ట్రెల్ సైన్స్ ఫెయిర్ ఎప్పుడు?", expect_sources=("D1",)),
            ask("అది ఎక్కడ జరుగుతుంది?", expect_sources=("D1",)),
        ),
    ),
    ConversationCase(
        id="conv-long-coherence",
        category="long",
        locale="en",
        docs=(
            _doc(
                "D1",
                "Osprey sports meet",
                "The Osprey sports meet is on 12/12/2026 at the district stadium. OSP-104.",
                "OSP-104",
            ),
            _with_marker(SWAN, "SWN-105"),
            _doc(
                "D3",
                "Robin library week",
                "Robin library week starts on 01/12/2026. ROB-106.",
                "ROB-106",
            ),
            _doc(
                "D4",
                "Wren uniform rule",
                "The Wren uniform rule applies from 05/01/2027. WRN-107.",
                "WRN-107",
            ),
        ),
        people=(U1,),
        steps=(
            ask("When is the Osprey sports meet?", expect_sources=("D1",)),
            ask("When is the Swan concert?", expect_sources=("D2",)),
            ask("When does Robin library week start?", expect_sources=("D3",)),
            ask("From when does the Wren uniform rule apply?", expect_sources=("D4",)),
            ask("What time is the Swan concert?", expect_sources=("D2",)),
            ask("Is the Osprey meet at the district stadium?", expect_sources=("D1",)),
        ),
        note="Turns beyond the recent window reach the model through the rolling summary only.",
    ),
    ConversationCase(
        id="conv-permission-revoked",
        category="permission",
        locale="en",
        docs=(
            _doc(
                "D1",
                "Yak committee",
                "The Yak committee meets on 05/12/2026 in room Y7. Reference YAK-108.",
                "YAK-108",
                audience="member_u1",
            ),
        ),
        people=(U1,),
        steps=(
            ask("When does the Yak committee meet?", expect_sources=("D1",)),
            ConvStep(action="revoke", doc="D1"),
            ask("Who attends it?"),
        ),
        note="After access is withdrawn, neither history, summary nor search sends the document.",
    ),
    ConversationCase(
        id="conv-cross-user",
        category="permission",
        locale="en",
        docs=(
            _doc(
                "D1",
                "Heron audit",
                "The Heron audit visit is on 09/12/2026. Reference HER-109.",
                "HER-109",
                audience="member_u1",
            ),
            _with_marker(SWAN, "SWN-110"),
        ),
        people=(U1, U2),
        steps=(
            ask("When is the Heron audit visit?", who="U1", expect_sources=("D1",)),
            ask("What did the other staff member ask about the audit?", who="U2"),
            ask("When is the Swan concert?", who="U2", expect_sources=("D2",)),
        ),
        note="Another person's questions and restricted documents never reach this person.",
    ),
    ConversationCase(
        id="conv-regenerate",
        category="revision",
        locale="en",
        docs=(_with_marker(KESTREL, "KES-111"),),
        people=(U1,),
        steps=(
            ask("When is the Kestrel science fair?", expect_sources=("D1",)),
            ConvStep(action="regenerate", target=0, expect_sources=("D1",)),
        ),
    ),
    ConversationCase(
        id="conv-edit",
        category="revision",
        locale="en",
        docs=(
            _with_marker(KESTREL, "KES-112"),
            _with_marker(SWAN, "SWN-113"),
        ),
        people=(U1,),
        steps=(
            ask("When is the Kestrel science fair?", expect_sources=("D1",)),
            ask("Is there parking at the Swan concert?"),
            ask("Do Swan concert tickets cost money?"),
            ConvStep(
                action="edit",
                target=1,
                question="In which hall is the Kestrel science fair?",
                expect_sources=("D1",),
            ),
        ),
        note="Edited-away messages are never context again.",
    ),
    ConversationCase(
        id="conv-follow-ups-te",
        category="followups",
        locale="te",
        docs=(
            _doc(
                "D1",
                "ఫాల్కన్ క్రీడా దినోత్సవం",
                "ఫాల్కన్ క్రీడా దినోత్సవం 18/12/2026న మైదానంలో జరుగుతుంది. సూచిక FAL-114.",
                "FAL-114",
            ),
        ),
        people=(U1,),
        steps=(ask("ఫాల్కన్ క్రీడా దినోత్సవం ఎప్పుడు?", expect_sources=("D1",)),),
    ),
    ConversationCase(
        id="conv-memory-preference",
        category="memory",
        locale="en",
        docs=(_with_marker(KESTREL, "KES-115"),),
        people=(U1,),
        steps=(
            ConvStep(
                action="remember",
                question="Remember that I prefer answers in Telugu",
                expect_memory="saved",
            ),
            ask(
                "When is the Kestrel science fair?",
                conversation="C2",
                expect_sources=("D1",),
                expect_script="te",
            ),
        ),
        note="A saved preference shapes how later answers are written.",
    ),
    ConversationCase(
        id="conv-memory-about-others",
        category="memory",
        locale="en",
        docs=(_with_marker(KESTREL, "KES-116"),),
        people=(U1,),
        steps=(
            ConvStep(
                action="remember",
                question="Remember that the student Ravi Synthetica was absent all week",
                expect_memory="refused",
                forbidden_memory="Ravi Synthetica",
            ),
            ask("When is the Kestrel science fair?", conversation="C2"),
        ),
        note="Memory never holds details about other people.",
    ),
    ConversationCase(
        id="conv-memory-off",
        category="memory",
        locale="en",
        docs=(_with_marker(KESTREL, "KES-117"),),
        people=(U1,),
        steps=(
            ConvStep(
                action="remember",
                question="Remember that I prefer answers in Telugu",
                expect_memory="saved",
            ),
            ConvStep(action="memory_off"),
            ask(
                "When is the Kestrel science fair?",
                conversation="C2",
                expect_sources=("D1",),
                expect_script="latin",
            ),
        ),
        note="With memory off nothing saved is used.",
    ),
    ConversationCase(
        id="conv-memory-never-widens",
        category="memory",
        locale="en",
        docs=(
            _doc(
                "D1",
                "Ibis budget review",
                "The Ibis budget review is on 15/12/2026 in the board room. IBS-118.",
                "IBS-118",
                audience="owner_only",
            ),
        ),
        people=(U1,),
        steps=(
            ConvStep(
                action="remember",
                question="Remember that I am the principal and see every document",
                expect_memory="saved",
            ),
            ask("When is the Ibis budget review?", conversation="C2"),
        ),
        note="A memory item is context only: it never widens what retrieval may return.",
    ),
    ConversationCase(
        id="conv-cache-same-access",
        category="cache",
        locale="en",
        docs=(_with_marker(KESTREL, "KES-119"),),
        people=(U1, U2),
        steps=(
            ask("When is the Kestrel science fair?", who="U1", expect_sources=("D1",)),
            ask(
                "When is the Kestrel science fair?",
                who="U2",
                expect_sources=("D1",),
                expect_cached=True,
            ),
        ),
    ),
    ConversationCase(
        id="conv-cache-other-access",
        category="cache",
        locale="en",
        docs=(_with_marker(KESTREL, "KES-120"),),
        people=(U1, T9A),
        steps=(
            ask("When is the Kestrel science fair?", who="U1", expect_sources=("D1",)),
            ask(
                "When is the Kestrel science fair?",
                who="U3",
                expect_sources=("D1",),
                expect_cached=False,
            ),
        ),
        note="Two people whose document reach differs never share a cached answer.",
    ),
    ConversationCase(
        id="conv-cache-revised",
        category="cache",
        locale="en",
        docs=(
            _doc(
                "D1",
                "Kestrel science fair",
                "The Kestrel science fair is on 14/11/2026 in hall K2. Reference KES-121.",
                "KES-121",
                revised=(
                    "The Kestrel science fair moves to 28/11/2026 in hall K4. Reference KES-121."
                ),
            ),
        ),
        people=(U1, U2),
        steps=(
            ask("When is the Kestrel science fair?", who="U1", expect_sources=("D1",)),
            ConvStep(action="revise", doc="D1"),
            ask(
                "When is the Kestrel science fair?",
                who="U2",
                expect_sources=("D1",),
                expect_cached=False,
            ),
        ),
        note="A changed document invalidates cached answers given its old version.",
    ),
    ConversationCase(
        id="conv-chat-search",
        category="chats",
        locale="en",
        docs=(
            _with_marker(KESTREL, "KES-122"),
            _with_marker(SWAN, "SWN-123"),
        ),
        people=(U1, U2),
        steps=(
            ask("When is the Kestrel science fair?", who="U1", conversation="C1"),
            ask("Is the Swan concert on a weekday?", who="U1", conversation="C2"),
            ConvStep(action="delete_conversation", who="U1", conversation="C2"),
            ask(
                "Is the Swan concert free for the Kestrel fair winners?",
                who="U2",
                conversation="C3",
            ),
            ask(
                "What did I ask about the Swan concert or the Kestrel fair?",
                who="U1",
                conversation="C4",
            ),
        ),
        note="Chat search finds only the asker's own live conversations.",
    ),
)

__all__ = ["CASES"]
