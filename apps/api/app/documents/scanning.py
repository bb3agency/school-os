"""Malware scanning of uploads before any processing (FR-DOC-002, SEC-016, docs/10 §6).

- :class:`ClamAvScanner` streams the object to a clamd sidecar with the ``INSTREAM`` command over
  TCP (connect/read timeouts; no file ever touches local disk).
- :class:`DevNoopScanner` is for local development and CI only: it refuses to exist or run when
  the environment is staging/prod. As a convenience it flags the harmless EICAR test string so
  the quarantine path can be exercised locally.
"""

from __future__ import annotations

import re
import socket
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from app.core.config import AvScannerKind, Settings

# The EICAR anti-virus test file (harmless by definition; recognised by every engine). Split
# in source so workstation anti-virus does not flag this file itself.
EICAR: Final = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-" + b"ANTIVIRUS-TEST-FILE!$H+H*"
_MAX_CHUNK: Final = 64 * 1024
_SIGNATURE_SAFE: Final = re.compile(r"[^A-Za-z0-9._:-]")


class Verdict(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"


@dataclass(frozen=True, slots=True)
class ScanResult:
    verdict: Verdict
    engine: str
    signature: str | None = None


class ScannerUnavailable(RuntimeError):
    """The scanner could not give a verdict (retry later; never treat as clean)."""


class ScannerRefused(RuntimeError):
    """A development scanner was asked to run in staging/production."""


class AvScanner(Protocol):
    engine: str

    def scan(self, chunks: Iterable[bytes]) -> ScanResult: ...


def safe_signature(raw: str) -> str:
    """Signature names are engine identifiers; keep a short, printable form for audit."""
    cleaned = _SIGNATURE_SAFE.sub("_", raw.strip())[:100]
    return re.sub(r"\d{6,}", "_", cleaned) or "unknown"


class ClamAvScanner:
    engine = "clamav"

    def __init__(self, host: str, port: int, *, timeout_s: float = 30.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout_s

    def _connect(self) -> socket.socket:
        try:
            return socket.create_connection((self._host, self._port), timeout=self._timeout)
        except OSError as exc:
            raise ScannerUnavailable("clamd_unreachable") from exc

    @staticmethod
    def _read_reply(sock: socket.socket) -> str:
        data = b""
        while not data.endswith(b"\0"):
            part = sock.recv(4096)
            if not part:
                break
            data += part
            if len(data) > 4096:
                raise ScannerUnavailable("clamd_bad_reply")
        return data.rstrip(b"\0").decode("utf-8", errors="replace").strip()

    def ping(self) -> bool:
        try:
            with self._connect() as sock:
                sock.sendall(b"zPING\0")
                return self._read_reply(sock) == "PONG"
        except (OSError, ScannerUnavailable):
            return False

    def scan(self, chunks: Iterable[bytes]) -> ScanResult:
        try:
            with self._connect() as sock:
                sock.sendall(b"zINSTREAM\0")
                for chunk in chunks:
                    for i in range(0, len(chunk), _MAX_CHUNK):
                        piece = chunk[i : i + _MAX_CHUNK]
                        sock.sendall(struct.pack("!L", len(piece)) + piece)
                sock.sendall(struct.pack("!L", 0))
                reply = self._read_reply(sock)
        except TimeoutError as exc:
            raise ScannerUnavailable("clamd_timeout") from exc
        except OSError as exc:
            raise ScannerUnavailable("clamd_io_error") from exc
        return self._parse(reply)

    def _parse(self, reply: str) -> ScanResult:
        # "stream: OK" | "stream: <signature> FOUND" | "<message> ERROR"
        if reply.endswith(" OK") or reply == "OK":
            return ScanResult(Verdict.CLEAN, self.engine)
        if reply.endswith(" FOUND"):
            body = reply.removesuffix(" FOUND")
            signature = body.split(":", 1)[1] if ":" in body else body
            return ScanResult(Verdict.INFECTED, self.engine, safe_signature(signature))
        raise ScannerUnavailable("clamd_error")


class DevNoopScanner:
    """Local/CI stand-in: everything is clean except the EICAR test string. Refused in prod."""

    engine = "dev-noop"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._refuse()

    def _refuse(self) -> None:
        if self._settings.is_production_like:
            raise ScannerRefused("the dev-noop scanner is not allowed in staging/prod")

    def scan(self, chunks: Iterable[bytes]) -> ScanResult:
        self._refuse()
        tail = b""
        for chunk in chunks:
            window = tail + chunk
            if EICAR in window:
                return ScanResult(Verdict.INFECTED, self.engine, "Eicar-Test-Signature")
            tail = window[-len(EICAR) :]
        return ScanResult(Verdict.CLEAN, self.engine)


def build_scanner(settings: Settings) -> AvScanner:
    if settings.av_scanner is AvScannerKind.CLAMAV:
        return ClamAvScanner(
            settings.clamav_host, settings.clamav_port, timeout_s=settings.clamav_timeout_s
        )
    return DevNoopScanner(settings)
