"""Fixtures for audit tests (synthetic IDs only; no personal data anywhere)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import Connection, Engine, create_engine, text

from app.audit.schemas import AuditEvent
from app.audit.service import record
from app.core.db import tenant_session


@pytest.fixture(scope="session")
def migrator_engine(test_database: Any) -> Iterator[Engine]:
    engine = create_engine(test_database.migrator_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def tenant() -> uuid.UUID:
    """A fresh synthetic tenant id (each test gets its own chain)."""
    return uuid.uuid4()


RecordN = Callable[[uuid.UUID, int], list[AuditEvent]]


@pytest.fixture
def record_events(app_engine: Engine) -> RecordN:
    """Record ``n`` synthetic events for ``tenant``, one committed transaction each."""

    def _record(tenant_id: uuid.UUID, n: int) -> list[AuditEvent]:
        out: list[AuditEvent] = []
        for i in range(n):
            with tenant_session(tenant_id) as s:
                out.append(
                    record(
                        s,
                        action="student.value.recorded",
                        resource_type="student",
                        resource_id=uuid.uuid4(),
                        summary={"field": "dob", "source": "admission_register", "index": i},
                        actor_id=uuid.uuid4(),
                        request_id=f"req-{i}",
                        ip_hash=bytes([i % 256]) * 32,
                    )
                )
        return out

    return _record


@pytest.fixture
def tamper(admin_engine: Engine) -> Callable[[], AbstractContextManager[Connection]]:
    """Superuser transaction with user triggers disabled (simulates a malicious DBA)."""

    @contextmanager
    def _tamper() -> Iterator[Connection]:
        with admin_engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            yield conn

    return _tamper


class FakeS3:
    """Minimal in-memory S3 (head_object/put_object) with S3-shaped 404 errors."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.puts: list[dict[str, Any]] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        obj = self.objects.get((Bucket, Key))
        if obj is None:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"Metadata": dict(obj["Metadata"]), "ContentLength": len(obj["Body"])}

    def put_object(self, **params: Any) -> dict[str, Any]:
        self.puts.append(params)
        self.objects[(params["Bucket"], params["Key"])] = params
        return {"ETag": '"fake"'}

    def body(self, bucket: str, key: str) -> bytes:
        body: bytes = self.objects[(bucket, key)]["Body"]
        return body


OwnerConn = Callable[..., AbstractContextManager[Connection]]


@pytest.fixture
def owner_conn(migrator_engine: Engine) -> OwnerConn:
    """Connection acting as ``sos_owner`` (optionally with tenant context); always rolled back."""

    @contextmanager
    def _owner(tenant_id: uuid.UUID | None = None) -> Iterator[Connection]:
        with migrator_engine.connect() as conn:
            conn.execute(text("SET LOCAL ROLE sos_owner"))
            if tenant_id is not None:
                # Makes rows visible under FORCE RLS so row triggers actually fire.
                conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
                )
            try:
                yield conn
            finally:
                conn.rollback()

    return _owner


@pytest.fixture
def fake_s3() -> FakeS3:
    return FakeS3()
