"""One sync, the service loop, key rotation and the command line against fake Tally and SchoolOS
(ADR-0032 §3-§4; FR-TALLY-002..005). Synthetic data only."""

from __future__ import annotations

import datetime as dt
import io
import logging
import threading
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from sos_edge_agent import cli, client, sync
from sos_edge_agent.config import AgentConfig, load, save
from sos_edge_agent.credentials import InsecureFileStore

from .conftest import DEVICE, NEW_SECRET, SECRET, TENANT, FakeServer, FakeTally

SERVER = "https://school.synthetic.test"
TODAY = dt.date(2026, 9, 28)


def _agent(server: FakeServer, tally: FakeTally) -> sync.Agent:
    config = AgentConfig(
        server_url=SERVER,
        tenant_id=TENANT,
        device_id=DEVICE,
        key_id="tdk-abcdefghijklmnopqrst",
        key_rotated_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )
    secrets = {"current": SECRET}
    api = client.SchoolOSClient(config, lambda: secrets["current"], server)

    def rotated(new: AgentConfig, secret: bytes) -> None:
        secrets["current"] = secret

    return sync.Agent(
        api,
        client.TallyClient("http://127.0.0.1:9000", tally),
        today=lambda: TODAY,
        on_key_rotated=rotated,
        now=lambda: dt.datetime(2026, 9, 29, tzinfo=dt.UTC),
    )


def _sent(server: FakeServer, route: str) -> list[dict[str, Any]]:
    return [p for m, path, p in server.calls if path.endswith(route) and p is not None]


def test_FR_TALLY_004_only_selected_groups_are_read_and_sent(
    server: FakeServer, tally: FakeTally, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="sos_edge_agent"):
        report = _agent(server, tally).sync_once()
    assert (report.outcome, report.groups, report.parties, report.repeat) == ("synced", 2, 4, False)
    catalog = _sent(server, "/catalog")[0]
    assert catalog["company"] == "Synthetic Model School 2026-27"
    snapshot = _sent(server, "/syncs")[0]
    parties = {p["name"]: p for p in snapshot["parties"]}
    assert parties["Synthetica Venkata Sai 9A"]["closing_balance"] == "15000.00"
    assert parties["Synthetica Advance Paid"]["closing_balance"] == "-2000.00"
    # A ledger of a selected sub-group is reported under its own (selected) group, once.
    assert parties["Synthetica Lakshmi Devi 9B"]["group"] == "Class IX Fees"
    assert parties["Synthetica No Guid Ledger"]["guid"] is None
    assert snapshot["as_of"] == "2026-09-28"
    assert set(snapshot["groups"]) == {"Sundry Debtors", "Class IX Fees"}
    # Tally was asked only for the selected groups' ledgers, and only with exports.
    asked = [b for b in tally.bodies if b"<TYPE>Ledger</TYPE>" in b]
    assert len(asked) == 2
    assert all(b"<TALLYREQUEST>Export</TALLYREQUEST>" in b for b in tally.bodies)
    assert all(b"Staff Advances" not in b for b in asked)
    # Invariant 5 on the PC too: counts and codes only in the logs.
    assert "Synthetica" not in caplog.text
    assert "15000" not in caplog.text


def test_FR_TALLY_005_a_retry_of_the_same_snapshot_is_the_same_batch(
    server: FakeServer, tally: FakeTally
) -> None:
    agent = _agent(server, tally)
    agent.sync_once()
    again = agent.sync_once()
    assert again.repeat is True
    batches = [p["batch_id"] for p in _sent(server, "/syncs")]
    assert batches[0] == batches[1]
    assert len(server.syncs) == 1
    tomorrow = _agent(server, tally)
    tomorrow._today = lambda: TODAY + dt.timedelta(days=1)
    assert tomorrow.sync_once().repeat is False
    assert len(server.syncs) == 2


def test_FR_TALLY_004_nothing_is_sent_until_groups_are_selected(tally: FakeTally) -> None:
    server = FakeServer(groups=[], company=None)
    report = _agent(server, tally).sync_once()
    assert report.outcome == "no_groups_selected"
    assert _sent(server, "/catalog")
    assert not _sent(server, "/syncs")
    assert not [b for b in tally.bodies if b"<TYPE>Ledger</TYPE>" in b]


