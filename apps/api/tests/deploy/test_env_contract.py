"""Deploy configuration vs the settings contract (SEC-009, SEC-011, NFR-AVL-002, FR-PLT-016/024).

``apps/api/app/core/config.py`` is the only code that reads the environment. Every variable that
Terraform (shared tier, ECS) or ``deploy/dedicated/compose.yaml`` (dedicated tier) hands to an app
container must therefore be a known ``Settings`` name, and each container's environment must pass
the staging/prod start-up guards on its own. These tests parse the deploy files directly (no
Terraform or Docker needed) so a renamed or missing variable fails CI instead of a deploy.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.config import (
    DEV_SUPPLIER_GSTIN,
    DEV_SUPPLIER_NAME,
    DeploymentMode,
    Environment,
    KeyWrapperKind,
    Settings,
)

REPO = Path(__file__).resolve().parents[4]
DEDICATED = REPO / "deploy" / "dedicated"
COMPOSE = DEDICATED / "compose.yaml"
ENV_TEMPLATE = DEDICATED / ".env.template"
SHARED = REPO / "infra" / "terraform" / "modules" / "shared_platform"
HOST = REPO / "infra" / "terraform" / "modules" / "dedicated_host"
CLOUD_INIT = HOST / "templates" / "cloud-init.yaml.tftpl"

APP_CONTAINERS = ("api", "worker", "beat", "migrate")

# Names app containers receive that are NOT Settings fields, and who reads them. Keep it short:
# anything else must be a Settings field (or be removed from the deploy files).
NOT_SETTINGS: dict[str, str] = {
    # boto3 / AWS CLI read it directly; Settings uses AWS_REGION.
    "AWS_DEFAULT_REGION": "AWS SDK default region on dedicated hosts",
    # Mount point of /var/lib/schoolos/state (backup.json from scripts/backup.sh) for the heartbeat.
    "SOS_HOST_STATE_DIR": "dedicated host state mount",
    # Knowledge gateway / embeddings (M2): remove from this list when their settings land.
    "SOS_ANTHROPIC_API_KEY": "knowledge gateway (M2)",
    "SOS_EMBEDDINGS_API_KEY": "embeddings provider (M2)",
}

SYNTHETIC_TENANT = "0192a0de-0000-7000-8000-00000000a001"
SYNTHETIC_DEPLOYMENT = "0192a0de-0000-7000-8000-00000000d001"


def settings_env_names() -> set[str]:
    names: set[str] = set()
    for field_name, field in Settings.model_fields.items():
        alias = field.validation_alias
        names.add(alias if isinstance(alias, str) else f"SOS_{field_name.upper()}")
    return names


# --- dedicated tier: compose ----------------------------------------------------------------------

_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?])([^}]*))?\}")


def interpolate(value: str, env: Mapping[str, str]) -> str:
    """Docker Compose variable interpolation (``${X}``, ``${X:-d}``, ``${X:?}``, ``$$``)."""

    def repl(match: re.Match[str]) -> str:
        name, op, arg = match.group(1), match.group(2), match.group(3) or ""
        current = env.get(name)
        if op in (":?", "?"):
            if current is None or (op == ":?" and current == ""):
                raise KeyError(f"compose requires {name}, which the host env does not provide")
            return current
        if op == ":-":
            return current if current else arg
        if op == "-":
            return current if current is not None else arg
        return current or ""

    return _INTERPOLATION.sub(repl, value.replace("$$", "\0")).replace("\0", "$")


def compose_doc() -> dict[str, Any]:
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def compose_env(service: str) -> dict[str, str]:
    services = compose_doc()["services"]
    assert isinstance(services, dict)
    env = services[service].get("environment", {})
    assert isinstance(env, dict), f"{service}: environment must be a mapping"
    return {str(k): str(v) for k, v in env.items()}


def _template_sections() -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in ENV_TEMPLATE.read_text(encoding="utf-8").splitlines():
        header = re.match(r"^# --- (.+?) -+$", line)
        if header:
            current = header.group(1)
            continue
        sections.setdefault(current, []).append(line)
    return sections


def template_assignments(section_prefix: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for title, lines in _template_sections().items():
        if not title.startswith(section_prefix):
            continue
        for line in lines:
            m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"')
    return out


def template_secret_names() -> set[str]:
    names: set[str] = set()
    for title, lines in _template_sections().items():
        if title.startswith("secrets.env keys"):
            for line in lines:
                body = line.lstrip("#").split(":", 1)[-1]
                names.update(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", body))
    return names


def synthetic_secret(name: str) -> str:
    if name == "SOS_HEARTBEAT_KEY_ID":
        return "hb-synthetickeyidab"
    return f"synthetic-{name.lower().replace('_', '-')}-0123456789abcdef"


def dedicated_host_env() -> dict[str, str]:
    """What scripts/lib.sh renders into compose.env: host.env + version/release + secrets.env."""
    env = template_assignments("host.env")
    env.update(template_assignments("version.env"))
    env.update({name: synthetic_secret(name) for name in template_secret_names()})
    return env


# --- shared tier: Terraform (minimal HCL reading; enough for maps of NAME = value) ----------------


def _brace_body(text: str, start: int) -> str:
    """Body of the block whose opening ``{`` is the first one at/after ``start``."""
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1 : i]
    raise ValueError("unbalanced braces")


def _attributes(body: str) -> dict[str, str]:
    """Top-level ``name = expression`` pairs of an HCL block body (nested blocks ignored)."""
    attrs: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    depth = 0
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        m = re.match(r"^([a-z_][a-z0-9_]*)\s*=\s*(.*)$", line)
        if depth == 0 and m:
            if name is not None:
                attrs[name] = "\n".join(buf)
            name, buf = m.group(1), [m.group(2)]
        elif depth == 0 and re.match(r"^[a-z_\"][\w\" -]*\{$", line):
            if name is not None:
                attrs[name] = "\n".join(buf)
            name, buf = None, []
        else:
            buf.append(line)
        depth += sum(line.count(c) for c in "{[(") - sum(line.count(c) for c in "}])")
    if name is not None:
        attrs[name] = "\n".join(buf)
    return attrs


class Hcl:
    def __init__(self, *files: Path) -> None:
        self.text = "\n".join(f.read_text(encoding="utf-8") for f in files)
        self.locals: dict[str, str] = {}
        for m in re.finditer(r"(?m)^locals\s*\{", self.text):
            self.locals.update(_attributes(_brace_body(self.text, m.start())))
        self.var_defaults: dict[str, str] = {}
        for m in re.finditer(r'(?m)^variable\s+"(\w+)"\s*\{', self.text):
            default = _attributes(_brace_body(self.text, m.start())).get("default")
            if default is not None:
                self.var_defaults[m.group(1)] = default

    def block(self, kind: str, name: str) -> dict[str, str]:
        m = re.search(rf'(?m)^{kind}\s+"{re.escape(name)}"\s*\{{', self.text)
        assert m, f'{kind} "{name}" not found'
        return _attributes(_brace_body(self.text, m.start()))

    def map_entries(self, expr: str, seen: frozenset[str] = frozenset()) -> dict[str, str]:
        """NAME => value expression for every UPPER_CASE key a map expression produces."""
        out: dict[str, str] = {}
        for ref_kind, ref in re.findall(r"\b(local|var)\.([a-z_][a-z0-9_]*)", expr):
            key = f"{ref_kind}.{ref}"
            source = self.locals if ref_kind == "local" else self.var_defaults
            if key in seen or ref not in source:
                continue
            out.update(self.map_entries(source[ref], seen | {key}))
        for m in re.finditer(r'\b([A-Z][A-Z0-9_]*)\s*=\s*("(?:[^"\\]|\\.)*"|[^,\n}]+)', expr):
            out[m.group(1)] = m.group(2).strip()
        return out

    def container_env(self, module: str) -> dict[str, str]:
        attrs = self.block("module", module)
        env = self.map_entries(attrs.get("environment", "{}"))
        env.update(self.map_entries(attrs.get("secrets", "{}")))
        return env


def shared_hcl() -> Hcl:
    return Hcl(SHARED / "main.tf", SHARED / "variables.tf")


def host_hcl() -> Hcl:
    return Hcl(HOST / "main.tf", HOST / "variables.tf")


def _hcl_literal(expr: str) -> str | None:
    m = re.fullmatch(r'"([^"$]*)"', expr.strip())
    return m.group(1) if m else None


def synthetic_value(name: str) -> str:
    """A syntactically valid, obviously synthetic value for a computed/secret variable."""
    if name.endswith("DATABASE_URL"):
        return f"postgresql+psycopg://sos_role:synthetic-{name.lower()}@db.example.test:5432/sos"
    values = {
        "SOS_REDIS_URL": "rediss://:synthetic@valkey.example.test:6379/0",
        "SOS_BILLING_SUPPLIER_GSTIN": "37ABCDE1234F1Z5",
        "SOS_BILLING_SUPPLIER_STATE_CODE": "37",
        "SOS_BILLING_SUPPLIER_LEGAL_NAME": "Synthetic Staging Supplier Private Limited",
        "SOS_DEDICATED_TENANT_ID": SYNTHETIC_TENANT,
        "SOS_DEPLOYMENT_ID": SYNTHETIC_DEPLOYMENT,
        "AWS_REGION": "ap-south-1",
        "SOS_LOG_LEVEL": "INFO",
    }
    if name in values:
        return values[name]
    if name.endswith("_ARN"):
        return "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
    return synthetic_secret(name)


def terraform_container_env(module: str, env: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, expr in shared_hcl().container_env(module).items():
        literal = _hcl_literal(expr)
        if expr.strip() == "var.env":
            out[name] = env
        elif literal is not None:
            out[name] = literal
        else:
            out[name] = synthetic_value(name)
    return out


def build_settings(monkeypatch: pytest.MonkeyPatch, env: Mapping[str, str]) -> Settings:
    """Settings from exactly ``env`` (the test process's own SOS_* variables are removed)."""
    for name in list(os.environ):
        if name.startswith("SOS_") or name in ("AWS_REGION", "AWS_DEFAULT_REGION"):
            monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings()


# --- names ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("service", APP_CONTAINERS)
def test_SEC_009_dedicated_compose_passes_only_settings_names(service: str) -> None:
    unknown = set(compose_env(service)) - settings_env_names() - set(NOT_SETTINGS)
    assert not unknown, f"compose service {service} sets names config.py never reads: {unknown}"


@pytest.mark.parametrize("module", APP_CONTAINERS)
def test_SEC_009_shared_tier_tasks_pass_only_settings_names(module: str) -> None:
    names = set(shared_hcl().container_env(module))
    assert names, f"no environment parsed for module {module}"
    unknown = names - settings_env_names() - set(NOT_SETTINGS)
    assert not unknown, f"ECS task {module} sets names config.py never reads: {unknown}"


def test_SEC_009_allowlist_entries_are_really_used() -> None:
    used: set[str] = set()
    for service in APP_CONTAINERS:
        used |= set(compose_env(service))
    for module in APP_CONTAINERS:
        used |= set(shared_hcl().container_env(module))
    stale = set(NOT_SETTINGS) - used
    assert not stale, f"remove stale NOT_SETTINGS entries: {stale}"
    assert not set(NOT_SETTINGS) & settings_env_names(), "allowlisted names are Settings fields"


# --- start-up guards per container ----------------------------------------------------------------


@pytest.mark.parametrize("service", APP_CONTAINERS)
def test_NFR_AVL_002_dedicated_containers_start_in_prod(
    service: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_env = dedicated_host_env()
    env = {k: interpolate(v, host_env) for k, v in compose_env(service).items()}
    settings = build_settings(monkeypatch, env)  # raises if a start-up guard refuses
    assert settings.env is Environment.PROD
    assert settings.deployment_mode is DeploymentMode.DEDICATED
    assert settings.key_wrapper is KeyWrapperKind.KMS
    assert settings.kms_data_key_arn, "SOS_KEY_WRAPPER=kms needs SOS_KMS_DATA_KEY_ARN"
    assert settings.audit_signing_key_arn, "audit archives are signed (FR-AUD-004)"
    # Heartbeat identity (FR-PLT-024): beat schedules it, the worker sends it.
    assert settings.control_plane_url
    assert settings.deployment_id
    assert settings.dedicated_tenant_id
    assert settings.heartbeat_key_id
    assert settings.heartbeat_key is not None
    assert settings.version != "0.0.0-dev"
    if service == "migrate":
        assert "dev-only" not in settings.migrator_database_url.get_secret_value()


@pytest.mark.parametrize("env", ["staging", "prod"])
@pytest.mark.parametrize("module", APP_CONTAINERS)
def test_NFR_AVL_002_shared_tier_tasks_start(
    module: str, env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = build_settings(monkeypatch, terraform_container_env(module, env))
    assert settings.env.value == env
    assert settings.deployment_mode is DeploymentMode.SHARED
    assert settings.key_wrapper is KeyWrapperKind.KMS
    assert settings.kms_data_key_arn, "SOS_KEY_WRAPPER=kms needs SOS_KMS_DATA_KEY_ARN"
    assert settings.audit_signing_key_arn, "audit archives are signed (FR-AUD-004)"
    assert settings.billing_supplier_gstin != DEV_SUPPLIER_GSTIN
    assert settings.billing_supplier_legal_name != DEV_SUPPLIER_NAME
    assert settings.version != "0.0.0-dev"
    if module == "migrate":
        assert "dev-only" not in settings.migrator_database_url.get_secret_value()


def test_SEC_009_guard_really_refuses_a_bare_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check above is meaningful: SOS_ENV alone (the old beat/migrate env) is refused."""
    with pytest.raises(ValueError, match="local-dev"):
        build_settings(monkeypatch, {"SOS_ENV": "prod", "SOS_LOG_LEVEL": "INFO"})


# --- dedicated host: Terraform <-> template <-> compose -------------------------------------------


def _cloud_init_host_env() -> set[str]:
    text = CLOUD_INIT.read_text(encoding="utf-8")
    start = text.index("path: /etc/schoolos/host.env")
    end = text.index("- path:", start + 1)
    return set(re.findall(r"(?m)^\s+([A-Z][A-Z0-9_]*)=", text[start:end]))


def test_SEC_009_cloud_init_host_env_matches_template() -> None:
    rendered = _cloud_init_host_env()
    # cloud-init also seeds the first release's SOS_VERSION; version.env overrides it later.
    documented = set(template_assignments("host.env")) | {"SOS_VERSION"}
    assert rendered == documented, {
        "rendered_not_documented": rendered - documented,
        "documented_not_rendered": documented - rendered,
    }


def test_SEC_009_host_secret_keys_match_template() -> None:
    hcl = host_hcl()
    generated = set(re.findall(r'"([A-Z][A-Z0-9_]*)"', hcl.locals["generated_keys"]))
    operator = set(re.findall(r'"([A-Z][A-Z0-9_]*)"', hcl.var_defaults["operator_secret_keys"]))
    plain_line = CLOUD_INIT.read_text(encoding="utf-8").split("SOS_SECRET_PLAIN=", 1)[1]
    plain = set(re.findall(r"([A-Z][A-Z0-9_]*)=\$\{", plain_line.splitlines()[0]))
    assert generated
    assert operator
    assert plain
    assert generated | operator | plain == template_secret_names()


def test_SEC_009_compose_needs_only_what_the_host_provides() -> None:
    host_env = dedicated_host_env()
    for service, spec in compose_doc()["services"].items():
        for value in (spec.get("environment") or {}).values():
            interpolate(str(value), host_env)  # KeyError names the missing variable
        assert service


def test_FR_PLT_024_heartbeat_key_is_an_operator_secret() -> None:
    operator = host_hcl().var_defaults["operator_secret_keys"]
    assert '"SOS_HEARTBEAT_KEY"' in operator
    assert '"SOS_HEARTBEAT_KEY_ID"' in operator


# --- Celery queues --------------------------------------------------------------------------------


def _queues(value: str) -> set[str]:
    return {q.strip() for q in value.split(",") if q.strip()}


def test_NFR_AVL_002_workers_consume_every_celery_queue() -> None:
    from sos_worker.celery_app import QUEUES

    worker = compose_doc()["services"]["worker"]
    command = worker["command"]
    compose_queues = interpolate(command[command.index("-Q") + 1], {})
    assert _queues(compose_queues) == set(QUEUES)
    tf_default = shared_hcl().var_defaults["worker_queues"]
    assert _queues(tf_default.strip().strip('"')) == set(QUEUES)
