"""SchoolOS Tally edge agent (M6; ADR-0032, Proposed).

A small program on the office PC that runs TallyPrime. It talks to Tally only on this PC
(``127.0.0.1``, XML over HTTP, **export requests only**) and to SchoolOS only over outbound HTTPS,
signing every request with its device key (HMAC-SHA256; the key is kept with Windows DPAPI). It
sends the ledger groups of the open company and, for the groups the school's accountant selected
in SchoolOS, each party ledger's closing balance. Nothing personal is written to disk or to the
logs.

Modules: :mod:`.tally_xml` (request builder and XXE-safe parser), :mod:`.signing`,
:mod:`.config`, :mod:`.credentials` (DPAPI), :mod:`.client` (SchoolOS and Tally over urllib),
:mod:`.sync` (one sync and the service loop with backoff), :mod:`.cli`.
"""

__version__ = "0.1.0"
