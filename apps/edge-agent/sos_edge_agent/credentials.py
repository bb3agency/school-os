"""Where the device secret lives on the PC (ADR-0032 §7).

- :class:`DpapiStore` (Windows, the only store for real use): the 32-byte secret is protected
  with ``CryptProtectData`` in the **service account's user scope** (not the machine scope), with
  the school id as extra entropy, and written to ``device.key`` in the state directory. Only the
  same Windows account on the same PC can unprotect it; other users of the shared office PC
  cannot. No pywin32: the two crypt32 calls go through :mod:`ctypes`.
- :class:`InsecureFileStore` (development and CI only): base64 in a file readable by the owner
  only; used only when ``--insecure-file-store`` is given, never chosen silently.

Nothing else sensitive is written to disk: snapshots stay in memory, logs carry counts and codes.
"""

from __future__ import annotations

import base64
import ctypes
import os
import sys
import uuid
from pathlib import Path
from typing import Final, Protocol

KEY_FILE: Final = "device.key"
_CRYPTPROTECT_UI_FORBIDDEN: Final = 0x1


class CredentialError(RuntimeError):
    """The secret cannot be stored or read (message says what to do; never the secret)."""


class CredentialStore(Protocol):
    def save(self, secret: bytes) -> None: ...

    def load(self) -> bytes: ...

    def clear(self) -> None: ...


class _Blob(ctypes.Structure):
    _fields_ = (("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char)))


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


class DpapiStore:
    """Windows DPAPI, current user scope, school id as entropy."""

    def __init__(self, directory: Path, tenant_id: uuid.UUID) -> None:
        if sys.platform != "win32":
            raise CredentialError("DPAPI is only available on Windows")
        self.path: Path = directory / KEY_FILE
        self._entropy: bytes = b"sos-tally-agent:" + tenant_id.bytes

    def _call(self, name: str, data: bytes) -> bytes:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        source, _keep = _blob(data)
        entropy, _keep_entropy = _blob(self._entropy)
        out = _Blob()
        function = getattr(crypt32, name)
        if name == "CryptProtectData":
            ok = function(
                ctypes.byref(source),
                "SchoolOS Tally agent",
                ctypes.byref(entropy),
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out),
            )
        else:
            ok = function(
                ctypes.byref(source),
                None,
                ctypes.byref(entropy),
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out),
            )
        if not ok:
            code = ctypes.get_last_error()  # type: ignore[attr-defined]
            raise CredentialError(
                f"Windows could not protect or unprotect the device key (error {code}). "
                "Run the agent as the Windows account that enrolled it."
            )
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)

    def save(self, secret: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        protected = self._call("CryptProtectData", secret)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(protected)
        temporary.replace(self.path)

    def load(self) -> bytes:
        if not self.path.is_file():
            raise CredentialError("no device key: run `sos-tally-agent enrol` first")
        return self._call("CryptUnprotectData", self.path.read_bytes())

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class InsecureFileStore:
    """Development and CI only: the secret base64-encoded in an owner-only file."""

    def __init__(self, directory: Path) -> None:
        self.path: Path = directory / KEY_FILE

    def save(self, secret: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        descriptor = os.open(self.path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(base64.b64encode(secret))

    def load(self) -> bytes:
        if not self.path.is_file():
            raise CredentialError("no device key: run `sos-tally-agent enrol` first")
        return base64.b64decode(self.path.read_bytes())

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def default_store(
    directory: Path, tenant_id: uuid.UUID, *, allow_insecure: bool = False
) -> CredentialStore:
    """DPAPI on Windows; elsewhere only with ``allow_insecure`` (development)."""
    if sys.platform == "win32":
        return DpapiStore(directory, tenant_id)
    if allow_insecure:
        return InsecureFileStore(directory)
    raise CredentialError(
        "The device key can only be stored with Windows DPAPI. For development on another "
        "system, pass --insecure-file-store."
    )


__all__ = [
    "KEY_FILE",
    "CredentialError",
    "CredentialStore",
    "DpapiStore",
    "InsecureFileStore",
    "default_store",
]
