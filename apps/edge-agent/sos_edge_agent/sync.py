"""One sync, and the service loop with backoff (ADR-0032 §4).

:meth:`Agent.sync_once`:

1. ``GET /config``: the company and the ledger groups the accountant selected; stop if this
   agent is older than ``min_agent_version``.
2. Tally: the open companies, then the groups of the chosen company -> ``PUT /catalog`` (names
   only, at most the server's 500, the first by name), so the accountant can choose groups in
   SchoolOS. Names are compared with SchoolOS's after NFC; Tally is asked with its own spelling.
3. If groups are selected: for each selected group, the ledgers under it (sub-groups included)
   with their closing balance today. A ledger is reported under the selected group it was read
   from (its own parent when that is also selected), once.
   Then the company's pending bills (bill-wise allocations, D63; one export), attached to the
   parties they belong to (bills of other ledgers are dropped on this PC); a ledger without
   bill-wise details sends none. Bills are optional: when Tally cannot answer for them the
   balances are still sent, and when the body would pass the server's 1 MiB limit the bills of
   the last parties are left out (those ledgers fall back to their balance) rather than split
   the snapshot (a snapshot is applied whole, and the server accepts one sync per window).
4. ``POST /syncs`` with a ``batch_id`` derived from the snapshot's content and date: a retry of
   the same snapshot is recognised by the server and never applied twice; a newer snapshot has a
   new id, also when its figures go back to an earlier snapshot's (the id then also names the
   last batch the server accepted, so A, B, A is three batches, not a repeat of A; the first
   snapshot of each run names a random run id instead, so a restart between B and A cannot
   turn A into a repeat either).

:meth:`Agent.run` repeats that every ``sync_interval_minutes``; a failure waits with exponential
backoff and jitter (1 minute doubling to 30 minutes), a 429 waits for ``Retry-After``, and a
revoked credential or a switched-off connector waits an hour and tries again (the owner may
re-enable it). The device key is rotated after ``rotate_after_days``.

Nothing personal is logged or written: log lines carry counts and error codes only; the snapshot
lives in memory for one attempt.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import random
import threading
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sos_edge_agent import __version__
from sos_edge_agent.client import (
    MAX_REQUEST_BYTES,
    AgentError,
    ConnectorOff,
    CredentialRefused,
    Outdated,
    PayloadTooLarge,
    RateLimited,
    SchoolOSClient,
    TallyClient,
)
from sos_edge_agent.config import AgentConfig
from sos_edge_agent.tally_xml import (
    TallyBill,
    TallyLedger,
    TallyXmlError,
    bills_request,
    companies_request,
    groups_request,
    ledgers_request,
    parse_bills,
    parse_companies,
    parse_groups,
    parse_ledgers,
)

log = logging.getLogger("sos_edge_agent")

BATCH_NAMESPACE: Final = uuid.UUID("0192f0c1-7a51-7c4e-9d0e-5c0a6b0e7f21")
BACKOFF_MIN_S: Final = 60.0
BACKOFF_MAX_S: Final = 30 * 60.0
REFUSED_WAIT_S: Final = 60 * 60.0
MAX_CATALOG_GROUPS: Final = 500
"""The server's ``sync.max_groups`` (app/tally/config.yaml): more is refused with 422."""
MAX_NAME_CHARS: Final = 200
"""The server's ``TallyName`` length (app/tally/schemas.py)."""
MAX_BILLS_PER_PARTY: Final = 100
"""The server's ``PartyIn.bills`` length (app/tally/schemas.py): the earliest due are sent."""
BODY_MARGIN_BYTES: Final = 4096
"""Room left under the 1 MiB body limit for the batch id and JSON punctuation."""


def nfc(value: str) -> str:
    """SchoolOS stores Tally names NFC-normalised; Tally keeps them as typed."""
    return unicodedata.normalize("NFC", value)


@dataclass(frozen=True, slots=True)
class SyncReport:
    """What happened (safe to log: counts and codes only)."""

    outcome: str
    groups: int = 0
    parties: int = 0
    repeat: bool = False
    bills: int = 0


def _version(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in value.split("."))
    except ValueError:
        return (0,)


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    content = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def batch_id(
    device_id: uuid.UUID, snapshot: dict[str, Any], after: uuid.UUID | None = None
) -> uuid.UUID:
    """Same content and date -> same id (a retry is recognised by the server). ``after``: the
    batch the server last accepted from this agent when its content differed, so figures that
    go back to an earlier snapshot's (A, B, then A again) are a new batch, not a repeat of A."""
    digest = snapshot_digest(snapshot)
    tail = f":{after}" if after is not None else ""
    return uuid.uuid5(BATCH_NAMESPACE, f"{device_id}:{digest}{tail}")


