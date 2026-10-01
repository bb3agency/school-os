"""Control-plane code boundaries (ADR-0020, CLAUDE.md §4, ADR-0017; decision 2026-09-27).

``platform`` may call ``tenancy.service`` only for tenant lifecycle (register, initialise keys,
activate, suspend, reactivate, offboard, usage counts) and never reads tenant data. This test
parses every ``app/platform/**/*.py`` file and pins:

1. every ``app.*`` import: ``core``, ``authz``, ``audit`` and ``platform`` itself are free; each
   tenant-side import is listed below with the exact names it may bring in;
2. every attribute used on the imported ``tenancy`` service module (and nothing else may be
   done with that module object);
3. which files may open ``tenant_session()`` and which tenant-side relations raw SQL may name.

Changing any of these lists is a boundary change: update ADR-0020 in the same PR (or write a new
ADR if the decision changes).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import app.platform

PLATFORM_DIR = Path(app.platform.__file__).resolve().parent

FREE_PREFIXES = ("app.core", "app.authz", "app.audit", "app.platform")

TENANCY_SERVICE = "app.tenancy.service"
TENANCY_ALLOWED = frozenset(
    {
        # register + initialise keys
        "register_tenant",
        "initialise_tenant",
        # activate / suspend / reactivate / offboard (definer core.set_tenant_status)
        "activate_tenant",
        "suspend_tenant",
        "reactivate_tenant",
        "begin_offboarding",
        "set_tenant_status",
        # usage counts (definer core.tenant_usage_summary) and the fan-out over school IDs
        "tenant_usage",
        "list_tenant_ids",
        # offboard: the deletion job (ADR-0029, ADR-0020 amendment 2026-09-29). Each opens the
        # school's own tenant_session inside tenancy and returns counts and codes only.
        "tenant_data_inventory",
        "purge_tenant",
        "verify_tenant_purged",
        "destroy_tenant_keys",
        "purge_expired_audit_chain",
    }
)

# Tenant-side imports other than ``app.tenancy.service``: module -> names allowed (``None`` =
# a bare ``import`` for its side effect only). Pinned as they exist on 2026-09-27.
TENANT_SIDE_IMPORTS: dict[str, frozenset[str] | None] = {
    # The input schema of core.provision_tenant (code, name, boards, tier): no tenant data.
    "app.tenancy.schemas": frozenset({"TenantProvisionIn"}),
    # Registers the system-role cloning hook for provision_dedicated (no names used).
    "app.identity.service": None,
    # Operator token verification and step-up (identity infrastructure, no tenant data).
    "app.identity.principal": frozenset(
        {"Principal", "get_operator_principal", "require_recent_auth"}
    ),
    # Heartbeat nonce replay stores (Valkey), shared with service tokens.
    "app.identity.service_token": frozenset(
        {"InMemoryReplayStore", "RedisReplayStore", "ReplayStore"}
    ),
    # Idempotency-Key replay on the KV store (ops.idempotency_keys is never touched).
    "app.ops.idempotency": frozenset(
        {
            "IdempotencyRecord",
            "IdempotencyStore",
            "KVIdempotencyStore",
            "check_key",
            "request_hash",
            "resolve_existing",
        }
    ),
}

# Files that may open ``tenant_session()`` (sos_app, RLS applies), and why.
TENANT_SESSION_FILES = frozenset(
    {
        "tenant_audit.py",  # deliver school-chain copies (audit.record) + dedupe check
        "usage.py",  # aggregate counts per school per day (active users, AI answers)
    }
)

# Tenant-side relations raw SQL (``text(...)``) may name, per file.
SQL_ALLOWED = frozenset(
    {
        ("tenant_audit.py", "audit.events"),  # does platform_event_id X exist? (dedupe)
        ("usage.py", "audit.events"),  # count(DISTINCT actor_id) for one day
        # questions and billable AI answers for one day (counts only; ADR-0020 B2, ADR-0037)
        ("usage.py", "kb.queries"),
        ("service.py", "core.current_subscription"),  # definer, own school's billing
        ("repository.py", "core.create_owner_invite"),  # definer, first owner invite
    }
)
_RELATION = re.compile(r"\b(core|sis|kb|ops|audit)\.([a-z_][a-z0-9_]*)")


@dataclass
class Findings:
    imports: dict[str, set[str | None]] = field(default_factory=dict)
    tenancy_attrs: set[str] = field(default_factory=set)
    tenancy_misuse: list[str] = field(default_factory=list)
    tenant_session_files: set[str] = field(default_factory=set)
    sql_relations: set[tuple[str, str]] = field(default_factory=set)


def _sources() -> list[tuple[str, str]]:
    return [
        (str(path.relative_to(PLATFORM_DIR)), path.read_text("utf-8"))
        for path in sorted(PLATFORM_DIR.rglob("*.py"))
    ]


def _text_arg(node: ast.Call) -> str | None:
    if not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _scan(sources: list[tuple[str, str]]) -> Findings:  # noqa: PLR0912
    """One pass over each module's AST (``sources`` = [(relative path, source)])."""
    out = Findings()
    for rel, source in sources:
        tree = ast.parse(source, filename=rel)
        tenancy_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if full == TENANCY_SERVICE:
                        tenancy_aliases.add(alias.asname or alias.name)
                        out.imports.setdefault(TENANCY_SERVICE, set())
                    elif node.module == TENANCY_SERVICE:
                        out.imports.setdefault(TENANCY_SERVICE, set()).add(alias.name)
                        out.tenancy_attrs.add(alias.name)
                    else:
                        out.imports.setdefault(node.module, set()).add(alias.name)
                    if alias.name == "tenant_session":
                        out.tenant_session_files.add(rel)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app"):
                        out.imports.setdefault(alias.name, set()).add(None)
                        if alias.name == TENANCY_SERVICE and alias.asname:
                            tenancy_aliases.add(alias.asname)
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                sql = _text_arg(node) if name == "text" else None
                if sql is not None:
                    for m in _RELATION.finditer(sql):
                        out.sql_relations.add((rel, f"{m.group(1)}.{m.group(2)}"))
        if not tenancy_aliases:
            continue
        attr_values = {
            id(n.value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id in tenancy_aliases
        }
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                if n.value.id in tenancy_aliases:
                    out.tenancy_attrs.add(n.attr)
            elif (
                isinstance(n, ast.Name)
                and n.id in tenancy_aliases
                and id(n) not in attr_values
                and isinstance(n.ctx, ast.Load)
            ):
                out.tenancy_misuse.append(f"{rel}:{n.lineno} uses the tenancy module as a value")
    return out


FINDINGS = _scan(_sources())


def test_ADR_0020_platform_calls_tenancy_service_only_for_lifecycle() -> None:
    assert FINDINGS.tenancy_attrs, "the scan found the tenancy calls"
    assert FINDINGS.tenancy_attrs <= TENANCY_ALLOWED, sorted(
        FINDINGS.tenancy_attrs - TENANCY_ALLOWED
    )
    assert FINDINGS.tenancy_misuse == []


def test_ADR_0020_platform_imports_only_pinned_tenant_side_modules() -> None:
    problems: list[str] = []
    for module, names in sorted(FINDINGS.imports.items(), key=lambda kv: kv[0]):
        if module.startswith(FREE_PREFIXES) or module == TENANCY_SERVICE:
            continue
        # ``from app.tenancy import service`` is recorded under the package itself.
        if module == "app.tenancy" and names <= {"service"}:
            continue
        if module not in TENANT_SIDE_IMPORTS:
            problems.append(f"{module} is not an allowed import for platform")
            continue
        allowed = TENANT_SIDE_IMPORTS[module]
        got = {n for n in names if n is not None}
        if allowed is None:
            if got:
                problems.append(f"{module}: side-effect import only, got {sorted(got)}")
        elif not got <= allowed:
            problems.append(f"{module}: {sorted(got - allowed)} not allowed")
    assert problems == []


def test_ADR_0020_platform_never_imports_tenant_repositories_or_models() -> None:
    bad = [
        m
        for m in FINDINGS.imports
        if not m.startswith("app.platform")
        and (m.endswith((".repository", ".models")) or ".repository." in m or ".models." in m)
    ]
    assert bad == []
    # Modules holding student, document or knowledge data are never imported at all.
    data_modules = ("app.students", "app.documents", "app.dq", "app.changes", "app.imports")
    assert not [m for m in FINDINGS.imports if m.startswith(data_modules)]


def test_ADR_0020_only_pinned_files_open_tenant_sessions() -> None:
    assert FINDINGS.tenant_session_files == TENANT_SESSION_FILES


def test_ADR_0020_raw_sql_names_only_pinned_tenant_relations() -> None:
    assert FINDINGS.sql_relations <= SQL_ALLOWED, sorted(FINDINGS.sql_relations - SQL_ALLOWED)
    assert ("tenant_audit.py", "audit.events") in FINDINGS.sql_relations


def test_ADR_0020_scanner_detects_violations() -> None:
    """Self-check: the same scan flags a non-lifecycle call, the module used as a value, a
    stray tenant import, a tenant_session and raw SQL on a student table."""
    source = (
        "from app.tenancy import service as tenancy\n"
        "from app.students import service as students\n"
        "from app.core.db import tenant_session\n"
        "from sqlalchemy import text\n"
        "def f(s):\n"
        "    tenancy.list_classes(s)\n"
        "    helper = tenancy\n"
        "    s.execute(text('SELECT * FROM sis.students'))\n"
    )
    found = _scan([("rogue.py", source)])
    assert found.tenancy_attrs - TENANCY_ALLOWED == {"list_classes"}
    assert found.tenancy_misuse == ["rogue.py:7 uses the tenancy module as a value"]
    assert "app.students" in found.imports
    assert "app.students" not in TENANT_SIDE_IMPORTS
    assert found.tenant_session_files == {"rogue.py"}
    assert found.sql_relations == {("rogue.py", "sis.students")}
