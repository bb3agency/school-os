"""Circulars -> tasks, reminders and bilingual parent notices (M4; US-1601..US-1606).

Public API: :mod:`app.circulars.service` (routes in :mod:`app.circulars.api`, worker jobs in
:mod:`app.circulars.tasks`). AI calls go only through ``app.knowledge.service`` (gateway,
prompts, metering); every AI result is a suggestion a person confirms (invariant 9).
"""
