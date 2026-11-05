"""Enrolment of the installed service (D69): the MSI runs the agent as its virtual account
``NT SERVICE\\SchoolOSTallyAgent``, which nobody can sign in as, so an administrator leaves an
enrolment request in the state folder and the service enrols itself (the device key is then
protected with the service account's DPAPI key). Synthetic data only."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from sos_edge_agent import cli
from sos_edge_agent.config import load
from sos_edge_agent.credentials import InsecureFileStore

from .conftest import DEVICE, SECRET, TENANT, FakeServer, FakeTally

SERVER = "https://school.synthetic.test"
CODE = "ABCD-EFGH-JKLM"


def _runtime(tmp_path: Path, server: FakeServer, tally: FakeTally) -> cli.Runtime:
    return cli.Runtime(
        tmp_path, transport=server, tally_transport=tally, store=InsecureFileStore(tmp_path)
    )


def _request(rt: cli.Runtime, code: str = CODE) -> int:
    argv = ["enrol", "--via-service", "--server", SERVER, "--school", str(TENANT), "--code", code]
    return cli.main(argv, runtime=rt)


def test_D69_enrol_via_service_leaves_a_request_and_does_not_enrol(
    tmp_path: Path, server: FakeServer, tally: FakeTally
) -> None:
    rt = _runtime(tmp_path, server, tally)
    assert _request(rt) == cli.EXIT_OK
    assert (tmp_path / cli.ENROL_REQUEST).is_file()
    assert not (tmp_path / "agent.toml").exists()
    assert not (tmp_path / "device.key").exists()
    assert server.calls == []  # the administrator's session never talks to SchoolOS


def test_D69_enrol_via_service_checks_the_addresses_first(tmp_path: Path) -> None:
    rt = cli.Runtime(tmp_path, store=InsecureFileStore(tmp_path))
    argv = ["enrol", "--via-service", "--server", "http://school.example.in"]
    argv += ["--school", str(uuid.uuid4()), "--code", CODE]
    assert cli.main(argv, runtime=rt) == cli.EXIT_FAILED
    assert not (tmp_path / cli.ENROL_REQUEST).exists()


def test_D69_the_service_enrols_itself_from_the_request_and_deletes_it(
    tmp_path: Path, server: FakeServer, tally: FakeTally
) -> None:
    rt = _runtime(tmp_path, server, tally)
    _request(rt)
    assert cli.wait_for_enrolment(rt, threading.Event(), poll_seconds=0) is True
    assert not (tmp_path / cli.ENROL_REQUEST).exists()
    assert InsecureFileStore(tmp_path).load() == SECRET
    assert load(tmp_path / "agent.toml").device_id == DEVICE
    logged = (tmp_path / "agent.log").read_text()
    assert CODE not in logged


def test_D69_a_rejected_code_is_deleted_and_the_service_keeps_waiting(
    tmp_path: Path, server: FakeServer, tally: FakeTally
) -> None:
    rt = _runtime(tmp_path, server, tally)
    _request(rt, code="WRONG-CODE-XX")
    stop = threading.Event()
    waited: list[float] = []

    def _wait(timeout: float | None = None) -> bool:
        waited.append(timeout or 0)
        stop.set()  # stop after the first poll
        return True

    stop.wait = _wait  # type: ignore[method-assign]
    assert cli.wait_for_enrolment(rt, stop, poll_seconds=7) is False
    assert waited == [7]
    assert not (tmp_path / cli.ENROL_REQUEST).exists()  # single-use code: never retried
    assert not (tmp_path / "agent.toml").exists()
    assert "WRONG-CODE-XX" not in (tmp_path / "agent.log").read_text()


def test_D69_an_enrolled_service_does_not_wait(
    tmp_path: Path, server: FakeServer, tally: FakeTally
) -> None:
    rt = _runtime(tmp_path, server, tally)
    argv = ["enrol", "--server", SERVER, "--school", str(TENANT), "--code", CODE]
    assert cli.main(argv, runtime=rt) == cli.EXIT_OK
    stop = threading.Event()
    stop.set()
    assert cli.wait_for_enrolment(rt, threading.Event(), poll_seconds=0) is True
    assert cli.wait_for_enrolment(rt, stop, poll_seconds=0) is False  # stopping wins
