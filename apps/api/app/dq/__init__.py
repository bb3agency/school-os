"""Data quality (docs/02 §5-6, FR-DQ-*).

M1 wave 1 is a pure library (no database, no routes):

- :mod:`app.dq.matching` - name match classes (:func:`~app.dq.matching.classify`), thresholds
  and severities (:class:`~app.dq.matching.MatchPolicy`), variant dictionary.
- :mod:`app.dq.rules` - rule registry types, severities, finding value objects and
  fingerprints; the DQ-001..DQ-012 catalog.
- :mod:`app.dq.explanations` - bilingual (EN/TE) explanation texts and correction routes.

Configuration ships in ``app/dq/config/*.yaml``. The DQ engine with findings tables (wave 2)
builds on these modules and exposes its public functions from ``app.dq.service``.
"""
