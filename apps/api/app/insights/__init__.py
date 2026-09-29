"""Student timeline and early warning (M5; US-1704..US-1709; FR-EW-*).

Public API: :mod:`app.insights.service` (routes in :mod:`app.insights.api`, worker jobs in
:mod:`app.insights.tasks`). Behaviour notes, ABC indicators, flags raised by versioned
deterministic rules, the intervention log and the per-student timeline. Restricted (C3) and
purpose-limited (08 §4 PRV-003..005): visible only to the student's class teacher and the
principal, never exported except in the school's full data export, never sent to an AI provider,
and every flag needs a person to act.
"""
