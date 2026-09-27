"""Identity-field change requests with maker-checker (US-601; FR-CR-001..005; SEC-014; BR-04;
ADR-0010).

A change to an identity attribute (``is_identity``) of a student is requested with the new value,
a reason and an evidence document, and applied only when a second person with
``student.identity_change.approve`` approves it after a fresh MFA sign-in. Other modules use only
``app.changes.service``.
"""
