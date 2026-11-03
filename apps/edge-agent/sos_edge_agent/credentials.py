"""Where the device secret lives on the PC (ADR-0032 §7).

- :class:`DpapiStore` (Windows, the only store for real use): the 32-byte secret is protected
  with ``CryptProtectData`` in the **service account's user scope** (not the machine scope), with
  the school id as extra entropy, and written to ``device.key`` in the state directory. Only the
  same Windows account on the same PC can unprotect it; other users of the shared office PC
  cannot. No pywin32: the two crypt32 calls go through :mod:`ctypes`.
- :class:`InsecureFileStore` (development and CI only): base64 in a file readable by the owner
  only; used only when ``--insecure-file-store`` is given, never chosen silently.

The key file holds the device id and key id together with the secret, written as one new file,
flushed to disk and swapped in (:func:`.config.write_atomic`): a crash between the key file and
``agent.toml`` cannot leave a secret that the config's key id does not name (the loader trusts the
key file, :func:`reconcile`). A file of the first layout (the bare secret) still loads.

Nothing else sensitive is written to disk: snapshots stay in memory, logs carry counts and codes.
"""

from __future__ import annotations

import base64
import ctypes
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from sos_edge_agent.config import AgentConfig, write_atomic

KEY_FILE: Final = "device.key"
_LAYOUT_2: Final = b"sos-tally-key/2\n"
_CRYPTPROTECT_UI_FORBIDDEN: Final = 0x1


class CredentialError(RuntimeError):
    """The secret cannot be stored or read (message says what to do; never the secret)."""


@dataclass(frozen=True, slots=True)
class StoredKey:
    """The secret and the ids it belongs to (ids are ``None`` in a file of the first layout)."""

    device_id: uuid.UUID | None
    key_id: str | None
    secret: bytes


def _pack(secret: bytes, device_id: uuid.UUID | None, key_id: str | None) -> bytes:
    ids = {"device_id": str(device_id) if device_id else None, "key_id": key_id}
    return _LAYOUT_2 + json.dumps(ids, separators=(",", ":")).encode() + b"\n" + secret


def _unpack(data: bytes) -> StoredKey:
    if not data.startswith(_LAYOUT_2):
        return StoredKey(None, None, data)  # first layout: the bare secret
    header, _, secret = data[len(_LAYOUT_2) :].partition(b"\n")
    try:
        ids = json.loads(header)
        device = uuid.UUID(ids["device_id"]) if ids.get("device_id") else None
        key_id = str(ids["key_id"]) if ids.get("key_id") else None
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CredentialError("the device key file is damaged: enrol this PC again") from exc
    return StoredKey(device, key_id, secret)


class CredentialStore(Protocol):
    def save(
        self, secret: bytes, *, device_id: uuid.UUID | None = None, key_id: str | None = None
    ) -> None: ...

    def load(self) -> bytes: ...

    def load_key(self) -> StoredKey: ...

    def clear(self) -> None: ...


def reconcile(config: AgentConfig, stored: StoredKey) -> AgentConfig:
    """The ids that go with the stored secret win over ``agent.toml`` (which is written second,
    so it is the one left stale by a crash during enrolment or key rotation)."""
    changes: dict[str, object] = {}
    if stored.device_id is not None and stored.device_id != config.device_id:
        changes["device_id"] = stored.device_id
    if stored.key_id is not None and stored.key_id != config.key_id:
        changes["key_id"] = stored.key_id
    return config.replace(**changes) if changes else config


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
        # Narrows the platform for mypy too (no ``type: ignore`` needed on either platform).
        assert sys.platform == "win32"  # noqa: S101 - __init__ refused other platforms
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
            code = ctypes.get_last_error()
            raise CredentialError(
                f"Windows could not protect or unprotect the device key (error {code}). "
                "Run the agent as the Windows account that enrolled it."
            )
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)

    def save(
        self, secret: bytes, *, device_id: uuid.UUID | None = None, key_id: str | None = None
    ) -> None:
        write_atomic(self.path, self._call("CryptProtectData", _pack(secret, device_id, key_id)))

    def load_key(self) -> StoredKey:
        if not self.path.is_file():
            raise CredentialError("no device key: run `sos-tally-agent enrol` first")
        return _unpack(self._call("CryptUnprotectData", self.path.read_bytes()))

    def load(self) -> bytes:
        return self.load_key().secret

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class InsecureFileStore:
    """Development and CI only: the secret base64-encoded in an owner-only file."""

    def __init__(self, directory: Path) -> None:
        self.path: Path = directory / KEY_FILE

    def save(
        self, secret: bytes, *, device_id: uuid.UUID | None = None, key_id: str | None = None
    ) -> None:
        write_atomic(self.path, base64.b64encode(_pack(secret, device_id, key_id)))

    def load_key(self) -> StoredKey:
        if not self.path.is_file():
            raise CredentialError("no device key: run `sos-tally-agent enrol` first")
        return _unpack(base64.b64decode(self.path.read_bytes()))

    def load(self) -> bytes:
        return self.load_key().secret

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
    "StoredKey",
    "default_store",
    "reconcile",
]
