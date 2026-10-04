"""Bilingual notification templates (FR-NOT-001, CLAUDE.md §6.13 config in versioned files)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.core.languages import contains_telugu
from app.notifications import templates as t

REQUIRED = {
    "change_request.submitted",
    "change_request.approved",
    "change_request.rejected",
    "dq.run.completed",
    "import.validated",
    "import.committed",
    "import.reverted",
    "document.quarantined",
    "breakglass.requested",
    "breakglass.approved",
    "breakglass.expired",
    "export.ready",
    "export.failed",
    "admin.tenant_export.ready",
    "admin.tenant_export.failed",
    # M4 (FR-CIR-001, FR-CIR-005, FR-TASK-003, FR-TASK-007)
    "circular.read_ready",
    "circular.needs_review",
    "task.assigned",
    "task.due_soon",
    "task.overdue",
    # M5 (FR-EW-006)
    "insights.flag_raised",
    "insights.flag_assigned",
    "insights.flag_overdue",
}
NUMERIC = {
    "blockers",
    "warnings",
    "valid_rows",
    "error_rows",
    "rows",
    "minutes",
    "low_confidence_rows",
    "students",
    "hours",
    "suggestions",
    "days",
}


def _sample(template: t.Template, number: int = 2) -> dict[str, Any]:
    return {
        p: number if p in NUMERIC else ("support_request" if p == "reason_code" else "id-1")
        for p in template.params
    }


def test_FR_NOT_001_required_templates_exist_in_english_and_telugu() -> None:
    catalog = t.catalog()
    assert set(catalog) >= REQUIRED
    for template in catalog.values():
        assert set(template.messages) == {"en", "te"}


@pytest.mark.parametrize("key", sorted(t.catalog()))
@pytest.mark.parametrize("number", [0, 1, 7])
def test_FR_NOT_001_every_template_renders_in_both_languages(
    key: str, number: int, telugu_on: None
) -> None:
    template = t.get(key)
    for lang in ("en", "te"):
        msg = t.render(key, _sample(template, number), lang)
        assert msg.title
        assert msg.body
        assert "{" not in msg.title + msg.body
        assert "#" not in msg.body
        assert "  " not in msg.body
        assert " ." not in msg.body
    te = t.render(key, _sample(template, number), "te")
    assert any("ఀ" <= ch <= "౿" for ch in te.title + te.body), "Telugu script"


def test_FR_NOT_001_plural_and_select_forms(telugu_on: None) -> None:
    zero = t.render("dq.run.completed", {"run_id": "r", "blockers": 0, "warnings": 1}, "en")
    assert zero.body == ("The data check found no problems that block submission and 1 warning.")
    many = t.render("import.committed", {"import_id": "i", "rows": 12}, "en")
    assert many.body == "12 rows were added to the student records."
    one = t.render("import.committed", {"import_id": "i", "rows": 1}, "te")
    assert one.body.startswith("1 వరుస ")
    legal = t.render(
        "breakglass.requested",
        {"grant_id": "g", "minutes": 60, "reason_code": "legal_obligation"},
        "en",
    )
    assert "because the law requires it" in legal.body
    unknown = t.render(
        "breakglass.requested", {"grant_id": "g", "minutes": 60, "reason_code": "zzz"}, "en"
    )
    assert "60 minutes. Review" in unknown.body


def test_FR_NOT_001_params_must_match_declared_placeholders() -> None:
    template = t.get("import.committed")
    t.check_params(template, {"import_id": "i", "rows": 3})
    with pytest.raises(t.TemplateError, match="missing"):
        t.check_params(template, {"import_id": "i"})
    with pytest.raises(t.TemplateError, match="undeclared"):
        t.check_params(template, {"import_id": "i", "rows": 3, "student_id": "s"})
    with pytest.raises(t.TemplateError):
        t.get("nope.nothing")
    with pytest.raises(t.TemplateError):
        t.render("import.committed", {"import_id": "i", "rows": "many"}, "en")


def test_FR_NOT_001_template_parser_rejects_malformed_messages() -> None:
    for bad in ("{", "}", "{count, plural, one {x}}", "{Bad}", "{n, date, short}"):
        with pytest.raises(t.TemplateError):
            t.placeholders(bad)
    assert t.placeholders("{a} {b, select, x {{c}} other {}}") == {"a", "b", "c"}


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "en"),
        ("", "en"),
        ("te", "te"),
        ("te-IN,te;q=0.9,en;q=0.8", "te"),
        ("en-IN,en;q=0.9,te;q=0.8", "en"),
        ("hi-IN, te;q=0.5", "te"),
        ("fr", "en"),
        ("en;q=0.2, te;q=0.7", "te"),
        ("te;q=abc", "en"),
    ],
)
def test_FR_NOT_001_accept_language_negotiation(
    header: str | None, expected: str, telugu_on: None
) -> None:
    assert t.negotiate_language(header) == expected


@pytest.mark.parametrize("key", sorted(t.catalog()))
def test_ADR_0036_every_template_renders_in_english_while_telugu_is_hidden(key: str) -> None:
    template = t.get(key)
    for number in (0, 1, 7):
        params = _sample(template, number)
        for lang in ("te", "mixed", "en", "fr"):
            msg = t.render(key, params, lang)
            assert msg == t.render(key, params, "en")
            assert not contains_telugu(msg.title + msg.body)


@pytest.mark.parametrize("header", ["te", "te-IN,te;q=0.9,en;q=0.8", "hi-IN, te;q=0.5", None])
def test_ADR_0036_accept_language_te_gives_english_while_telugu_is_hidden(
    header: str | None,
) -> None:
    assert t.negotiate_language(header) == "en"


def test_FR_ADM_002_read_retention_is_ninety_days() -> None:
    assert t.read_retention_days() == 90


# --- every notification the code sends has a template -------------------------------------

APP_DIR = Path(t.__file__).resolve().parents[1]


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "text"`` / ``NAME: Final = "text"`` assignments."""
    constants: dict[str, str] = {}
    for stmt in tree.body:
        targets = (
            stmt.targets
            if isinstance(stmt, ast.Assign)
            else [stmt.target]
            if isinstance(stmt, ast.AnnAssign)
            else []
        )
        value = getattr(stmt, "value", None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value.value
    return constants


def _local_constants(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, set[str]]:
    """String literals a function assigns to its local names, including tuple unpacking
    (``key, template = f"...", "task.overdue"`` in ``circulars.send_reminders``)."""
    found: dict[str, set[str]] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
            continue
        value = node.value
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            pairs: list[tuple[ast.expr, ast.expr]] = (
                list(zip(target.elts, value.elts, strict=False))
                if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple)
                else [(target, value)]
            )
            for name, item in pairs:
                if (
                    isinstance(name, ast.Name)
                    and isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                ):
                    found.setdefault(name.id, set()).add(item.value)
    return found


