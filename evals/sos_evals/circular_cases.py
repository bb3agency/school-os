"""Synthetic circulars for the M4 reading eval (docs/06 §13.3). No real document, no person.

Offices, districts, reference numbers and dates are invented ("Sitarampuram", "Kondapalli" are
fictional); nothing is copied from a real government circular. Each case lists the deadlines a
careful office clerk would write down: dates by which or on which the school must act. Distractor
dates (the circular's own date, earlier letters it refers to, past events) are not deadlines.

Languages: English (``en``), Telugu script (``te``) and code-mixed Telugu in Latin script with
English (``mixed``), in the date formats offices meet: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY,
"5th November 2026" and Telugu month names.
"""

from __future__ import annotations

from datetime import date

from sos_evals.circulars import CircularCase


def _case(
    case_id: str,
    locale: str,
    title: str,
    lines: list[str],
    expected: list[date],
    *,
    reference_no: str | None = None,
    issued_on: date | None = None,
    note: str = "",
) -> CircularCase:
    return CircularCase.model_validate(
        {
            "id": f"circ-{case_id}",
            "locale": locale,
            "title": title,
            "lines": lines,
            "expected_deadlines": expected,
            "reference_no": reference_no,
            "issued_on": issued_on,
            "note": note,
        }
    )


D = date

