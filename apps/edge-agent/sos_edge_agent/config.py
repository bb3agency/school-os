"""The agent's configuration file (TOML; no secrets in it) and where its state lives.

Fields: ``server_url`` (the school's SchoolOS address, HTTPS), ``tenant_id`` (the school id shown
on the connector screen), ``device_id`` and ``key_id`` (after enrolment), ``tally_url`` (Tally's
XML server on THIS PC; any other host is refused), ``key_rotated_at``. The device secret is kept
by :mod:`.credentials` (Windows DPAPI), never here.

State directory: ``%LOCALAPPDATA%\\SchoolOS\\TallyAgent`` of the service account on Windows (so
other users of the shared office PC cannot read it), ``~/.local/state/sos-tally-agent``
elsewhere (development only).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import ipaddress
import os
import sys
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

CONFIG_NAME: Final = "agent.toml"
LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_TALLY_URL: Final = "http://127.0.0.1:9000"


class ConfigError(ValueError):
    """The configuration is missing or unsafe (the message says how to fix it)."""


def default_state_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "SchoolOS" / "TallyAgent"
    return Path.home() / ".local" / "state" / "sos-tally-agent"


def _is_local(host: str | None) -> bool:
    if host is None:
        return False
    if host in LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_tally_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "http" or not _is_local(parts.hostname):
        raise ConfigError(
            "tally_url must be Tally's XML server on this PC, e.g. http://127.0.0.1:9000. "
            "SchoolOS never reads Tally over the network."
        )
    if parts.path not in ("", "/") or parts.query:
        raise ConfigError("tally_url must be just the address and port, without a path")
    return f"http://{parts.netloc}"


def check_server_url(url: str) -> str:
    parts = urlsplit(url.rstrip("/"))
    local = _is_local(parts.hostname)
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ConfigError("server_url must start with https:// (plain http only for localhost)")
    if not parts.hostname or parts.query or parts.fragment or parts.path not in ("",):
        raise ConfigError("server_url must be the SchoolOS address only, e.g. https://x.example")
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True, slots=True)
class AgentConfig:
    server_url: str
    tenant_id: uuid.UUID
    device_id: uuid.UUID | None = None
    key_id: str | None = None
    tally_url: str = DEFAULT_TALLY_URL
    key_rotated_at: dt.datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "server_url", check_server_url(self.server_url))
        object.__setattr__(self, "tally_url", check_tally_url(self.tally_url))

    @property
    def enrolled(self) -> bool:
        return self.device_id is not None and self.key_id is not None

    def replace(self, **changes: object) -> AgentConfig:
        return dataclasses.replace(self, **changes)  # type: ignore[arg-type]


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    if any(ch in escaped for ch in "\n\r\t"):
        raise ConfigError("configuration values cannot contain line breaks")
    return f'"{escaped}"'


def save(config: AgentConfig, path: Path) -> None:
    lines = [
        "# SchoolOS Tally agent (ADR-0032). No secrets here: the device key is kept by Windows.",
        f"server_url = {_quote(config.server_url)}",
        f"tenant_id = {_quote(str(config.tenant_id))}",
        f"tally_url = {_quote(config.tally_url)}",
    ]
    if config.device_id is not None:
        lines.append(f"device_id = {_quote(str(config.device_id))}")
    if config.key_id is not None:
        lines.append(f"key_id = {_quote(config.key_id)}")
    if config.key_rotated_at is not None:
        lines.append(f"key_rotated_at = {_quote(config.key_rotated_at.isoformat())}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def load(path: Path) -> AgentConfig:
    if not path.is_file():
        raise ConfigError(f"no configuration at {path}: run `sos-tally-agent enrol` first")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML") from exc
    try:
        rotated = raw.get("key_rotated_at")
        return AgentConfig(
            server_url=str(raw["server_url"]),
            tenant_id=uuid.UUID(str(raw["tenant_id"])),
            device_id=uuid.UUID(str(raw["device_id"])) if raw.get("device_id") else None,
            key_id=str(raw["key_id"]) if raw.get("key_id") else None,
            tally_url=str(raw.get("tally_url", DEFAULT_TALLY_URL)),
            key_rotated_at=dt.datetime.fromisoformat(str(rotated)) if rotated else None,
        )
    except (KeyError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"{path} is missing server_url or tenant_id, or has a bad id") from exc


__all__ = [
    "CONFIG_NAME",
    "DEFAULT_TALLY_URL",
    "AgentConfig",
    "ConfigError",
    "check_server_url",
    "check_tally_url",
    "default_state_dir",
    "load",
    "save",
]
