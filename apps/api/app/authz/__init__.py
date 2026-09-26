"""Authorization: permission catalog, system roles, UserContext, require() and scope helpers.

SEC-003, SEC-005, SEC-015; FR-IAM-002, FR-IAM-010..014. Everyone may import this module
(CLAUDE.md §4). Import submodules explicitly (``from app.authz.dependencies import require``);
this package ``__init__`` stays empty so ``identity.service`` can use ``authz.catalog``/``cache``
without an import cycle.
"""
