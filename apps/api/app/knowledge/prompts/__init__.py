"""Versioned prompt files and their loader (docs/06 §10; invariant 13).

Responsibility: hold every system prompt as a file ``<id>.v<version>.txt`` with a header
(``id``, ``version``, ``model_config_key``, ``changelog``) and load, validate and render it
(:mod:`.registry`). Prompt text never lives inline in code. A prompt change needs a passing
``make eval`` (docs/06 §10, SEC-019).

Boundary: pure (reads files shipped in this package only). May import ``app.knowledge.config``
and ``app.knowledge.domain``; no database, web, network or other module (import-linter
``knowledge-pure-foundations``).
"""
