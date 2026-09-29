"""Attendance, exams and marks: the school's educational records (M5; US-1701..US-1703;
FR-ATT-*, FR-MRK-*).

Public API: :mod:`app.academics.service` (routes in :mod:`app.academics.api`). C2 school records:
scoped by section (class teachers: their sections), corrected in place with an audit event,
never deleted by the app and never sent to an AI provider. The early-warning rules that read
them live in :mod:`app.insights`.
"""
