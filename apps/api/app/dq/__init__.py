"""Data quality (docs/02 §5-6, FR-DQ-*).

Pure library (no database, no routes; import-linter contract ``dq-pure-library``):

- :mod:`app.dq.matching` - name match classes (:func:`~app.dq.matching.classify`), thresholds
  and severities (:class:`~app.dq.matching.MatchPolicy`), variant dictionary.
- :mod:`app.dq.rules` - rule registry types, severities, finding value objects and
  fingerprints; the DQ-001..DQ-012 catalog.
- :mod:`app.dq.explanations` - bilingual (EN/TE) explanation texts and correction routes.
- :mod:`app.dq.checks` - one :class:`~app.dq.rules.RuleCheck` per check kind over in-memory
  facts; :mod:`app.dq.masking` - masked value forms; :mod:`app.dq.profiles` - engine settings
  and export pre-check profiles.

Engine and workflow (M1 wave 2, database): :mod:`app.dq.engine` (bulk loading through
``app.students.service``, reconcile by fingerprint), :mod:`app.dq.service` (public functions:
runs, findings, resolve/waive, catalog, summary), ``repository``/``models`` (``sis.dq_runs``,
``sis.dq_findings``, migration 0013_dq), ``api`` (``/api/v1/dq``) and ``tasks`` (queue ``dq``).

Configuration ships in ``app/dq/config/*.yaml`` and ``app/dq/config/profiles/*.yaml``.
"""
