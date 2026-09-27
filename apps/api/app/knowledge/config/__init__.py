"""Versioned knowledge configuration (invariant 13; docs/06 §4.5, §4.6, §6, §7, §9, §12).

Responsibility: load and validate the YAML files next to this package, one loader per file so
each later package owns its own pair: ``models.yaml`` (model roles, prices, limits, budgets;
:mod:`.llm`), ``embeddings.yaml`` (:mod:`.embeddings`), ``retrieval.yaml`` (:mod:`.retrieval`),
``chunking.yaml`` (:mod:`.chunking`) and ``tools.yaml`` (:mod:`.tools`). Model IDs, provider
names and thresholds live here, never in code; changing a model, prompt or retrieval setting
needs a passing ``make eval``. Operational switches and secrets are ``SOS_*`` settings in
``app.core.config`` instead (docs/10 §11). There is deliberately no setting that points at
another file: an unversioned file could change models without the eval gate.

Boundary: pure (reads files shipped in this package; no environment, database, web or
network). Imports nothing from ``app`` except ``app.knowledge.domain`` (import-linter
``knowledge-pure-foundations``).
"""