def _sent_template_keys() -> dict[str, tuple[str, set[str] | None]]:
    """``template_key`` values passed to ``notifications.notify`` by app code, with the literal
    param names when the call spells them out. Covers literals, module constants, string
    literals the calling function assigns to a local name (``template = "task.overdue"``) and
    module helpers that forward a key (``changes._notify(session, row, "<key>", ...)``,
    ``extraction._notify(session, batch, <CONSTANT>, {...})``). Keyed ``path:line:key``."""
    found: dict[str, tuple[str, set[str] | None]] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.parent.name == "notifications":
            continue
        tree = ast.parse(path.read_text("utf-8"))
        constants = _module_constants(tree)
        scopes: list[tuple[ast.AST, dict[str, set[str]]]] = [(tree, {})]
        scopes += [
            (node, _local_constants(node))
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for scope, local in scopes:

            def resolve(
                expr: ast.expr,
                constants: dict[str, str] = constants,
                local: dict[str, set[str]] = local,
            ) -> set[str]:
                if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                    return {expr.value}
                if isinstance(expr, ast.Name):
                    if expr.id in local:
                        return local[expr.id]
                    if expr.id in constants:
                        return {constants[expr.id]}
                return set()

            for node in ast.walk(scope):
                if not isinstance(node, ast.Call):
                    continue
                kwargs = {k.arg: k.value for k in node.keywords}
                key_expr = kwargs.get("template_key")
                params_expr = kwargs.get("params")
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if key_expr is None and name == "_notify" and len(node.args) >= 3:
                    key_expr = node.args[2]
                    params_expr = node.args[3] if len(node.args) >= 4 else None
                if key_expr is None:
                    continue
                params = (
                    {str(k.value) for k in params_expr.keys if isinstance(k, ast.Constant)}
                    if isinstance(params_expr, ast.Dict)
                    else None
                )
                # Nothing resolved: a forwarded parameter, checked where the helper is called.
                for key in resolve(key_expr):
                    found[f"{path.relative_to(APP_DIR)}:{node.lineno}:{key}"] = (key, params)
    return found


def test_FR_NOT_001_every_notification_sent_by_the_code_has_a_bilingual_template() -> None:
    sent = _sent_template_keys()
    keys = {key for key, _ in sent.values()}
    # The scan sees literals, module constants, function-local literals and forwarding helpers.
    assert {
        "export.ready",
        "extraction.batch.ready",
        "change_request.expired",
        "task.due_soon",
        "task.overdue",
    } <= keys
    catalog = t.catalog()
    problems = []
    for where, (key, params) in sorted(sent.items()):
        template = catalog.get(key)
        if template is None:
            problems.append(f"{where}: {key} has no template")
        elif params is not None and params != set(template.params):
            problems.append(f"{where}: {key} params {sorted(params)} != {sorted(template.params)}")
    assert problems == []


def test_FR_NOT_001_every_template_has_a_producer() -> None:
    """A template nothing sends is dead copy that reviewers keep checking and docs keep
    listing. Remove it (as ``announcement.new`` was, 2026-10-04) instead of keeping it."""
    sent = {key for key, _ in _sent_template_keys().values()}
    assert sorted(set(t.catalog()) - sent) == []


def test_FR_NOT_001_announcements_are_banner_only() -> None:
    """Owner decision 2026-10-04: platform announcements reach schools only as the banner
    (``GET /announcements``); the control plane never writes school notifications (ADR-0020),
    so there is no ``announcement.new`` template and nothing links to an ``announcement``."""
    catalog = t.catalog()
    assert "announcement.new" not in catalog
    assert not [key for key in catalog if key.startswith("announcement.")]
    assert all(template.resource_type != "announcement" for template in catalog.values())
    with pytest.raises(t.TemplateError):
        t.get("announcement.new")
