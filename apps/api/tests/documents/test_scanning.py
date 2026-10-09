"""Malware scanner adapters (FR-DOC-002, SEC-016). A fake clamd speaks the INSTREAM protocol."""

from __future__ import annotations

import socket
import struct
import sys
import threading
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from app.core.config import AvScannerKind, Environment, KeyWrapperKind, Settings
from app.documents.scanning import (
    ClamAvScanner,
    DevNoopScanner,
    ScannerRefused,
    ScannerUnavailable,
    Verdict,
    build_scanner,
    safe_signature,
)

S = sys.modules["sos_test_documents_support"]


class FakeClamd:
    """Minimal clamd: collects INSTREAM chunks and answers OK / FOUND / ERROR."""

    def __init__(self, reply: bytes | None = None) -> None:
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.received = b""
        self.command = b""
        self.reply = reply
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _recv_exact(self, conn: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            part = conn.recv(n - len(buf))
            if not part:
                raise ConnectionError
            buf += part
        return buf

    def _serve(self) -> None:
        conn, _ = self.server.accept()
        with conn:
            cmd = b""
            while not cmd.endswith(b"\0"):
                cmd += conn.recv(1)
            self.command = cmd
            if cmd == b"zPING\0":
                conn.sendall(b"PONG\0")
                return
            while True:
                (size,) = struct.unpack("!L", self._recv_exact(conn, 4))
                if size == 0:
                    break
                self.received += self._recv_exact(conn, size)
            if self.reply is not None:
                conn.sendall(self.reply)
            elif S.EICAR in self.received:
                conn.sendall(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
            else:
                conn.sendall(b"stream: OK\0")

    def close(self) -> None:
        self.server.close()


@pytest.fixture
def clamd() -> Iterator[FakeClamd]:
    server = FakeClamd()
    yield server
    server.close()


def _chunks(data: bytes, size: int = 100_000) -> Iterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


def test_FR_DOC_002_clamav_streams_everything_and_reports_clean(clamd: FakeClamd) -> None:
    data = S.pdf() * 3000  # > 64 KiB: split into several INSTREAM chunks
    result = ClamAvScanner("127.0.0.1", clamd.port, timeout_s=5).scan(_chunks(data))
    assert result.verdict is Verdict.CLEAN
    assert clamd.command == b"zINSTREAM\0"
    assert clamd.received == data


def test_FR_DOC_002_clamav_reports_infected_with_signature(clamd: FakeClamd) -> None:
    result = ClamAvScanner("127.0.0.1", clamd.port, timeout_s=5).scan([b"prefix", S.EICAR])
    assert result.verdict is Verdict.INFECTED
    assert result.signature == "Win.Test.EICAR_HDB-1"
    assert result.engine == "clamav"


def test_FR_DOC_002_clamav_error_reply_is_not_clean() -> None:
    server = FakeClamd(reply=b"INSTREAM size limit exceeded. ERROR\0")
    try:
        with pytest.raises(ScannerUnavailable):
            ClamAvScanner("127.0.0.1", server.port, timeout_s=5).scan([b"x" * 10])
    finally:
        server.close()


def test_FR_DOC_002_unreachable_clamd_is_unavailable_never_clean() -> None:
    sock = socket.create_server(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(ScannerUnavailable):
        ClamAvScanner("127.0.0.1", port, timeout_s=1).scan([b"data"])


def test_FR_DOC_002_clamav_ping(clamd: FakeClamd) -> None:
    assert ClamAvScanner("127.0.0.1", clamd.port, timeout_s=5).ping() is True


def _settings(env: Environment) -> Settings:
    if env in (Environment.STAGING, Environment.PROD):
        return Settings(
            env=env,
            key_wrapper=KeyWrapperKind.KMS,
            database_url=SecretStr(
                "postgresql+psycopg://sos_app:x@db:5432/schoolos?sslmode=verify-full"
            ),
            platform_database_url=SecretStr(
                "postgresql+psycopg://sos_platform:y@db:5432/s?sslmode=verify-full"
            ),
            service_token_key=SecretStr("k" * 48),
        )
    return Settings(env=env, local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789"))


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_SEC_016_dev_noop_scanner_is_refused_in_production_like_envs(env: Environment) -> None:
    settings = _settings(env)
    with pytest.raises(ScannerRefused):
        DevNoopScanner(settings)
    with pytest.raises(ScannerRefused):
        build_scanner(settings)  # the default kind is dev-noop: prod must configure clamav


def test_SEC_016_production_builds_clamav() -> None:
    settings = _settings(Environment.PROD).model_copy(update={"av_scanner": AvScannerKind.CLAMAV})
    assert isinstance(build_scanner(settings), ClamAvScanner)


def test_FR_DOC_002_dev_scanner_flags_eicar_even_across_chunks() -> None:
    scanner = DevNoopScanner(_settings(Environment.CI))
    assert scanner.scan([S.pdf()]).verdict is Verdict.CLEAN
    split = len(S.EICAR) // 2
    infected = scanner.scan([b"head" + S.EICAR[:split], S.EICAR[split:] + b"tail"])
    assert infected.verdict is Verdict.INFECTED


def test_signature_is_sanitised_for_audit() -> None:
    assert safe_signature("Win.Test.EICAR_HDB-1") == "Win.Test.EICAR_HDB-1"
    assert safe_signature("bad name<x>") == "bad_name_x_"
    assert safe_signature("a\nb") == "a_b"
    assert len(safe_signature("x" * 500)) == 100
    assert not any(ch.isdigit() for ch in safe_signature("x9876543210"))
