"""Circular reading and parent-notice drafting (M4; docs/06 §4.10, §10.4-10.5).

Requirements: FR-CIR-*, FR-NOTICE-*.

Pure helpers used by ``knowledge.service`` (the only public entry point for other modules):

- :mod:`.dates`: date mentions written in English, Telugu or numerically (``DD/MM/YYYY``), used to
  prove that every suggested deadline is written in the text it cites (FR-CIR-003).
- :mod:`.reading`: the request text, the strict JSON schema and the server-side validation of a
  circular reading (metadata, EN/TE summary, deadline suggestions with passage citations).
- :mod:`.notice`: the request text, schema and validation of a bilingual parent-notice draft.

No database, web, network or gateway imports: :mod:`app.knowledge.service` reads the passages,
calls the gateway and hands the raw output to these validators.
"""
