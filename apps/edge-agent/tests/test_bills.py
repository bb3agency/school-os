"""Pending bills (bill-wise / term-wise dues) read from Tally and sent with the snapshot, and the
agent's User-Agent (lead decision D63; ADR-0032 §1; FR-TALLY-003, FR-TALLY-005). Synthetic
fixtures only."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest

from sos_edge_agent import client, sync
from sos_edge_agent import tally_xml as tx
from sos_edge_agent.client import HttpResponse

from .conftest import FakeServer, FakeTally, fixture
from .test_sync_and_cli import _agent, _sent

TODAY = dt.date(2026, 9, 28)


def test_FR_TALLY_003_D63_the_bills_request_is_an_export_of_the_bills_collection() -> None:
    body = tx.bills_request("Synthetic Model School 2026-27", TODAY)
    tx.assert_export_only(body)
    assert b"<TALLYREQUEST>Export</TALLYREQUEST>" in body
    assert b"<TYPE>Bills</TYPE>" in body
    assert b'<SVTODATE TYPE="Date">28-Sep-2026</SVTODATE>' in body
    assert b"Import" not in body


def test_D63_bills_are_parsed_with_due_dates_and_settled_ones_left_out() -> None:
    bills = {b.reference: b for b in tx.parse_bills(fixture("bills.xml"))}
    assert set(bills) == {
        "Term 2/2026-27",
        "Term 1/2026-27",
        "Bus Fee Q1",
        "On Account",
        "INV-0042",
    }  # "Admission 2026" is settled (nothing pending)
    term1 = bills["Term 1/2026-27"]
    assert (term1.ledger, term1.bill_date, term1.due_date, term1.pending) == (
        "Synthetica Venkata Sai 9A",
        dt.date(2026, 6, 1),
        dt.date(2026, 7, 1),  # 30 days of credit
        Decimal("5000.00"),
    )
    bus = bills["Bus Fee Q1"]
    assert (bus.due_date, bus.pending) == (dt.date(2026, 4, 30), Decimal("4500.25"))
    # No credit period: no due date (never guessed); a credit balance is an advance.
    advance = bills["On Account"]
    assert (advance.due_date, advance.pending) == (None, Decimal("-2000.00"))


@pytest.mark.parametrize(
    ("bill_date", "period", "expected"),
    [
        (dt.date(2026, 6, 1), "30 Days", dt.date(2026, 7, 1)),
        (dt.date(2026, 6, 1), "0 days", dt.date(2026, 6, 1)),
        (None, "30 Days", None),
        (dt.date(2026, 6, 1), "15-Jun-2026", dt.date(2026, 6, 15)),
        (dt.date(2026, 6, 1), "20260615", dt.date(2026, 6, 15)),
        (dt.date(2026, 6, 1), "31-Feb-2026", None),
        (dt.date(2026, 6, 1), "soon", None),
        (dt.date(2026, 6, 1), None, None),
    ],
)
def test_D63_due_date(bill_date: dt.date | None, period: str | None, expected: Any) -> None:
    assert tx.due_date(bill_date, period) == expected


def test_D63_an_error_answer_for_bills_is_refused_by_the_parser() -> None:
    with pytest.raises(tx.TallyXmlError):
        tx.parse_bills(fixture("line_error.xml"))


def test_FR_TALLY_005_D63_bills_go_with_their_parties_only(
    server: FakeServer, tally: FakeTally
) -> None:
    report = _agent(server, tally).sync_once()
    assert report.bills == 4
    snapshot = _sent(server, "/syncs")[0]
    parties = {p["name"]: p for p in snapshot["parties"]}
    assert [b["reference"] for b in parties["Synthetica Venkata Sai 9A"]["bills"]] == [
        "Term 1/2026-27",
        "Term 2/2026-27",
    ]  # earliest due first
    assert parties["Synthetica Venkata Sai 9A"]["bills"][0] == {
        "reference": "Term 1/2026-27",
        "bill_date": "2026-06-01",
        "due_date": "2026-07-01",
        "pending": "5000.00",
    }
    assert parties["Synthetica Lakshmi Devi 9B"]["bills"][0]["pending"] == "4500.25"
    # A ledger without bill-wise details sends no bills.
    assert "bills" not in parties["Synthetica No Guid Ledger"]
    # A supplier's bill (a ledger outside the selected groups) never leaves the PC.
    assert "INV-0042" not in json.dumps(snapshot)
    assert sum(1 for b in tally.bodies if b"<TYPE>Bills</TYPE>" in b) == 1


def test_D63_balances_are_still_sent_when_tally_cannot_answer_for_bills(
    server: FakeServer,
) -> None:
    tally = FakeTally(bills=fixture("line_error.xml"))
    report = _agent(server, tally).sync_once()
    assert (report.outcome, report.parties, report.bills) == ("synced", 4, 0)
    snapshot = _sent(server, "/syncs")[0]
    assert all("bills" not in p for p in snapshot["parties"])


def test_D63_bills_of_the_last_parties_are_left_out_to_keep_under_the_body_limit() -> None:
    bill = {"reference": "T" * 150, "bill_date": None, "due_date": None, "pending": "1.00"}
    parties = [
        {"guid": None, "name": f"P{i}", "group": "G", "closing_balance": "1.00", "bills": [bill]}
        for i in range(10)
    ]
    snapshot: dict[str, Any] = {"company": "C", "as_of": "2026-09-28", "groups": ["G"]}
    snapshot["parties"] = parties
    bare = sync._size({**snapshot, "parties": [{**p, "bills": []} for p in parties]})
    limit = bare + sync.BODY_MARGIN_BYTES + 3 * (sync._size([bill]) + 10)
    dropped = sync.fit_bills(snapshot, limit)
    assert dropped == 7
    assert [("bills" in p) for p in parties] == [True] * 3 + [False] * 7
    assert sync._size(snapshot) <= limit
    # Under the limit nothing changes.
    assert sync.fit_bills(snapshot, limit) == 0


class _Recorder:
    def __init__(self) -> None:
        self.headers: list[Mapping[str, str]] = []

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> HttpResponse:
        self.headers.append(headers)
        return HttpResponse(200, {}, b"<ENVELOPE/>" if "9000" in url else b"{}")


def test_D63_every_request_names_the_agent_in_its_user_agent(server: FakeServer) -> None:
    assert client.USER_AGENT.startswith("SchoolOS-Edge-Agent/")
    recorder = _Recorder()
    agent = _agent(server, FakeTally())
    agent.server._transport = recorder
    agent.server.get_config()
    tally = client.TallyClient("http://127.0.0.1:9000", recorder)
    tally.export(tx.companies_request())
    assert [h["User-Agent"] for h in recorder.headers] == [client.USER_AGENT] * 2