def collect(
    tally: TallyClient,
    company: str,
    groups: Sequence[str],
    today: dt.date,
    tally_names: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Parties under the selected groups, each once, reported under a selected group.

    ``groups`` are SchoolOS's (NFC) names; ``tally_names`` maps each to Tally's own spelling,
    which is what Tally is asked with. Names are compared after NFC."""
    selected = {nfc(g) for g in groups}
    seen: dict[str, dict[str, Any]] = {}
    for group in groups:
        asked = (tally_names or {}).get(group, group)
        for ledger in parse_ledgers(tally.export(ledgers_request(company, asked, today))):
            key = ledger.guid or f"name:{ledger.name}"
            if key not in seen:
                parent = nfc(ledger.parent)
                reported = parent if parent in selected else group
                seen[key] = _party(ledger, reported)
    return list(seen.values())


def _party(ledger: TallyLedger, group: str) -> dict[str, Any]:
    return {
        "guid": ledger.guid if ledger.guid and len(ledger.guid) <= 80 else None,
        "name": ledger.name[:200],
        "group": group,
        "closing_balance": str(ledger.owed),
    }


def collect_bills(
    tally: TallyClient, company: str, parties: Sequence[dict[str, Any]], today: dt.date
) -> int:
    """Attach each party's pending bills (``party["bills"]``; D63) from one export of the
    company's bills; bills of ledgers not in ``parties`` are dropped here and never sent. A
    ledger without bill-wise details gets no ``bills`` key. Returns the number attached; 0 when
    Tally cannot answer for bills (the balances still go)."""
    try:
        bills = parse_bills(tally.export(bills_request(company, today)))
    except TallyXmlError:
        log.warning("tally.bills.unreadable", extra={"code": "bills_unreadable"})
        return 0
    by_ledger: dict[str, list[TallyBill]] = {}
    for bill in bills:
        by_ledger.setdefault(nfc(bill.ledger[:MAX_NAME_CHARS]), []).append(bill)
    count = 0
    for party in parties:
        own = by_ledger.get(nfc(party["name"]), [])
        if not own:
            continue
        own.sort(key=lambda b: (b.due_date is None, b.due_date or dt.date.min, b.reference))
        party["bills"] = [_bill(b) for b in own[:MAX_BILLS_PER_PARTY]]
        count += len(party["bills"])
    return count


def _bill(bill: TallyBill) -> dict[str, Any]:
    return {
        "reference": bill.reference[:MAX_NAME_CHARS],
        "bill_date": bill.bill_date.isoformat() if bill.bill_date else None,
        "due_date": bill.due_date.isoformat() if bill.due_date else None,
        "pending": str(bill.pending),
    }


def _size(value: Any) -> int:
    """Bytes of ``value`` as :class:`SchoolOSClient` encodes it (compact UTF-8 JSON)."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def fit_bills(snapshot: dict[str, Any], limit: int = MAX_REQUEST_BYTES) -> int:
    """Keep the body under ``limit``: when it would pass it, the bills of the last parties are
    left out (those ledgers fall back to their balance). Returns how many bills were dropped."""
    if _size(snapshot) <= limit - BODY_MARGIN_BYTES:
        return 0
    parties: list[dict[str, Any]] = snapshot["parties"]
    bare = [{k: v for k, v in p.items() if k != "bills"} for p in parties]
    budget = limit - BODY_MARGIN_BYTES - _size({**snapshot, "parties": bare})
    dropped = 0
    for party in parties:
        if "bills" not in party:
            continue
        cost = _size(party["bills"]) + len(',"bills":')
        if cost <= budget:
            budget -= cost
        else:
            dropped += len(party.pop("bills"))
            budget = -1  # keep the order: no later party gets bills either
    return dropped


class Agent:
    def __init__(
        self,
        server: SchoolOSClient,
        tally: TallyClient,
        *,
        today: Callable[[], dt.date] | None = None,
        on_key_rotated: Callable[[AgentConfig, bytes], None] | None = None,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self.server = server
        self.tally = tally
        self._today = today or (lambda: dt.datetime.now().date())  # noqa: DTZ005 - Tally's local date
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._on_key_rotated = on_key_rotated
        self.interval_s = 30 * 60.0
        self.rotate_after_days = 90
        # The last snapshot the server accepted (content digest, batch id), in memory only.
        self._accepted: tuple[str, uuid.UUID] | None = None
        # Stands in for the last accepted batch until this run has one: after a restart the
        # agent does not know what the server last accepted, so the first snapshot of a run
        # must never collide with a batch of an earlier run (A, B, restart, A is applied).
        self._run_id = uuid.uuid4()

    # --- one sync -------------------------------------------------------------------------------

    def _company(self, wanted: str | None) -> str:
        companies = parse_companies(self.tally.export(companies_request()))
        if not companies:
            raise AgentError("no_company_open")
        if wanted is not None:  # Tally's own spelling of the company SchoolOS names (NFC)
            for company in companies:
                if nfc(company) == nfc(wanted):
                    return company
        return companies[0]

    def sync_once(self) -> SyncReport:
        config = self.server.get_config()
        self.interval_s = max(5.0, float(config.get("sync_interval_minutes", 30))) * 60.0
        self.rotate_after_days = int(config.get("rotate_after_days", 90))
        if _version(__version__) < _version(str(config.get("min_agent_version", "0.0.0"))):
            raise Outdated("agent_outdated")
        company = self._company(config.get("company"))
        groups = parse_groups(self.tally.export(groups_request(company)))
        catalog = sorted(groups, key=lambda g: g.name)[:MAX_CATALOG_GROUPS]
        if len(groups) > MAX_CATALOG_GROUPS:
            # Counts only; the first groups by name are sent so the choice is stable.
            extra = {"code": "too_many_groups", "count": len(groups)}
            log.warning("tally.catalog.truncated", extra=extra)
        self.server.put_catalog(
            {
                "company": company,
                "tally_product": "TallyPrime",
                "groups": [
                    {
                        "name": g.name[:MAX_NAME_CHARS],
                        "parent": g.parent[:MAX_NAME_CHARS] if g.parent else None,
                    }
                    for g in catalog
                ],
            }
        )
        selected = [str(g) for g in config.get("groups") or []]
        wanted = config.get("company")
        if not selected or wanted is None or nfc(str(wanted)) != nfc(company):
            log.info("tally.sync.skipped", extra={"code": "no_groups_selected"})
            return SyncReport(outcome="no_groups_selected", groups=len(groups))
        today = self._today()
        tally_names = {nfc(g.name): g.name for g in groups}
        parties = collect(self.tally, company, selected, today, tally_names)
        bills = collect_bills(self.tally, company, parties, today)
        snapshot = {
            "company": nfc(company),
            "as_of": today.isoformat(),
            "groups": selected,
            "parties": parties,
        }
        dropped = fit_bills(snapshot)
        if dropped:  # counts only
            log.warning("tally.bills.trimmed", extra={"code": "body_limit", "count": dropped})
            bills -= dropped
        device = self.server.config.device_id
        if device is None:  # pragma: no cover - SchoolOSClient.signed refused already
            raise CredentialRefused("not_enrolled")
        digest = snapshot_digest(snapshot)
        if self._accepted is not None and self._accepted[0] == digest:
            batch = self._accepted[1]  # unchanged since the server accepted it: a repeat
        else:
            batch = batch_id(
                device, snapshot, self._accepted[1] if self._accepted else self._run_id
            )
        result = self.server.post_sync({"batch_id": str(batch), **snapshot})
        self._accepted = (digest, batch)
        report = SyncReport(
            outcome="synced",
            groups=len(selected),
            parties=len(parties),
            repeat=bool(result.get("repeat")),
            bills=bills,
        )
        log.info(
            "tally.sync.done",
            extra={"count": report.parties, "code": "repeat" if report.repeat else "applied"},
        )
        return report

    # --- key rotation ---------------------------------------------------------------------------

    def rotate_if_due(self) -> bool:
        cfg = self.server.config
        if cfg.key_rotated_at is not None and self._now() - cfg.key_rotated_at < dt.timedelta(
            days=self.rotate_after_days
        ):
            return False
        return self.rotate()

    def rotate(self) -> bool:
        out = self.server.rotate_key()
        secret = _b64(str(out["secret"]))
        new = self.server.config.replace(key_id=str(out["key_id"]), key_rotated_at=self._now())
        if self._on_key_rotated is not None:
            self._on_key_rotated(new, secret)
        self.server.use(new)
        log.info("tally.key.rotated", extra={"code": "rotated"})
        return True

    # --- the service loop -----------------------------------------------------------------------

    def next_wait(self, failures: int, error: AgentError | None) -> float:
        if error is None:
            return self.interval_s
        if isinstance(error, RateLimited) and error.retry_after_s:
            return max(error.retry_after_s, 30.0)
        if isinstance(error, CredentialRefused | ConnectorOff | Outdated | PayloadTooLarge):
            return REFUSED_WAIT_S
        base = min(BACKOFF_MAX_S, BACKOFF_MIN_S * float(2 ** max(0, failures - 1)))
        jitter: float = 0.5 + random.random() / 2  # noqa: S311 - jitter, not security
        return base * jitter

    def run(self, stop: threading.Event, *, max_cycles: int | None = None) -> None:
        failures = 0
        cycles = 0
        while not stop.is_set():
            error: AgentError | None = None
            try:
                self.sync_once()
                self.rotate_if_due()
                failures = 0
            except AgentError as err:
                failures += 1
                error = err
                log.warning("tally.sync.failed", extra={"code": err.code, "count": failures})
            except ValueError as err:  # a Tally answer we cannot read
                failures += 1
                error = AgentError("tally_answer_unreadable")
                log.warning("tally.sync.failed", extra={"code": type(err).__name__})
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
            stop.wait(self.next_wait(failures, error))


def _b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


__all__ = [
    "BATCH_NAMESPACE",
    "Agent",
    "SyncReport",
    "batch_id",
    "collect",
    "collect_bills",
    "fit_bills",
]
