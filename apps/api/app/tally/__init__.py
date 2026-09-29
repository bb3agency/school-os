"""Tally read connector (M6; ADR-0032 Proposed; behind the flag ``tally.connector.enabled``).

The edge agent on the office PC reads TallyPrime over XML/HTTP on localhost and pushes snapshots
of the party ledgers under the ledger groups the accountant selected; people link parties to
students; the fee dues screen and the ``get_fee_dues`` record tool read only linked parties.

Other modules use only :mod:`app.tally.service`. Agent routes are guarded by
:func:`app.tally.agent_auth.require_edge_agent_signature` (and the enrolment guard), never by a
user session.
"""
