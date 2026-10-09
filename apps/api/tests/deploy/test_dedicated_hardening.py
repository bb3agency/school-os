"""Deployment hardening notes from the 2026-10-04 and 2026-10-05 audits (SEC-030, SEC-009).

- db-bootstrap passwords were on psql's command line (``-v app_password=...``), readable in
  ``/proc/*/cmdline`` while it ran; psql now reads them with ``\\getenv`` from its environment.
- web sat on the dedicated hosts' ``data`` network and could reach ``db:5432``.
- Caddy ran as root, without explicit server timeouts, trusted ``X-Forwarded-For`` from every
  private address, and sent ``includeSubDomains; preload`` HSTS on a school's own domain.
- caddy, pgvector, valkey and the API base image were pinned by tag only.
- the images defaulted to ``SOS_ENV=local``, which turns every staging/prod guard off.
- the migrator URL had no dev-only guard (the api never sets it, so the guard belongs to the
  migrate entrypoint, not to the start-up checks).
Synthetic values only.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.config import Environment, Settings

REPO = Path(__file__).resolve().parents[4]
DEDICATED = REPO / "deploy" / "dedicated"
COMPOSE = DEDICATED / "compose.yaml"
CADDYFILE = DEDICATED / "Caddyfile"
BOOTSTRAP_SQL = REPO / "infra" / "db" / "bootstrap.sql"
SHARED = REPO / "infra" / "terraform" / "modules" / "shared_platform" / "main.tf"
API_DOCKERFILE = REPO / "apps" / "api" / "Dockerfile"
MIGRATIONS_ENV = REPO / "apps" / "api" / "migrations" / "env.py"
PARTITIONS = REPO / "apps" / "api" / "app" / "audit" / "partitions.py"
DIGEST = r"@sha256:[0-9a-f]{64}"


def _services() -> dict[str, Any]:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert isinstance(services, dict)
    return services


def _networks(service: str) -> set[str]:
    return set(_services()[service].get("networks", []))


# --- db-bootstrap: passwords never on a command line ----------------------------------------------


def _bootstrap_variables() -> set[str]:
    names = set(re.findall(r":'(\w+_password)'", BOOTSTRAP_SQL.read_text(encoding="utf-8")))
    assert names, "bootstrap.sql uses no password variables"
    return names


def _check_bootstrap_command(command: str) -> None:
    assert "password=" not in command.lower(), "a password variable is set on psql's argv"
    assert not re.search(r"-v\s+\w+_password", command)
    for name in _bootstrap_variables():
        assert re.search(rf"\\\\?getenv {name} [A-Z_]+", command), f"{name} is not read with getenv"


def test_hardening_dedicated_db_bootstrap_reads_passwords_with_getenv() -> None:
    command = " ".join(_services()["db-bootstrap"]["command"])
    _check_bootstrap_command(command)


def test_hardening_shared_db_bootstrap_reads_passwords_with_getenv() -> None:
    text = SHARED.read_text(encoding="utf-8")
    block = text[text.index('module "db_bootstrap"') :]
    command = block[block.index("command = [") : block.index("environment = {")]
    _check_bootstrap_command(command)


# --- network segmentation ------------------------------------------------------------------------


def test_hardening_web_cannot_reach_the_database_network() -> None:
    assert "data" not in _networks("web")
    assert _networks("db") == {"data"}
    assert _networks("valkey") == {"cache"}
    networks = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["networks"]
    assert networks["data"]["internal"] is True
    assert networks["cache"]["internal"] is True
    for service in ("api", "worker", "beat"):
        assert {"data", "cache"} <= _networks(service)


# --- Caddy -------------------------------------------------------------------------------------


def test_hardening_caddy_runs_as_a_non_root_user() -> None:
    caddy = _services()["caddy"]
    uid = str(caddy.get("user", "0")).split(":")[0]
    assert uid not in ("", "0", "root")
    # Binding 80/443 needs no capability: the port floor is lowered in the container's own
    # network namespace. NET_BIND_SERVICE stays in the bounding set only so the image's caddy
    # binary (which carries that file capability) can be executed; no-new-privileges keeps it out
    # of the process's effective set.
    assert str(caddy["sysctls"]["net.ipv4.ip_unprivileged_port_start"]) == "80"
    assert caddy.get("cap_add", []) in ([], ["NET_BIND_SERVICE"])
    assert "no-new-privileges:true" in caddy["security_opt"]
    assert caddy["cap_drop"] == ["ALL"]


def _caddy_uid_of(compose_dir: Path) -> str:
    from tests.deploy.test_release_pins import _bash

    bash = _bash()
    if bash is None:
        pytest.skip("bash is not installed")
    lib = (DEDICATED / "scripts" / "lib.sh").as_posix()
    result = subprocess.run(
        [bash, "-c", f'. "{lib}"\ncaddy_uid_of "{compose_dir.as_posix()}"\n'],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stdout.strip()


def test_hardening_upgrade_owns_the_caddy_store_for_the_release_user(tmp_path: Path) -> None:
    """upgrade.sh re-owns Caddy's certificate store for the release it starts (and its rollback for
    the previous one): Caddy has no capability, so root cannot read a 10001-owned store."""
    assert _caddy_uid_of(DEDICATED) == "10001"
    old = tmp_path / "old"
    old.mkdir()
    (old / "compose.yaml").write_text(
        "services:\n  caddy:\n    image: caddy\n    cap_add:\n      - NET_BIND_SERVICE\n"
        '  web:\n    user: "10001:10001"\n',
        encoding="utf-8",
        newline="\n",
    )
    assert _caddy_uid_of(old) == "0", "a release without a Caddy user runs it as root"
    upgrade = (DEDICATED / "scripts" / "upgrade.sh").read_text(encoding="utf-8")
    rollback = upgrade[upgrade.index("rollback() {") : upgrade.index("trap rollback ERR")]
    assert 'prepare_caddy_dirs "$(active_release_dir)"' in rollback
    restart = upgrade[upgrade.index("for svc in db valkey") :]
    assert restart.index("prepare_caddy_dirs") < restart.index('up -d --no-deps "$svc"')


def _caddy_global() -> str:
    text = CADDYFILE.read_text(encoding="utf-8")
    depth, start = 0, text.index("{")
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start : i + 1]
    raise AssertionError("unbalanced Caddyfile")


def test_hardening_caddy_sets_server_timeouts() -> None:
    block = _caddy_global()
    timeouts = re.search(r"timeouts\s*\{([^}]*)\}", block)
    assert timeouts, "no explicit server timeouts"
    values = dict(re.findall(r"(\w+)\s+(\S+)", timeouts.group(1)))
    assert values.get("read_header") == "10s"  # slow-header (slowloris) clients
    assert "read_body" in values
    assert "idle" in values
    assert "write" not in values, "a write timeout would cut streamed AI answers (SSE)"


def test_hardening_caddy_trusts_no_forwarded_headers() -> None:
    """Caddy is the first hop on a dedicated host: nothing in front of it may set
    X-Forwarded-For (a client seen through Docker's proxy has a private address)."""
    text = CADDYFILE.read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*trusted_proxies\b", text)
    assert not re.search(r"(?m)^\s*client_ip_headers\b", text)


def test_hardening_hsts_preload_only_on_our_own_domain() -> None:
    text = CADDYFILE.read_text(encoding="utf-8")
    preload = re.findall(
        r"(?m)^\s*header\s+(@\w+)\s+Strict-Transport-Security\s+\"([^\"]+)\"", text
    )
    assert preload, "HSTS is set per host matcher"
    by_matcher = dict(preload)
    platform = re.search(r"@(\w+)\s+host\s+\{\$SOS_PUBLIC_HOST\}", text)
    assert platform, "a matcher for the platform host name"
    assert "includeSubDomains; preload" in by_matcher[f"@{platform.group(1)}"]
    others = [v for k, v in by_matcher.items() if k != f"@{platform.group(1)}"]
    assert others, "the school's custom domain gets HSTS too"
    assert all("includeSubDomains" not in v and "preload" not in v for v in others)
    assert all("max-age=" in v for v in others)
    assert not re.search(r"(?m)^\s*Strict-Transport-Security\s", text), "no unconditional HSTS"


# --- images pinned by digest -------------------------------------------------------------------


@pytest.mark.parametrize("service", ["caddy", "db", "db-bootstrap", "valkey"])
def test_hardening_third_party_images_are_pinned_by_digest(service: str) -> None:
    image = _services()[service]["image"]
    assert re.fullmatch(rf"[a-z0-9./-]+:[\w.-]+{DIGEST}", image), image


def test_hardening_api_base_image_is_pinned_by_digest() -> None:
    m = re.search(r"(?m)^ARG PYTHON_IMAGE=(\S+)$", API_DOCKERFILE.read_text(encoding="utf-8"))
    assert m
    assert re.fullmatch(rf"python:3\.12-slim-bookworm{DIGEST}", m.group(1)), m.group(1)


# --- SOS_ENV defaults to prod in the image ------------------------------------------------------


def test_hardening_python_image_defaults_to_prod() -> None:
    """Every guard is off in local; a container started without SOS_ENV must fail closed.
    Local compose (.env) and CI (workflow env) set SOS_ENV explicitly."""
    text = API_DOCKERFILE.read_text(encoding="utf-8")
    base = text[text.index("AS base") : text.index("AS worker")]
    assert re.search(r"(?m)^ENV .*\bSOS_ENV=prod\b", base)
    env_example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"(?m)^SOS_ENV=local$", env_example)
    for workflow in ("ci.yml", "nightly.yml"):
        wf = (REPO / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert re.search(r"(?m)^  SOS_ENV: ci$", wf), workflow


# --- migrator URL: dev-only guard in the migrate entrypoint -------------------------------------


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_hardening_migrate_refuses_the_dev_only_migrator_url(env: Environment) -> None:
    settings = Settings.model_construct(
        env=env,
        migrator_database_url=Settings.model_fields["migrator_database_url"].default,
    )
    with pytest.raises(ValueError, match="dev-only"):
        settings.checked_migrator_url()


def test_hardening_migrate_accepts_a_real_migrator_url_and_local_defaults() -> None:
    from pydantic import SecretStr

    real = "postgresql+psycopg://sos_migrator:synthetic-pw@db:5432/schoolos"
    prod = Settings.model_construct(env=Environment.PROD, migrator_database_url=SecretStr(real))
    assert prod.checked_migrator_url() == real
    local = Settings.model_construct(
        env=Environment.LOCAL,
        migrator_database_url=Settings.model_fields["migrator_database_url"].default,
    )
    assert "dev-only" in local.checked_migrator_url()


def test_hardening_migrate_entrypoints_use_the_checked_url() -> None:
    for path in (MIGRATIONS_ENV, PARTITIONS):
        text = path.read_text(encoding="utf-8")
        assert "checked_migrator_url()" in text, path.name
        assert "migrator_database_url.get_secret_value()" not in text, path.name