CASES: tuple[CircularCase, ...] = (
    # --- English ------------------------------------------------------------------------------
    _case(
        "en-udise",
        "en",
        "UDISE+ data collection 2026-27",
        [
            "Office of the District Educational Officer, Sitarampuram.",
            "Rc.No.101/A/2026 Date: 03/09/2026.",
            "Sub: UDISE+ data collection for 2026-27.",
            "Ref: Circular dated 12/08/2026.",
            "All Headmasters are requested to submit the UDISE+ data capture formats on or before "
            "15/10/2026.",
            "A review meeting will be conducted on 22/10/2026 at the district office.",
        ],
        [D(2026, 10, 15), D(2026, 10, 22)],
        reference_no="Rc.No.101/A/2026",
        issued_on=D(2026, 9, 3),
        note="header date and an earlier circular are distractors",
    ),
    _case(
        "en-fees",
        "en",
        "Examination fee",
        [
            "Office of the Secretary, Board of Secondary Education (synthetic).",
            "Memo No.55/Exam/2026 Date: 10-09-2026.",
            "Sub: Payment of SSC examination fee.",
            "The last date for payment of the examination fee without late fee is 20-10-2026.",
            "Schools must upload the nominal rolls not later than 30-10-2026.",
        ],
        [D(2026, 10, 20), D(2026, 10, 30)],
        reference_no="Memo No.55/Exam/2026",
        issued_on=D(2026, 9, 10),
    ),
    _case(
        "en-science-fair",
        "en",
        "District science fair",
        [
            "Office of the District Science Officer, Kondapalli.",
            "Proc.No.77/Sci/2026 Date: 01.10.2026.",
            "Sub: District level science fair.",
            "The district science fair will be held on 5th November 2026 at the Zilla Parishad "
            "school.",
            "Entries must reach this office by 25.10.2026.",
        ],
        [D(2026, 11, 5), D(2026, 10, 25)],
        reference_no="Proc.No.77/Sci/2026",
        issued_on=D(2026, 10, 1),
    ),
    _case(
        "en-training",
        "en",
        "Teacher training",
        [
            "Office of the District Educational Officer, Sitarampuram.",
            "Rc.No.140/T/2026 Date: 05/10/2026.",
            "Sub: Five day training for mathematics teachers.",
            "Nominated teachers shall attend the training from 03/11/2026 at the district "
            "institute.",
            "Headmasters should send the list of nominated teachers by 20/10/2026.",
        ],
        [D(2026, 11, 3), D(2026, 10, 20)],
        reference_no="Rc.No.140/T/2026",
        issued_on=D(2026, 10, 5),
    ),
    _case(
        "en-no-deadline",
        "en",
        "Cleanliness drive",
        [
            "Office of the District Educational Officer, Sitarampuram.",
            "Rc.No.150/G/2026 Date: 06/10/2026.",
            "Sub: Cleanliness in school premises.",
            "All schools should keep their premises and toilets clean throughout the year.",
        ],
        [],
        reference_no="Rc.No.150/G/2026",
        issued_on=D(2026, 10, 6),
        note="no deadline at all: nothing should be suggested",
    ),
    _case(
        "en-past-event",
        "en",
        "Sports meet follow-up",
        [
            "Office of the District Sports Officer, Kondapalli.",
            "Rc.No.19/Spt/2026 Date: 08/10/2026.",
            "Sub: Zonal sports meet.",
            "The previous zonal meet was conducted in the district stadium last year.",
            "Schools must submit the entries for the zonal sports meet on or before 28/10/2026.",
            "The zonal sports meet will be held on 12/11/2026.",
        ],
        [D(2026, 10, 28), D(2026, 11, 12)],
        reference_no="Rc.No.19/Spt/2026",
        issued_on=D(2026, 10, 8),
    ),
    _case(
        "en-aadhaar-seeding",
        "en",
        "APAAR ID generation",
        [
            "Office of the District Educational Officer, Sitarampuram.",
            "Rc.No.160/APAAR/2026 Date: 09/10/2026.",
            "Sub: Generation of APAAR IDs for students.",
            "Headmasters shall complete the generation of APAAR IDs for all students by "
            "15/11/2026.",
            "A progress report must be sent to this office on 01/11/2026.",
        ],
        [D(2026, 11, 15), D(2026, 11, 1)],
        reference_no="Rc.No.160/APAAR/2026",
        issued_on=D(2026, 10, 9),
    ),
    _case(
        "en-midday-meal",
        "en",
        "Mid-day meal inspection",
        [
            "Office of the Deputy Educational Officer, Kondapalli.",
            "Lr.No.33/MDM/2026 Date: 11/10/2026.",
            "Sub: Inspection of mid-day meal kitchens.",
            "Read: Government memo dated 30/09/2026.",
            "The inspection team will visit the schools on 29/10/2026.",
            "Headmasters must keep the stock registers ready and submit the utilisation "
            "certificate by 27/10/2026.",
        ],
        [D(2026, 10, 29), D(2026, 10, 27)],
        reference_no="Lr.No.33/MDM/2026",
        issued_on=D(2026, 10, 11),
        note="an earlier memo date is a distractor",
    ),
    _case(
        "en-textbooks",
        "en",
        "Textbook indent",
        [
            "Office of the District Educational Officer, Sitarampuram.",
            "Rc.No.171/TB/2026 Date: 12/10/2026.",
            "Sub: Indent of textbooks for 2027-28.",
            "The textbook indent in the prescribed format must reach this office by "
            "December 5, 2026.",
        ],
        [D(2026, 12, 5)],
        reference_no="Rc.No.171/TB/2026",
        issued_on=D(2026, 10, 12),
        note="month written before the day",
    ),
    _case(
        "en-exam-schedule",
        "en",
        "Half-yearly examinations",
        [
            "Office of the District Common Examination Board (synthetic).",
            "Rc.No.180/DCEB/2026 Date: 13/10/2026.",
            "Sub: Half-yearly examinations.",
            "The half-yearly examinations will be conducted from 07/12/2026.",
            "Schools shall collect the question papers from the mandal office on 05/12/2026.",
            "Marks must be uploaded on the portal by 24/12/2026.",
        ],
        [D(2026, 12, 7), D(2026, 12, 5), D(2026, 12, 24)],
        reference_no="Rc.No.180/DCEB/2026",
        issued_on=D(2026, 10, 13),
    ),
    # --- Telugu -------------------------------------------------------------------------------
    _case(
        "te-nominal-rolls",
        "te",
        "నామినల్ రోల్స్",
        [
            "జిల్లా విద్యాశాఖ అధికారి కార్యాలయం, సీతారాంపురం.",
            "తేదీ: 02/10/2026.",
            "పదవ తరగతి విద్యార్థుల నామినల్ రోల్స్ 20 అక్టోబర్ 2026 లోగా సమర్పించాలి.",
        ],
        [D(2026, 10, 20)],
        issued_on=D(2026, 10, 2),
    ),
    _case(
        "te-exam-fee",
        "te",
        "పరీక్ష రుసుము",
        [
            "ప్రభుత్వ పరీక్షల విభాగం కార్యాలయం (నమూనా).",
            "తేదీ: 04/10/2026.",
            "పరీక్ష రుసుము 10.11.2026 లోపు చెల్లించాలి.",
            "ఆలస్య రుసుముతో 20.11.2026 లోపు చెల్లించవచ్చు.",
        ],
        [D(2026, 11, 10), D(2026, 11, 20)],
        issued_on=D(2026, 10, 4),
    ),
    _case(
        "te-meeting",
        "te",
        "ప్రధానోపాధ్యాయుల సమావేశం",
        [
            "మండల విద్యాశాఖ అధికారి కార్యాలయం, కొండపల్లి.",
            "తేదీ: 06/10/2026.",
            "ప్రధానోపాధ్యాయులందరూ 25/10/2026 న మండల కార్యాలయంలో జరిగే సమావేశానికి హాజరు కావాలి.",
        ],
        [D(2026, 10, 25)],
        issued_on=D(2026, 10, 6),
    ),
    _case(
        "te-exams",
        "te",
        "అర్ధ సంవత్సర పరీక్షలు",
        [
            "జిల్లా ఉమ్మడి పరీక్షల మండలి కార్యాలయం (నమూనా).",
            "తేదీ: 07/10/2026.",
            "అర్ధ సంవత్సర పరీక్షలు 02/12/2026 నుండి నిర్వహించబడతాయి.",
            "మార్కుల జాబితాలను 15-12-2026 నాటికి పంపించాలి.",
        ],
        [D(2026, 12, 2), D(2026, 12, 15)],
        issued_on=D(2026, 10, 7),
    ),
    _case(
        "te-scholarship",
        "te",
        "ఉపకార వేతనాలు",
        [
            "జిల్లా సంక్షేమ అధికారి కార్యాలయం, సీతారాంపురం.",
            "తేదీ: 08/10/2026.",
            "ఉపకార వేతనాల దరఖాస్తులను 30 నవంబర్ 2026 లోగా ఆన్‌లైన్‌లో సమర్పించాలి.",
        ],
        [D(2026, 11, 30)],
        issued_on=D(2026, 10, 8),
    ),
    _case(
        "te-sports",
        "te",
        "క్రీడా పోటీలు",
        [
            "జిల్లా క్రీడాభివృద్ధి అధికారి కార్యాలయం, కొండపల్లి.",
            "తేదీ: 09/10/2026.",
            "మండల స్థాయి క్రీడా పోటీలు 14/11/2026 న నిర్వహించబడతాయి.",
            "పాల్గొనే విద్యార్థుల జాబితాను 05/11/2026 లోగా పంపించాలి.",
        ],
        [D(2026, 11, 14), D(2026, 11, 5)],
        issued_on=D(2026, 10, 9),
    ),
    _case(
        "te-no-deadline",
        "te",
        "పరిశుభ్రత",
        [
            "జిల్లా విద్యాశాఖ అధికారి కార్యాలయం, సీతారాంపురం.",
            "తేదీ: 10/10/2026.",
            "పాఠశాల ఆవరణను ఎల్లప్పుడూ పరిశుభ్రంగా ఉంచాలి.",
        ],
        [],
        issued_on=D(2026, 10, 10),
        note="no deadline at all: nothing should be suggested",
    ),
    _case(
        "te-training",
        "te",
        "ఉపాధ్యాయ శిక్షణ",
        [
            "జిల్లా విద్యా శిక్షణ సంస్థ, కొండపల్లి.",
            "తేదీ: 11/10/2026.",
            "ఎంపికైన ఉపాధ్యాయులు 16/11/2026 నుండి శిక్షణకు హాజరు కావాలి.",
            "ఎంపికైన ఉపాధ్యాయుల వివరాలను 01/11/2026 లోపు పంపించాలి.",
        ],
        [D(2026, 11, 16), D(2026, 11, 1)],
        issued_on=D(2026, 10, 11),
    ),
    # --- code-mixed (Latin-script Telugu with English) -----------------------------------------
    _case(
        "mixed-udise",
        "mixed",
        "UDISE update",
        [
            "Office of the Mandal Educational Officer, Kondapalli.",
            "Rc.No.45/MEO/2026 Date: 12/10/2026.",
            "UDISE data ni 18/10/2026 lopu submit cheyali.",
        ],
        [D(2026, 10, 18)],
        reference_no="Rc.No.45/MEO/2026",
        issued_on=D(2026, 10, 12),
    ),
    _case(
        "mixed-training",
        "mixed",
        "Training ki hajaru",
        [
            "Office of the District Educational Officer, Sitarampuram.",
            "Rc.No.190/T/2026 Date: 13/10/2026.",
            "English teachers andaru training ki 04/11/2026 na hajaru kavali.",
            "Attendance list ni 06-11-2026 loga office ki pampandi.",
        ],
        [D(2026, 11, 4), D(2026, 11, 6)],
        reference_no="Rc.No.190/T/2026",
        issued_on=D(2026, 10, 13),
    ),
    _case(
        "mixed-fees",
        "mixed",
        "Fee receipts",
        [
            "Office of the Deputy Educational Officer, Kondapalli.",
            "Rc.No.58/Fin/2026 Date: 14/10/2026.",
            "Fee receipts 28-10-2026 loga office ki pampandi.",
        ],
        [D(2026, 10, 28)],
        reference_no="Rc.No.58/Fin/2026",
        issued_on=D(2026, 10, 14),
    ),
    _case(
        "mixed-exam",
        "mixed",
        "Pre-final exams",
        [
            "Office of the District Common Examination Board (synthetic).",
            "Rc.No.200/DCEB/2026 Date: 15/10/2026.",
            "Pre-final exams 11.01.2027 nundi nirvahinchabadatayi.",
            "Question papers ni 09.01.2027 na mandal office nundi collect cheyali.",
        ],
        [D(2027, 1, 11), D(2027, 1, 9)],
        reference_no="Rc.No.200/DCEB/2026",
        issued_on=D(2026, 10, 15),
    ),
    _case(
        "mixed-apaar",
        "mixed",
        "APAAR progress",
        [
            "Office of the Mandal Educational Officer, Sitarampuram.",
            "Rc.No.61/MEO/2026 Date: 16/10/2026.",
            "APAAR ID generation ni 20/11/2026 lopala complete cheyali.",
        ],
        [D(2026, 11, 20)],
        reference_no="Rc.No.61/MEO/2026",
        issued_on=D(2026, 10, 16),
    ),
    _case(
        "mixed-no-deadline",
        "mixed",
        "Library books",
        [
            "Office of the Mandal Educational Officer, Kondapalli.",
            "Rc.No.62/MEO/2026 Date: 17/10/2026.",
            "Library books ni students ki regular ga ivvali.",
        ],
        [],
        reference_no="Rc.No.62/MEO/2026",
        issued_on=D(2026, 10, 17),
        note="no deadline at all: nothing should be suggested",
    ),
)

__all__ = ["CASES"]