def test_FR_TALLY_002_an_outdated_agent_stops_syncing(tally: FakeTally) -> None:
    server = FakeServer(min_version="9.0.0")
    with pytest.raises(client.Outdated):
        _agent(server, tally).sync_once()
    assert not _sent(server, "/syncs")


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, client.CredentialRefused),
        (404, client.ConnectorOff),
        (429, client.RateLimited),
        (503, client.TransientError),
    ],
)
def test_FR_TALLY_002_server_answers_map_to_actions(
    server: FakeServer, tally: FakeTally, status: int, error: type[Exception]
) -> None:
    server.status_override["/config"] = status
    agent = _agent(server, tally)
    with pytest.raises(error) as caught:
        agent.sync_once()
    wait = agent.next_wait(1, caught.value)  # type: ignore[arg-type]
    if status in (401, 404):
        assert wait == sync.REFUSED_WAIT_S
    elif status == 429:
        assert wait == 120.0
    else:
        assert sync.BACKOFF_MIN_S / 2 <= wait <= sync.BACKOFF_MIN_S


def test_FR_TALLY_005_backoff_doubles_to_thirty_minutes_and_the_loop_survives(
    server: FakeServer,
) -> None:
    agent = _agent(server, FakeTally(down=True))
    waits = [agent.next_wait(n, client.TransientError("x")) for n in range(1, 9)]
    assert waits[0] <= 60.0
    assert max(waits) <= sync.BACKOFF_MAX_S
    assert waits[-1] >= sync.BACKOFF_MAX_S / 2
    stop = threading.Event()
    agent.run(stop, max_cycles=1)  # Tally down: logged and retried, never raised
    assert not _sent(server, "/syncs")


def test_FR_TALLY_002_key_rotation_switches_to_the_new_key(
    server: FakeServer, tally: FakeTally
) -> None:
    agent = _agent(server, tally)
    agent.rotate_after_days = 7
    assert agent.rotate_if_due() is True  # rotated 28 days ago
    assert agent.server.config.key_id == "tdk-zyxwvutsrqponmlkjihg"
    del server.keys["tdk-abcdefghijklmnopqrst"]  # the server retired the old key
    assert agent.sync_once().outcome == "synced"
    assert agent.rotate_if_due() is False


def test_ADR_0032_the_cli_enrols_syncs_and_never_prints_the_key(
    tmp_path: Path, tally: FakeTally, server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    store = InsecureFileStore(tmp_path)
    rt = cli.Runtime(tmp_path, transport=server, tally_transport=tally, store=store)
    bad = cli.main(
        ["enrol", "--server", SERVER, "--school", str(TENANT), "--code", "WRONG-CODE-XX"],
        runtime=rt,
    )
    assert bad == cli.EXIT_FAILED
    code = cli.main(
        ["enrol", "--server", SERVER, "--school", str(TENANT), "--code", "ABCD-EFGH-JKLM"],
        runtime=rt,
    )
    assert code == cli.EXIT_OK
    assert store.load() == SECRET
    config = load(tmp_path / "agent.toml")
    assert (config.device_id, config.key_id) == (DEVICE, "tdk-abcdefghijklmnopqrst")
    assert cli.main(["sync-once"], runtime=rt) == cli.EXIT_OK
    assert cli.main(["status"], runtime=rt) == cli.EXIT_OK
    assert cli.main(["check-tally"], runtime=rt) == cli.EXIT_OK
    assert cli.main(["rotate-key"], runtime=rt) == cli.EXIT_OK
    assert store.load() == NEW_SECRET
    assert load(tmp_path / "agent.toml").key_id == "tdk-zyxwvutsrqponmlkjihg"
    out = capsys.readouterr()
    logged = (tmp_path / "agent.log").read_text()
    for text in (out.out, out.err, logged):
        assert "ABCD-EFGH-JKLM" not in text
        assert "Synthetica" not in text
    assert "4 ledgers in 2 groups" in out.out


def test_ADR_0032_the_cli_refuses_unsafe_configuration(tmp_path: Path) -> None:
    rt = cli.Runtime(tmp_path, store=InsecureFileStore(tmp_path))
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(
            [
                "enrol",
                "--server",
                "http://school.example.in",
                "--school",
                str(uuid.uuid4()),
                "--code",
                "ABCD-EFGH-JKLM",
            ],
            runtime=rt,
        )
    assert code == cli.EXIT_FAILED
    assert cli.main(["status"], runtime=rt) == cli.EXIT_FAILED  # not enrolled yet
    save(
        AgentConfig(server_url=SERVER, tenant_id=TENANT, tally_url="http://127.0.0.1:9000"),
        tmp_path / "agent.toml",
    )
    text = (tmp_path / "agent.toml").read_text().replace("127.0.0.1", "192.168.1.20")
    (tmp_path / "agent.toml").write_text(text)
    assert cli.main(["check-tally"], runtime=rt) == cli.EXIT_FAILED
