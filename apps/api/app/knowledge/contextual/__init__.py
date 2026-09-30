"""Contextual chunk headers: request shape and output checks (docs/06 §4.11; FR-KB-001).

Pure (no database, network or model call; import-linter ``knowledge-pure-foundations``), like
``knowledge.circulars``: :mod:`.rules` builds what the ``contextualize`` model role sees (the
document once, stable, then numbered passages) and checks what comes back. The ingestion package
calls the model through the gateway; the gateway's offline fake answers the same schema.
"""
