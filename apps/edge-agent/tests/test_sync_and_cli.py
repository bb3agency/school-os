"""One sync, the service loop, key rotation and the command line against fake Tally and SchoolOS
(ADR-0032 §3-§4; FR-TALLY-002..005). Synthetic data only."""

from __future__ import annotations

import datetime as dt
import email.utils
import http.server
import io
import logging
import socket
import threading
import unicodedata
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from sos_edge_agent import cli, client, sync
from sos_edge_agent.config import AgentConfig, load, save
from sos_edge_agent.credentials import InsecureFileStore
from sos_edge_agent.tally_xml import companies_request

from .conftest import DEVICE, NEW_SECRET, SECRET, TENANT, FakeServer, FakeTally, fixture

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


def test_FR_TALLY_005_a_snapshot_back_to_earlier_figures_is_applied_again(
    server: FakeServer, tally: FakeTally, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A payment entered and then deleted the same day: A, B, then A again. The third snapshot
    # is not a retry of the first; answered "repeat", SchoolOS would keep showing B all day.
    def party(balance: str) -> dict[str, Any]:
        return {"guid": "g-1", "name": "Synthetic P", "group": "Sundry Debtors",
                "closing_balance": balance}  # fmt: skip

    snapshots = iter([[party("100.00")], [party("0.00")], [party("100.00")], [party("100.00")]])
    monkeypatch.setattr(sync, "collect", lambda *args: next(snapshots))
    agent = _agent(server, tally)
    reports = [agent.sync_once() for _ in range(4)]
    assert [r.repeat for r in reports] == [False, False, False, True]
    batches = [p["batch_id"] for p in _sent(server, "/syncs")]
    assert len(set(batches[:3])) == 3
    assert batches[3] == batches[2]
    assert len(server.syncs) == 3


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


def test_FR_TALLY_005_a_restarted_agent_applies_figures_back_to_an_earlier_snapshot(
    server: FakeServer, tally: FakeTally, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A, B, then the service restarts (PC reboot) and Tally is back at A the same day. The
    # restarted agent has no memory of B; A must still be a new batch, not a repeat of the first
    # A (answered "repeat", SchoolOS would keep showing B).
    def party(balance: str) -> dict[str, Any]:
        return {"guid": "g-1", "name": "Synthetic P", "group": "Sundry Debtors",
                "closing_balance": balance}  # fmt: skip

    snapshots = iter([[party("100.00")], [party("0.00")], [party("100.00")]])
    monkeypatch.setattr(sync, "collect", lambda *args: next(snapshots))
    first = _agent(server, tally)
    assert [first.sync_once().repeat for _ in range(2)] == [False, False]
    restarted = _agent(server, tally)
    assert restarted.sync_once().repeat is False
    assert len(server.syncs) == 3


@pytest.mark.parametrize(("offset_s", "code"), [(0, "credential_refused"), (900, "clock_skew")])
def test_FR_TALLY_002_a_401_from_a_pc_clock_off_says_clock_skew(offset_s: int, code: str) -> None:
    # The server answers a timestamp outside ±300 s with the same 401 as a revoked device; the
    # office PC's clock (a flat CMOS battery is common) must be named in the log, not "refused".
    now = 1_790_000_000.0
    date = email.utils.formatdate(now + offset_s, usegmt=True)

    class _Refusing:
        def request(
            self, method: str, url: str, headers: Any, body: bytes | None
        ) -> client.HttpResponse:
            return client.HttpResponse(401, {"Date": date}, b"{}")

    config = AgentConfig(
        server_url=SERVER, tenant_id=TENANT, device_id=DEVICE, key_id="tdk-abcdefghijklmnopqrst"
    )
    api = client.SchoolOSClient(config, lambda: SECRET, _Refusing(), clock=lambda: now)
    with pytest.raises(client.CredentialRefused) as caught:
        api.get_config()
    assert caught.value.code == code


class _LocalTally(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = fixture("companies.xml")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return None


def test_FR_TALLY_003_tally_is_never_read_through_a_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ledger names and balances go only to Tally on this PC: an office proxy (HTTP_PROXY or the
    # Windows setting) must never receive them, even when 127.0.0.1 is not in its bypass list.
    with socket.socket() as probe:  # a port nothing listens on plays the proxy
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    for name in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{dead}")
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _LocalTally)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        rt = cli.Runtime(tmp_path, store=InsecureFileStore(tmp_path))
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        answer = client.TallyClient(url, rt.tally_transport).export(companies_request())
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert b"Synthetic Model School 2026-27" in answer


# --- audit 2026-11-03: catalog cap, body size, crash-safe keys, NFC names ------------------------

_API_TALLY = Path(__file__).resolve().parents[2] / "api" / "app" / "tally"


def _envelope(rows: str) -> bytes:
    return (
        f"<ENVELOPE><BODY><DATA><COLLECTION>{rows}</COLLECTION></DATA></BODY></ENVELOPE>".encode()
    )


class _ManyGroupsTally(FakeTally):
    """Tally with more groups than SchoolOS accepts, each under a very long parent name."""

    def __init__(self, count: int) -> None:
        super().__init__()
        parent = "P" * 300
        self.groups_xml = _envelope(
            "".join(
                f'<GROUP NAME="Group {n:04d}"><PARENT>{parent}</PARENT></GROUP>'
                for n in reversed(range(count))  # Tally's order is not sorted
            )
        )

    def request(
        self, method: str, url: str, headers: Any, body: bytes | None
    ) -> client.HttpResponse:
        if body is not None and b"<TYPE>Group</TYPE>" in body:
            self.bodies.append(body)
            return client.HttpResponse(200, {}, self.groups_xml)
        return super().request(method, url, headers, body)


def test_FR_TALLY_004_the_catalog_keeps_to_the_servers_group_cap(
    server: FakeServer, caplog: pytest.LogCaptureFixture
) -> None:
    # 2,000 groups were sent while the server takes 500 (422 too_many, and then no sync ever ran).
    tally = _ManyGroupsTally(620)
    with caplog.at_level(logging.INFO, logger="sos_edge_agent"):
        report = _agent(server, tally).sync_once()
    catalog = _sent(server, "/catalog")[0]
    names = [g["name"] for g in catalog["groups"]]
    assert len(names) == sync.MAX_CATALOG_GROUPS == 500
    assert names == [f"Group {n:04d}" for n in range(500)]  # the first 500, sorted
    assert all(len(g["parent"]) == 200 for g in catalog["groups"])
    assert report.outcome == "synced"
    warned = [r for r in caplog.records if getattr(r, "code", None) == "too_many_groups"]
    assert len(warned) == 1
    assert getattr(warned[0], "count", None) == 620
    assert "Group 0" not in caplog.text


def test_FR_TALLY_004_agent_limits_match_the_server_rules() -> None:
    # The agent cannot ask the server for these numbers, so they are pinned to the server's files.
    if not _API_TALLY.is_dir():
        pytest.skip("the API sources are not next to the agent")
    rules = (_API_TALLY / "config.yaml").read_text(encoding="utf-8")
    auth = (_API_TALLY / "agent_auth.py").read_text(encoding="utf-8")
    assert f"  max_groups: {sync.MAX_CATALOG_GROUPS}\n" in rules
    assert f"  skew_seconds: {int(client.SKEW_LIMIT_S)} " in rules
    assert "MAX_BODY_BYTES: Final = 1024 * 1024\n" in auth
    assert client.MAX_REQUEST_BYTES == 1024 * 1024


def test_FR_TALLY_005_bodies_are_compact_utf8_not_ascii_escapes() -> None:
    # Escaped, a Telugu letter costs 6 bytes ("\\u0c2a"); raw UTF-8 costs 3.
    calls: list[bytes] = []

    class _Recording(FakeServer):
        def request(
            self, method: str, url: str, headers: Any, body: bytes | None
        ) -> client.HttpResponse:
            calls.append(body or b"")
            return super().request(method, url, headers, body)

    config = AgentConfig(
        server_url=SERVER, tenant_id=TENANT, device_id=DEVICE, key_id="tdk-abcdefghijklmnopqrst"
    )
    api = client.SchoolOSClient(config, lambda: SECRET, _Recording())
    api.put_catalog({"company": "పాఠశాల", "groups": [{"name": "ఫీజులు", "parent": None}]})
    assert "పాఠశాల".encode() in calls[0]
    assert b"\\u" not in calls[0]
    assert b", " not in calls[0]
    assert b": " not in calls[0]


def test_FR_TALLY_005_a_snapshot_over_the_server_limit_fails_the_run_without_sending(
    server: FakeServer,
    tally: FakeTally,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Over 1 MiB the server refuses the body; retried every cycle, the agent never synced again.
    big = [
        {
            "guid": None,
            "name": f"Synthetic {n:05d} " + "క" * 60,
            "group": "Sundry Debtors",
            "closing_balance": "1.00",
        }
        for n in range(5000)
    ]
    monkeypatch.setattr(sync, "collect", lambda *args: big)
    agent = _agent(server, tally)
    with (
        caplog.at_level(logging.INFO, logger="sos_edge_agent"),
        pytest.raises(client.PayloadTooLarge) as caught,
    ):
        agent.sync_once()
    assert caught.value.code == "snapshot_too_large"
    assert not _sent(server, "/syncs")  # never sent
    assert agent.next_wait(1, caught.value) == sync.REFUSED_WAIT_S
    logged = [r for r in caplog.records if getattr(r, "code", None) == "snapshot_too_large"]
    assert logged
    assert int(getattr(logged[0], "count", 0)) > client.MAX_REQUEST_BYTES
    assert "Synthetic" not in caplog.text
    # The server's own 413 is the same refusal, not an endless retry.
    with pytest.raises(client.PayloadTooLarge):
        client._raise_for(client.HttpResponse(413, {}, b"{}"))


def _runtime(tmp_path: Path, server: FakeServer, tally: FakeTally) -> cli.Runtime:
    return cli.Runtime(
        tmp_path, transport=server, tally_transport=tally, store=InsecureFileStore(tmp_path)
    )


_ENROL = ["enrol", "--server", SERVER, "--school", str(TENANT), "--code", "ABCD-EFGH-JKLM"]


def _crash(*args: Any) -> None:
    raise OSError("power cut")


def test_FR_TALLY_002_a_crash_between_the_key_and_config_writes_does_not_lock_the_pc_out(
    tmp_path: Path, server: FakeServer, tally: FakeTally, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert cli.main(_ENROL, runtime=_runtime(tmp_path, server, tally)) == cli.EXIT_OK
    monkeypatch.setattr(cli, "save", _crash)
    with pytest.raises(OSError, match="power cut"):
        cli.main(["rotate-key"], runtime=_runtime(tmp_path, server, tally))
    monkeypatch.setattr(cli, "save", save)
    assert load(tmp_path / "agent.toml").key_id == "tdk-abcdefghijklmnopqrst"  # stale config
    del server.keys["tdk-abcdefghijklmnopqrst"]  # the new key's first use retires the old one
    assert cli.main(["sync-once"], runtime=_runtime(tmp_path, server, tally)) == cli.EXIT_OK
    assert load(tmp_path / "agent.toml").key_id == "tdk-zyxwvutsrqponmlkjihg"  # repaired


def test_FR_TALLY_001_a_crash_during_re_enrolment_does_not_lock_the_pc_out(
    tmp_path: Path, server: FakeServer, tally: FakeTally, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An enrolled PC is enrolled again (new device); the config write fails after the key write.
    old_device = uuid.UUID("01920000-0000-7000-8000-00000000d0ff")
    old = AgentConfig(
        server_url=SERVER, tenant_id=TENANT, device_id=old_device, key_id="tdk-oldoldoldoldoldoldol"
    )
    save(old, tmp_path / "agent.toml")
    InsecureFileStore(tmp_path).save(NEW_SECRET)
    monkeypatch.setattr(cli, "save", _crash)
    with pytest.raises(OSError, match="power cut"):
        cli.main(_ENROL, runtime=_runtime(tmp_path, server, tally))
    monkeypatch.setattr(cli, "save", save)
    assert cli.main(["sync-once"], runtime=_runtime(tmp_path, server, tally)) == cli.EXIT_OK
    repaired = load(tmp_path / "agent.toml")
    assert (repaired.device_id, repaired.key_id) == (DEVICE, "tdk-abcdefghijklmnopqrst")


_DECOMPOSED = "పై"  # Telugu "pai" typed as two vowel marks
_COMPOSED = "పై"  # the same text after NFC (how SchoolOS stores it)


class _TeluguTally(FakeTally):
    """Tally keeps names as typed (not NFC)."""

    def request(
        self, method: str, url: str, headers: Any, body: bytes | None
    ) -> client.HttpResponse:
        assert body is not None
        self.bodies.append(body)
        if b"<TYPE>Company</TYPE>" in body:
            rows = f'<COMPANY NAME="School {_DECOMPOSED}"/>'
        elif b"<TYPE>Group</TYPE>" in body:
            rows = f'<GROUP NAME="Fees {_DECOMPOSED}"><PARENT>Sundry Debtors</PARENT></GROUP>'
        else:
            rows = (
                f'<LEDGER NAME="Synthetic Party"><GUID>g-1</GUID><PARENT>Fees {_DECOMPOSED}'
                "</PARENT><CLOSINGBALANCE>-100.00</CLOSINGBALANCE></LEDGER>"
            )
        return client.HttpResponse(200, {}, _envelope(rows))


def test_FR_TALLY_004_tally_names_match_the_servers_nfc_names() -> None:
    assert unicodedata.normalize("NFC", _DECOMPOSED) == _COMPOSED
    server = FakeServer(groups=[f"Fees {_COMPOSED}"], company=f"School {_COMPOSED}")
    tally = _TeluguTally()
    report = _agent(server, tally).sync_once()
    assert (report.outcome, report.parties) == ("synced", 1)
    snapshot = _sent(server, "/syncs")[0]
    assert snapshot["company"] == f"School {_COMPOSED}"
    assert snapshot["parties"][0]["group"] == f"Fees {_COMPOSED}"
    # Tally is asked with its own spelling, the one it knows.
    asked = [b for b in tally.bodies if b"<TYPE>Ledger</TYPE>" in b]
    assert len(asked) == 1
    assert f"Fees {_DECOMPOSED}".encode() in asked[0]
    assert f"School {_DECOMPOSED}".encode() in asked[0]
