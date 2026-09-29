"""Pydantic IO for the Tally connector (docs/09 Tally connector; ADR-0032; FR-TALLY-001..010).

School side (staff sessions) and agent side (``/edge/tally/*``, device-signed). Agent bodies are
strict (unknown fields rejected); text is NFC-normalised and trimmed. Money is ``Decimal`` with
two places (INR), never a float; a positive balance is what the party owes the school.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.core.textnorm import nfc
from app.tally.config import VERSION_PATTERN

DeviceStatus = Literal["active", "revoked"]
LinkFilter = Literal["all", "linked", "unlinked"]
MAX_AMOUNT = Decimal("999999999999.99")


def _clean(value: Any) -> Any:
    return nfc(value).strip() if isinstance(value, str) else value


Text = Annotated[str, BeforeValidator(_clean)]
TallyName = Annotated[Text, Field(min_length=1, max_length=200)]
Amount = Annotated[Decimal, Field(max_digits=14, decimal_places=2, ge=-MAX_AMOUNT, le=MAX_AMOUNT)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- school side ----------------------------------------------------------------------------------


class EnrolmentCodeCreate(_In):
    device_name: Annotated[Text, Field(min_length=1, max_length=80)] = "Office PC"
    """A label for the PC (e.g. "Accounts PC"); shown on the connector screen."""


class EnrolmentCodeOut(_Out):
    """Shown ONCE: SchoolOS keeps only a hash of the code (ADR-0032 §2)."""

    id: uuid.UUID
    code: str = Field(description="12 characters, grouped XXXX-XXXX-XXXX; valid once")
    device_name: str
    expires_at: dt.datetime


class DeviceOut(_Out):
    id: uuid.UUID
    name: str
    status: DeviceStatus
    agent_version: str | None
    platform: str | None
    tally_product: str | None
    enrolled_at: dt.datetime
    revoked_at: dt.datetime | None
    last_seen_at: dt.datetime | None
    last_sync_at: dt.datetime | None
    silent: bool = Field(description="Active, and no call for longer than the silence limit")
    outdated: bool = Field(description="Older than the minimum agent version; it stops syncing")
    version: int


class ConnectorStatus(_Out):
    """The connector at a glance. Money totals only for school-wide ``finance.read`` holders."""

    devices_active: int
    silent: bool
    last_sync_at: dt.datetime | None
    as_of: dt.date | None
    company: str | None
    groups_selected: int
    parties: int
    parties_linked: int
    parties_unlinked: int
    total_due: Decimal | None
    unlinked_due: Decimal | None


class GroupOut(_Out):
    id: uuid.UUID
    company: str
    name: str
    parent: str | None
    present: bool
    selected: bool
    version: int


class GroupSelectionIn(_In):
    """The groups whose party ledgers the agent may send (all of one company). Replaces the
    current selection; an empty list stops the sync of parties."""

    company: TallyName
    group_ids: list[uuid.UUID] = Field(max_length=100)


class LinkedStudentOut(_Out):
    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None


class PartyOut(_Out):
    id: uuid.UUID
    ledger_name: str
    group_name: str
    closing_balance: Decimal = Field(description="Positive: the party owes the school")
    as_of: dt.date
    present: bool
    links: list[LinkedStudentOut]


class PartyDetail(PartyOut):
    candidates: list[LinkedStudentOut] = Field(
        description="Students whose name, admission number or class appears in the ledger name. "
        "Suggestions only: a person links (ADR-0032 §6)."
    )


class PartySearchIn(_In):
    query: Annotated[Text, Field(min_length=1, max_length=100)]
    link: LinkFilter = "all"


class LinkIn(_In):
    student_id: uuid.UUID


class StudentDuesOut(_Out):
    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    total_due: Decimal
    ledgers: int
    as_of: dt.date


class DuesTotals(_Out):
    students_with_dues: int
    total_due: Decimal
    unlinked_parties: int
    unlinked_due: Decimal
    as_of: dt.date | None


class DuesPage(_Out):
    data: list[StudentDuesOut]
    next_cursor: str | None
    totals: DuesTotals


# --- agent side -----------------------------------------------------------------------------------


class AgentConfigOut(_Out):
    company: str | None = Field(description="The Tally company to read; null until chosen")
    groups: list[str] = Field(description="Selected ledger groups: send parties under these only")
    sync_interval_minutes: int
    max_parties: int
    min_agent_version: str
    rotate_after_days: int
    server_time: dt.datetime


class EnrolIn(_In):
    code: Annotated[str, Field(min_length=12, max_length=20, pattern=r"^[A-Za-z0-9 -]+$")]
    agent_version: Annotated[str, Field(pattern=VERSION_PATTERN)]
    platform: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,39}$")]


class EnrolOut(_Out):
    """Returned ONCE: the agent stores the secret with Windows DPAPI (ADR-0032 §7)."""

    device_id: uuid.UUID
    key_id: str
    secret: str = Field(description="base64url, 32 bytes; the HMAC key")
    config: AgentConfigOut


class CatalogGroupIn(_In):
    name: TallyName
    parent: TallyName | None = None


class CatalogIn(_In):
    """The ledger groups of the company open in Tally (names only; no ledgers, no people)."""

    company: TallyName
    tally_product: Annotated[Text, Field(min_length=1, max_length=80)] | None = None
    groups: list[CatalogGroupIn] = Field(max_length=2000)


class CatalogOut(_Out):
    groups: int
    selected: int


class PartyIn(_In):
    guid: Annotated[str, Field(pattern=r"^[0-9A-Za-z-]{1,80}$")] | None = None
    name: TallyName
    group: TallyName
    closing_balance: Amount


class SyncIn(_In):
    """One complete snapshot of the parties under the selected groups (FR-TALLY-004/005)."""

    batch_id: uuid.UUID
    company: TallyName
    as_of: dt.date
    groups: list[TallyName] = Field(max_length=100)
    parties: list[PartyIn] = Field(max_length=20000)

    @field_validator("as_of")
    @classmethod
    def _not_future(cls, value: dt.date) -> dt.date:
        if value > dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1):
            raise ValueError("as_of is in the future")
        return value


class SyncOut(_Out):
    sync_id: uuid.UUID
    batch_id: uuid.UUID
    repeat: bool = Field(description="True when this batch was already accepted (nothing applied)")
    parties: int
    created: int
    updated: int
    missing: int
    received_at: dt.datetime


class KeyRotationOut(_Out):
    """The new key, returned ONCE. The old key works until the new one is first used, or for
    the rotation overlap at most."""

    key_id: str
    secret: str


__all__ = [
    "AgentConfigOut",
    "CatalogGroupIn",
    "CatalogIn",
    "CatalogOut",
    "ConnectorStatus",
    "DeviceOut",
    "DeviceStatus",
    "DuesPage",
    "DuesTotals",
    "EnrolIn",
    "EnrolOut",
    "EnrolmentCodeCreate",
    "EnrolmentCodeOut",
    "GroupOut",
    "GroupSelectionIn",
    "KeyRotationOut",
    "LinkFilter",
    "LinkIn",
    "LinkedStudentOut",
    "PartyDetail",
    "PartyIn",
    "PartyOut",
    "PartySearchIn",
    "StudentDuesOut",
    "SyncIn",
    "SyncOut",
]
