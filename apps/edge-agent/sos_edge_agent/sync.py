"""One sync, and the service loop with backoff (ADR-0032 §4).

:meth:`Agent.sync_once`:

1. ``GET /config``: the company and the ledger groups the accountant selected; stop if this
   agent is older than ``min_agent_version``.
2. Tally: the open companies, then the groups of the chosen company -> ``PUT /catalog`` (names
   only), so the accountant can choose groups in SchoolOS.
3. If groups are selected: for each selected group, the ledgers under it (sub-groups included)
   with their closing balance today. A ledger is reported under the selected group it was read
   from (its own parent when that is also selected), once.
4. ``POST /syncs`` with a ``batch_id`` derived from the snapshot's content and date: a retry of
   the same snapshot is recognised by the server and never applied twice; a newer snapshot has a
   new id, also when its figures go back to an earlier snapshot's (the id then also names the
   last batch the server accepted, so A, B, A is three batches, not a repeat of A).

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
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sos_edge_agent import __version__
from sos_edge_agent.client import (
    AgentError,
    ConnectorOff,
    CredentialRefused,
    Outdated,
    RateLimited,
    SchoolOSClient,
    TallyClient,
)
from sos_edge_agent.config import AgentConfig
from sos_edge_agent.tally_xml import (
    TallyLedger,
    companies_request,
    groups_request,
    ledgers_request,
    parse_companies,
    parse_groups,
    parse_ledgers,
)

log = logging.getLogger("sos_edge_agent")

BATCH_NAMESPACE: Final = uuid.UUID("0192f0c1-7a51-7c4e-9d0e-5c0a6b0e7f21")
BACKOFF_MIN_S: Final = 60.0
BACKOFF_MAX_S: Final = 30 * 60.0
REFUSED_WAIT_S: Final = 60 * 60.0


@dataclass(frozen=True, slots=True)
class SyncReport:
    """What happened (safe to log: counts and codes only)."""

    outcome: str
    groups: int = 0
    parties: int = 0
    repeat: bool = False


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
    tally: TallyClient, company: str, groups: Sequence[str], today: dt.date
) -> list[dict[str, Any]]:
    """Parties under the selected groups, each once, reported under a selected group."""
    selected = set(groups)
    seen: dict[str, dict[str, Any]] = {}
    for group in groups:
        for ledger in parse_ledgers(tally.export(ledgers_request(company, group, today))):
            key = ledger.guid or f"name:{ledger.name}"
            if key not in seen:
                reported = ledger.parent if ledger.parent in selected else group
                seen[key] = _party(ledger, reported)
    return list(seen.values())


def _party(ledger: TallyLedger, group: str) -> dict[str, Any]:
    return {
        "guid": ledger.guid if ledger.guid and len(ledger.guid) <= 80 else None,
        "name": ledger.name[:200],
        "group": group,
        "closing_balance": str(ledger.owed),
    }


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

    # --- one sync -------------------------------------------------------------------------------

    def _company(self, wanted: str | None) -> str:
        companies = parse_companies(self.tally.export(companies_request()))
        if not companies:
            raise AgentError("no_company_open")
        if wanted is not None and wanted in companies:
            return wanted
        return companies[0]

    def sync_once(self) -> SyncReport:
        config = self.server.get_config()
        self.interval_s = max(5.0, float(config.get("sync_interval_minutes", 30))) * 60.0
        self.rotate_after_days = int(config.get("rotate_after_days", 90))
        if _version(__version__) < _version(str(config.get("min_agent_version", "0.0.0"))):
            raise Outdated("agent_outdated")
        company = self._company(config.get("company"))
        groups = parse_groups(self.tally.export(groups_request(company)))
        self.server.put_catalog(
            {
                "company": company,
                "tally_product": "TallyPrime",
                "groups": [{"name": g.name[:200], "parent": g.parent} for g in groups][:2000],
            }
        )
        selected = [str(g) for g in config.get("groups") or []]
        if not selected or config.get("company") != company:
            log.info("tally.sync.skipped", extra={"code": "no_groups_selected"})
            return SyncReport(outcome="no_groups_selected", groups=len(groups))
        today = self._today()
        parties = collect(self.tally, company, selected, today)
        snapshot = {
            "company": company,
            "as_of": today.isoformat(),
            "groups": selected,
            "parties": parties,
        }
        device = self.server.config.device_id
        if device is None:  # pragma: no cover - SchoolOSClient.signed refused already
            raise CredentialRefused("not_enrolled")
        digest = snapshot_digest(snapshot)
        if self._accepted is not None and self._accepted[0] == digest:
            batch = self._accepted[1]  # unchanged since the server accepted it: a repeat
        else:
            batch = batch_id(device, snapshot, self._accepted[1] if self._accepted else None)
        result = self.server.post_sync({"batch_id": str(batch), **snapshot})
        self._accepted = (digest, batch)
        report = SyncReport(
            outcome="synced",
            groups=len(selected),
            parties=len(parties),
            repeat=bool(result.get("repeat")),
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
        if isinstance(error, CredentialRefused | ConnectorOff | Outdated):
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


__all__ = ["BATCH_NAMESPACE", "Agent", "SyncReport", "batch_id", "collect"]
