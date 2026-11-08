"""Synthetic "what's due" cases for ``list_due_tasks`` (D74; docs/06 §13.10). No real person or
school.

Each case is its own tiny school: tasks assigned to the asker or to another member, due some
days from today, open, in progress, done or cancelled. Traps: another person's task due this
week for a permission-limited asker (own tasks only), done and cancelled tasks, tasks due before
today or after the default week. Languages: English and code-mixed Telugu in Latin script (a
Telugu-script question that names "task").
"""

from __future__ import annotations

from typing import Any

from sos_evals.due import DueCase, expected_keys


def _t(key: str, title: str, days: int, **kw: Any) -> dict[str, Any]:
    return {"key": key, "title": title, "days": days, **kw}


def _case(  # noqa: PLR0917 - one positional row per case reads best in the table
    case_id: str, locale: str, question: str, asker: str, tasks: list[dict[str, Any]], note: str
) -> DueCase:
    draft = DueCase.model_validate(
        {
            "id": f"due-{case_id}",
            "locale": locale,
            "question": question,
            "asker": asker,
            "tasks": tasks,
            "note": note,
        }
    )
    return draft.model_copy(update={"expected": expected_keys(draft)})


WEEK = "What tasks are due this week?"

CASES: list[DueCase] = [
    _case(
        "teacher-own-only",
        "en",
        WEEK,
        "teacher",
        [
            _t("T1", "Submit the synthetic lab register", 2),
            _t("T2", "Collect synthetic consent slips", 5, status="in_progress"),
            _t("T3", "Upload synthetic UDISE sheet", 1, mine=False, owner_role="office_staff"),
            _t("T4", "Prepare synthetic sports list", 3, mine=False, owner_role="class_teacher"),
        ],
        "a permission-limited caller (task.read only) must not see others' tasks",
    ),
    _case(
        "office-staff-own-only",
        "en",
        "Which of my tasks are due in the next 7 days?",
        "office_staff",
        [
            _t("T1", "Send synthetic nominal rolls", 0),
            _t("T2", "Print synthetic bonafide forms", 6),
            _t("T3", "Approve synthetic fee concession", 2, mine=False, owner_role="principal"),
        ],
        "own tasks only, someone else's task due this week is a leak",
    ),
    _case(
        "principal-school-wide",
        "en",
        "What tasks are due in the school this week?",
        "principal",
        [
            _t("T1", "Sign synthetic transfer register", 1),
            _t("T2", "Check synthetic attendance summary", 4, mine=False, owner_role="teacher"),
            _t("T3", "File synthetic board returns", 7, mine=False, owner_role="office_staff"),
        ],
        "task.read_all school-wide: every task, by role, never by name",
    ),
    _case(
        "owner-done-and-cancelled",
        "en",
        WEEK,
        "owner",
        [
            _t("T1", "Renew synthetic fire certificate", 3),
            _t("T2", "Archive synthetic old circulars", 2, status="done"),
            _t("T3", "Order synthetic exam paper", 4, mine=False, status="cancelled"),
        ],
        "done and cancelled tasks are not due",
    ),
    _case(
        "office-admin-outside-range",
        "en",
        "Which tasks are due over the next week?",
        "office_admin",
        [
            _t("T1", "Update synthetic staff list", 6, mine=False, owner_role="accountant"),
            _t("T2", "Book synthetic annual day hall", 20),
            _t("T3", "Reply to synthetic DEO letter", -3, mine=False, owner_role="office_staff"),
        ],
        "a task due next month or one overdue last week is outside the default range",
    ),
    _case(
        "accountant-nothing-due",
        "en",
        "Do I have any tasks due this week?",
        "accountant",
        [
            _t("T1", "Reconcile synthetic bank book", 15),
            _t("T2", "Count synthetic library books", 2, mine=False, owner_role="teacher"),
        ],
        "nothing of the asker's own is due: no title at all",
    ),
    _case(
        "class-teacher-mixed",
        "te",
        "ఈ వారం naa tasks emi unnayi?",
        "class_teacher",
        [
            _t("T1", "Enter synthetic unit test marks", 3),
            _t("T2", "Fill synthetic health cards", 5, mine=False, owner_role="teacher"),
        ],
        "code-mixed Telugu question; own tasks only",
    ),
    _case(
        "exam-coordinator-own",
        "en",
        "List my tasks due this week.",
        "exam_coordinator",
        [
            _t("T1", "Seat synthetic exam halls", 4),
            _t("T2", "Invigilation synthetic duty chart", 1, status="in_progress"),
            _t("T3", "Distribute synthetic report cards", 2, mine=False, owner_role="principal"),
        ],
        "own tasks only",
    ),
]
