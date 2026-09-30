""".env.example lists every setting, so it cannot drift from ``Settings`` (docs/10 §11; SEC-009,
CLAUDE.md invariant 10).

``make dev`` and ``make dev-host`` copy ``.env.example`` to ``.env``. Every ``Settings`` field
(``app/core/config.py``, the only code that reads the environment) must appear in it, either set
(the dev-only local values) or commented out as ``# SOS_NAME=<default>``. A commented-out value
must be exactly the default, written so that uncommenting it parses to that default (empty means
the default is unset). Every other ``SOS_*`` name in the file is a known non-setting (compose or
web only), and every secret-looking value is a ``dev-only`` placeholder or empty.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.core.config import Settings

REPO = Path(__file__).resolve().parents[4]
ENV_EXAMPLE = REPO / ".env.example"

LINE = re.compile(r"^(?P<comment>#\s*)?(?P<name>[A-Z][A-Z0-9_]*)=(?P<value>.*)$")

# SOS_* names in .env.example that are not Settings fields, and who reads them.
NOT_SETTINGS: dict[str, str] = {
    "SOS_DB_ADMIN_PASSWORD": "docker compose / infra/docker/db-init.sh (role passwords)",
    "SOS_DB_APP_PASSWORD": "docker compose / scripts/dev.py (builds SOS_DATABASE_URL)",
    "SOS_DB_MIGRATOR_PASSWORD": "docker compose / scripts/dev.py",
    "SOS_DB_PLATFORM_PASSWORD": "docker compose / scripts/dev.py",
    "SOS_DB_READONLY_PASSWORD": "docker compose / infra/docker/db-init.sh",
    "SOS_DB_PORT": "docker compose / scripts/dev.py host port",
    "SOS_VALKEY_PORT": "docker compose / scripts/dev.py host port",
    "SOS_INSTALL_PSQL": "docker compose build argument INSTALL_PSQL",
    "SOS_PUBLIC_CONTACT_EMAIL": "web only: apps/web/src/features/marketing/settings.ts",
    "SOS_PUBLIC_COMPANY_NAME": "web only: apps/web/src/features/marketing/settings.ts",
    "SOS_PUBLIC_COMPANY_ADDRESS": "web only: apps/web/src/features/marketing/settings.ts",
    "SOS_PUBLIC_WHATSAPP_NUMBER": "web only: apps/web/src/features/marketing/settings.ts",
}

SECRET_NAME = re.compile(r"(SECRET|PASSWORD|_KEY$|MASTER_KEY|TOKEN_KEY)")


def env_name(field_name: str) -> str:
    alias = Settings.model_fields[field_name].validation_alias
    return alias if isinstance(alias, str) else f"SOS_{field_name.upper()}"


def example_lines() -> dict[str, tuple[bool, str]]:
    """name -> (commented out, value) for every ``NAME=value`` line, set or commented."""
    found: dict[str, tuple[bool, str]] = {}
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = LINE.match(raw.strip())
        if m is None:
            continue
        name = m["name"]
        assert name not in found, f"{name} appears twice in .env.example"
        found[name] = (m["comment"] is not None, m["value"].strip())
    return found


def plain(value: Any) -> Any:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def test_SEC_009_every_setting_is_listed_in_env_example() -> None:
    listed = example_lines()
    missing = sorted(env_name(f) for f in Settings.model_fields if env_name(f) not in listed)
    assert not missing, f"add to .env.example (commented out, with default): {missing}"


def test_SEC_009_every_sos_name_in_env_example_is_known() -> None:
    settings = {env_name(f) for f in Settings.model_fields}
    unknown = sorted(
        n
        for n in example_lines()
        if n.startswith("SOS_") and n not in settings and n not in NOT_SETTINGS
    )
    assert not unknown, f"not a Settings field (typo or stale name?): {unknown}"


@pytest.mark.parametrize("field_name", sorted(Settings.model_fields))
def test_SEC_009_commented_values_are_the_defaults(
    field_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = env_name(field_name)
    commented, value = example_lines()[name]
    if not commented:
        return  # a set dev-only value, deliberately different from the default
    default = Settings.model_fields[field_name].default
    if value == "":
        assert default is None, f"{name}: empty means unset, but the default is {default!r}"
        return
    for key in list(os.environ):
        if key.startswith("SOS_") or key == "AWS_REGION":
            monkeypatch.delenv(key)
    monkeypatch.setenv(name, value)
    parsed = getattr(Settings(), field_name)
    assert plain(parsed) == plain(default), f"{name}={value} parses to {parsed!r}"


def test_SEC_009_env_example_holds_only_placeholders() -> None:
    for name, (_, value) in example_lines().items():
        if SECRET_NAME.search(name) and value:
            assert "dev-only" in value, f"{name} must be a dev-only placeholder or empty"
