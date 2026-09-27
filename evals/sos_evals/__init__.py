"""SchoolOS RAG evaluation harness (docs/06 §13).

The harness talks to the system under test only through the adapter protocols in
`sos_evals.adapters`, so it runs today against deterministic stubs and later against the
knowledge module without changes. Datasets are synthetic (CLAUDE.md invariant 11).
"""
