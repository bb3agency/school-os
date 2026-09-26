"""`python -m app.audit.verify_all` exit codes for restore drills (SEC-024, FR-AUD-004)."""

from __future__ import annotations

import uuid

import pytest

from app.audit import verify_all as cli
from app.audit.service import VerifyResult

pytestmark = pytest.mark.db


def test_cli_exits_zero_when_all_chains_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    tid = uuid.uuid4()
    monkeypatch.setattr(cli, "list_tenant_ids", lambda **_: [tid])
    monkeypatch.setattr(
        cli, "verify_all", lambda ids, **_: {t: VerifyResult(True, 3, None, None) for t in ids}
    )
    monkeypatch.setattr(cli, "verify_platform", lambda **_: VerifyResult(True, 1, None, None))
    assert cli.main() == 0


def test_cli_exits_one_when_a_tenant_chain_is_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    tid = uuid.uuid4()
    monkeypatch.setattr(cli, "list_tenant_ids", lambda **_: [tid])
    monkeypatch.setattr(
        cli,
        "verify_all",
        lambda ids, **_: {t: VerifyResult(False, 2, 2, "hash_mismatch") for t in ids},
    )
    monkeypatch.setattr(cli, "verify_platform", lambda **_: VerifyResult(True, 1, None, None))
    assert cli.main() == 1


def test_cli_exits_one_when_platform_chain_is_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "list_tenant_ids", lambda **_: [])
    monkeypatch.setattr(cli, "verify_all", lambda ids, **_: {})
    monkeypatch.setattr(
        cli, "verify_platform", lambda **_: VerifyResult(False, 1, 1, "hash_mismatch")
    )
    assert cli.main() == 1
