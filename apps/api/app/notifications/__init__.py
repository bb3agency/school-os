"""In-app notifications with bilingual (English/Telugu) templates (C11, FR-NOT-001); rendered
in English only while Telugu is hidden (ADR-0036).

Other modules call :func:`app.notifications.service.notify` inside their own transaction.
"""
