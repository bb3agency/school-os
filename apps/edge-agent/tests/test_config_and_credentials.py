"""Configuration safety and the device-key store (ADR-0032 §1, §7)."""

from __future__ import annotations

import datetime as dt
import os
import stat
import sys
import uuid
from pathlib import Path

import pytest

from sos_edge_agent import config as cfg
from sos_edge_agent import credentials as cred

TENANT = uuid.UUID("01920000-0000-7000-8000-00000000a001")


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.20:9000",  # another PC: SchoolOS never reads Tally over the network
        "http://tally.local:9000",
        "https://127.0.0.1:9000",
        "http://127.0.0.1:9000/xml",
        "ftp://127.0.0.1:9000",
    ],
)
def test_ADR_0032_tally_must_be_on_this_pc(url: str) -> None:
    with pytest.raises(cfg.ConfigError):
        cfg.check_tally_url(url)


@pytest.mark.parametrize(
    ("url", "normal"),
    [
        ("http://127.0.0.1:9000", "http://127.0.0.1:9000"),
        ("http://localhost:9000/", "http://localhost:9000"),
        ("http://[::1]:9000", "http://[::1]:9000"),
    ],
)
def test_ADR_0032_local_tally_urls_are_accepted(url: str, normal: str) -> None:
    assert cfg.check_tally_url(url) == normal


@pytest.mark.parametrize(
    "url",
    ["http://school.example.in", "https://school.example.in/api", "https://", "school.example"],
)
def test_ADR_0032_schoolos_is_reached_over_https_only(url: str) -> None:
    with pytest.raises(cfg.ConfigError):
        cfg.check_server_url(url)
    assert cfg.check_server_url("https://school.example.in/") == "https://school.example.in"
    assert cfg.check_server_url("http://localhost:8000") == "http://localhost:8000"


def test_ADR_0032_the_config_file_round_trips_and_holds_no_secret(tmp_path: Path) -> None:
    path = tmp_path / "agent.toml"
    config = cfg.AgentConfig(
        server_url="https://school.synthetic.test",
        tenant_id=TENANT,
        device_id=uuid.uuid4(),
        key_id="tdk-abcdefghijklmnopqrst",
        key_rotated_at=dt.datetime(2026, 9, 28, tzinfo=dt.UTC),
    )
    cfg.save(config, path)
    assert cfg.load(path) == config
    text = path.read_text()
    assert "secret" not in text.split("\n", 1)[1]
    with pytest.raises(cfg.ConfigError, match="enrol"):
        cfg.load(tmp_path / "missing.toml")
    path.write_text('server_url = "https://x.example"\n')
    with pytest.raises(cfg.ConfigError):
        cfg.load(path)
    path.write_text('server_url = "https://x.example"\ntenant_id = "x"\n')
    with pytest.raises(cfg.ConfigError):
        cfg.load(path)


def test_ADR_0032_the_file_store_is_owner_only_and_never_chosen_silently(tmp_path: Path) -> None:
    if sys.platform != "win32":
        with pytest.raises(cred.CredentialError, match="DPAPI"):
            cred.default_store(tmp_path, TENANT)
    store = cred.default_store(tmp_path, TENANT, allow_insecure=True)
    with pytest.raises(cred.CredentialError, match="enrol"):
        store.load()
    store.save(bytes(range(32)))
    assert store.load() == bytes(range(32))
    if sys.platform != "win32":
        mode = stat.S_IMODE(os.stat(tmp_path / cred.KEY_FILE).st_mode)
        assert mode == 0o600
    store.clear()
    assert not (tmp_path / cred.KEY_FILE).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI (CI runs this on Windows)")
def test_ADR_0032_dpapi_protects_the_key_for_this_account_and_school(tmp_path: Path) -> None:
    store = cred.DpapiStore(tmp_path, TENANT)
    store.save(bytes(range(32)))
    raw = (tmp_path / cred.KEY_FILE).read_bytes()
    assert bytes(range(32)) not in raw
    assert store.load() == bytes(range(32))
    other_school = cred.DpapiStore(tmp_path, uuid.uuid4())
    with pytest.raises(cred.CredentialError):
        other_school.load()


def test_ADR_0032_dpapi_is_windows_only(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("covered by the DPAPI test on Windows")
    with pytest.raises(cred.CredentialError, match="Windows"):
        cred.DpapiStore(tmp_path, TENANT)
