"""The real edge agent (``apps/edge-agent``) against the real API (ADR-0032; FR-TALLY-001..005).

The agent's own client signs and sends; the API's guard verifies; the service applies. Tally is
a fake that answers from the agent's synthetic XML fixtures. Proves the two sides agree on the
signing string, the payloads and the idempotent batch ids, not just each side on its own.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from defusedxml import ElementTree as SafeET  # type: ignore[import-untyped]
from sqlalchemy import Engine, text

from app.tally import agent_auth
from sos_edge_agent import client as agent_client
from sos_edge_agent import sync as agent_sync
from sos_edge_agent.config import AgentConfig

from .conftest import T

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).resolve().parents[3] / "edge-agent" / "tests" / "fixtures"
SERVER = "https://school.synthetic.test"
COMPANY = "Synthetic Model School 2026-27"


class ApiTransport:
    """The agent's transport, delivering to the FastAPI TestClient."""

    def __init__(self, api: Any) -> None:
        self.api = api

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> agent_client.HttpResponse:
        path = url.removeprefix(SERVER)
        res = self.api.client.request(method, path, content=body or b"", headers=dict(headers))
        return agent_client.HttpResponse(res.status_code, dict(res.headers), res.content)


class FixtureTally:
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> agent_client.HttpResponse:
        root = SafeET.fromstring(body or b"<x/>")
        kind = root.findtext(".//COLLECTION/TYPE")
        if kind == "Company":
            name = "companies.xml"
        elif kind == "Group":
            name = "groups.xml"
        else:
            group = (root.findtext(".//COLLECTION/CHILDOF") or "").lower().replace(" ", "_")
            name = f"ledgers_{group}.xml"
        data = (FIXTURES / name).read_bytes() if (FIXTURES / name).is_file() else b"<E/>"
        return agent_client.HttpResponse(200, {}, data)


def test_FR_TALLY_005_the_agent_enrols_and_syncs_through_the_real_api(
    api: Any, admin_engine: Engine
) -> None:
    agent_auth.get_agent_stores.cache_clear()
    school = T.fresh_school(admin_engine)
    code = T.new_code(api, school)["code"]
    config = AgentConfig(server_url=SERVER, tenant_id=school.tenant_id)
    transport = ApiTransport(api)
    out = agent_client.SchoolOSClient(config, lambda: b"", transport).enrol(code, "windows-10")
    secret = T.b64(out["secret"])
    enrolled = config.replace(
        device_id=uuid.UUID(out["device_id"]),
        key_id=out["key_id"],
        key_rotated_at=dt.datetime.now(dt.UTC),
    )
    server = agent_client.SchoolOSClient(enrolled, lambda: secret, transport)
    agent = agent_sync.Agent(
        server,
        agent_client.TallyClient("http://127.0.0.1:9000", FixtureTally()),
        today=lambda: dt.date(2026, 9, 28),
    )
    # Before the accountant chooses groups the agent only reports the catalog.
    assert agent.sync_once().outcome == "no_groups_selected"
    T.select(api, school, ["Sundry Debtors", "Class IX Fees"], company=COMPANY)
    agent_auth.get_agent_stores.cache_clear()  # a new rate-limit window
    report = agent.sync_once()
    assert (report.outcome, report.parties, report.repeat) == ("synced", 4, False)
    agent_auth.get_agent_stores.cache_clear()
    assert agent.sync_once().repeat is True  # same snapshot, same batch: applied once
    with admin_engine.connect() as c:
        rows = {
            r.ledger_name: (str(r.closing_balance), r.group_name)
            for r in c.execute(
                text(
                    "SELECT ledger_name, closing_balance, group_name FROM ops.tally_parties "
                    "WHERE tenant_id = :t"
                ),
                {"t": school.tenant_id},
            )
        }
        syncs: int = c.execute(
            text("SELECT count(*) FROM ops.tally_syncs WHERE tenant_id = :t"),
            {"t": school.tenant_id},
        ).scalar_one()
    assert rows["Synthetica Venkata Sai 9A"] == ("15000.00", "Sundry Debtors")
    assert rows["Synthetica Advance Paid"] == ("-2000.00", "Sundry Debtors")
    assert rows["Synthetica Lakshmi Devi 9B"] == ("4500.25", "Class IX Fees")
    assert syncs == 1
    # Key rotation end to end: the new key works and retires the old one.
    agent_auth.get_agent_stores.cache_clear()
    keys: dict[str, bytes] = {}

    def rotated(new: AgentConfig, new_secret: bytes) -> None:
        keys["secret"] = new_secret

    agent = agent_sync.Agent(
        agent_client.SchoolOSClient(enrolled, lambda: keys.get("secret", secret), transport),
        agent_client.TallyClient("http://127.0.0.1:9000", FixtureTally()),
        on_key_rotated=rotated,
    )
    assert agent.rotate() is True
    agent_auth.get_agent_stores.cache_clear()
    assert agent.server.get_config()["company"] == COMPANY
    agent_auth.get_agent_stores.cache_clear()
    old = agent_client.SchoolOSClient(enrolled, lambda: secret, transport)
    with pytest.raises(agent_client.CredentialRefused):
        old.get_config()
