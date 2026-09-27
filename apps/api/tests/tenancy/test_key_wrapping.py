"""Per-tenant key wrapping and the fixed AEAD ciphertext format (FR-TEN-003, SEC-012, docs/05 §9).

Field encryption itself is M1; the wrap/unwrap contract and ciphertext layout are fixed now.
"""

from __future__ import annotations

import uuid
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from pydantic import SecretStr

from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import (
    CIPHERTEXT_VERSION,
    CryptoError,
    KeyWrapper,
    KmsKeyWrapper,
    LocalDevKeyWrapper,
    aead_decrypt,
    aead_encrypt,
    ciphertext_key_version,
    field_aad,
    generate_tenant_keys,
    get_key_wrapper,
    reencrypt,
)

MASTER = SecretStr("synthetic-local-dev-master-key-for-tests-0123456789")


def local_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "env": Environment.LOCAL,
        "key_wrapper": KeyWrapperKind.LOCAL_DEV,
        "local_dev_master_key": MASTER,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def wrapper() -> LocalDevKeyWrapper:
    return LocalDevKeyWrapper(local_settings())


# --- local-dev wrapper -----------------------------------------------------------------------


def test_FR_TEN_003_local_wrapper_round_trip(wrapper: LocalDevKeyWrapper) -> None:
    tenant = uuid.uuid4()
    dek = bytes(range(32))
    wrapped = wrapper.wrap(dek, tenant_id=tenant)
    assert dek not in wrapped
    assert wrapper.unwrap(wrapped, tenant_id=tenant) == dek


def test_FR_TEN_003_local_wrapper_is_nondeterministic(wrapper: LocalDevKeyWrapper) -> None:
    tenant = uuid.uuid4()
    assert wrapper.wrap(b"k" * 32, tenant_id=tenant) != wrapper.wrap(b"k" * 32, tenant_id=tenant)


def test_ADR_0013_wrapped_key_bound_to_tenant(wrapper: LocalDevKeyWrapper) -> None:
    """A wrapped DEK copied to another tenant's row cannot be unwrapped (AAD = tenant_id)."""
    wrapped = wrapper.wrap(b"k" * 32, tenant_id=uuid.uuid4())
    with pytest.raises(CryptoError):
        wrapper.unwrap(wrapped, tenant_id=uuid.uuid4())


def test_FR_TEN_003_local_wrapper_detects_tampering(wrapper: LocalDevKeyWrapper) -> None:
    tenant = uuid.uuid4()
    wrapped = bytearray(wrapper.wrap(b"k" * 32, tenant_id=tenant))
    wrapped[-1] ^= 0x01
    with pytest.raises(CryptoError):
        wrapper.unwrap(bytes(wrapped), tenant_id=tenant)
    with pytest.raises(CryptoError):
        wrapper.unwrap(b"\x01short", tenant_id=tenant)


def test_FR_TEN_003_different_master_keys_do_not_interoperate(
    wrapper: LocalDevKeyWrapper,
) -> None:
    tenant = uuid.uuid4()
    other = LocalDevKeyWrapper(
        local_settings(local_dev_master_key=SecretStr("another-synthetic-master-key-0123456789ab"))
    )
    assert other.key_id != wrapper.key_id
    with pytest.raises(CryptoError):
        other.unwrap(wrapper.wrap(b"k" * 32, tenant_id=tenant), tenant_id=tenant)


def test_SEC_009_local_wrapper_refuses_production_like_settings() -> None:
    # Settings itself refuses local-dev in prod; model_construct simulates a bypassed validator.
    for env in (Environment.STAGING, Environment.PROD):
        settings = Settings.model_construct(
            env=env, key_wrapper=KeyWrapperKind.LOCAL_DEV, local_dev_master_key=MASTER
        )
        with pytest.raises(RuntimeError, match="not allowed"):
            LocalDevKeyWrapper(settings)


def test_local_wrapper_requires_a_strong_master_key() -> None:
    with pytest.raises(ValueError, match="SOS_LOCAL_DEV_MASTER_KEY"):
        LocalDevKeyWrapper(local_settings(local_dev_master_key=None))
    with pytest.raises(ValueError, match="at least 32"):
        LocalDevKeyWrapper(local_settings(local_dev_master_key=SecretStr("short")))


def test_local_wrapper_key_id_reveals_no_key_material(wrapper: LocalDevKeyWrapper) -> None:
    assert wrapper.key_id.startswith("local-dev:")
    assert MASTER.get_secret_value() not in wrapper.key_id


# --- KMS wrapper (stubbed client; no network) ------------------------------------------------

ARN = "arn:aws:kms:ap-south-1:000000000000:key/00000000-0000-0000-0000-000000000000"


def test_FR_TEN_003_kms_wrapper_sends_tenant_encryption_context() -> None:
    client = boto3.client(
        "kms",
        region_name="ap-south-1",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    tenant = uuid.uuid4()
    ctx = {"tenant_id": str(tenant)}
    with Stubber(client) as stub:
        stub.add_response(
            "encrypt",
            {"CiphertextBlob": b"wrapped", "KeyId": ARN},
            {"KeyId": ARN, "Plaintext": b"d" * 32, "EncryptionContext": ctx},
        )
        stub.add_response(
            "decrypt",
            {"Plaintext": b"d" * 32, "KeyId": ARN},
            {"CiphertextBlob": b"wrapped", "KeyId": ARN, "EncryptionContext": ctx},
        )
        kms = KmsKeyWrapper(client, ARN)
        assert kms.key_id == ARN
        assert kms.wrap(b"d" * 32, tenant_id=tenant) == b"wrapped"
        assert kms.unwrap(b"wrapped", tenant_id=tenant) == b"d" * 32
        stub.assert_no_pending_responses()


def test_FR_TEN_003_kms_wrapper_maps_failures_to_crypto_error() -> None:
    client = boto3.client(
        "kms",
        region_name="ap-south-1",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    with Stubber(client) as stub:
        stub.add_client_error("decrypt", service_error_code="InvalidCiphertextException")
        with pytest.raises(CryptoError):
            KmsKeyWrapper(client, ARN).unwrap(b"x", tenant_id=uuid.uuid4())


def test_get_key_wrapper_selects_implementation() -> None:
    assert isinstance(get_key_wrapper(local_settings()), LocalDevKeyWrapper)
    with pytest.raises(ValueError, match="SOS_KMS_DATA_KEY_ARN"):
        get_key_wrapper(local_settings(key_wrapper=KeyWrapperKind.KMS, kms_data_key_arn=None))
    kms = get_key_wrapper(local_settings(key_wrapper=KeyWrapperKind.KMS, kms_data_key_arn=ARN))
    assert isinstance(kms, KmsKeyWrapper)
    assert kms.key_id == ARN


# --- tenant key generation ------------------------------------------------------------------


def test_FR_TEN_003_generate_tenant_keys(wrapper: LocalDevKeyWrapper) -> None:
    tenant = uuid.uuid4()
    wrapped_dek, wrapped_hmac = generate_tenant_keys(tenant, wrapper)
    dek = wrapper.unwrap(wrapped_dek, tenant_id=tenant)
    hmac_key = wrapper.unwrap(wrapped_hmac, tenant_id=tenant)
    assert len(dek) == 32
    assert len(hmac_key) == 32
    assert dek != hmac_key
    again = generate_tenant_keys(tenant, wrapper)
    assert wrapper.unwrap(again[0], tenant_id=tenant) != dek


def test_key_wrapper_protocol_is_satisfied(wrapper: LocalDevKeyWrapper) -> None:
    def use(w: KeyWrapper) -> str:
        return w.key_id

    assert use(wrapper).startswith("local-dev:")


# --- AEAD ciphertext format (docs/05 §9) -----------------------------------------------------


def test_SEC_012_aead_format_and_round_trip() -> None:
    dek = b"\x11" * 32
    aad = field_aad(uuid.uuid4(), "guardians", "phone", uuid.uuid4())
    blob = aead_encrypt(dek, "9876543210 తెలుగు".encode(), aad, key_version=3)
    assert blob[0] == CIPHERTEXT_VERSION == 1
    assert int.from_bytes(blob[1:3], "big") == 3 == ciphertext_key_version(blob)
    # version(1) | key_version(2) | nonce(12) | ciphertext | tag(16)
    assert len(blob) == 1 + 2 + 12 + len("9876543210 తెలుగు".encode()) + 16
    assert aead_decrypt(dek, blob, aad).decode() == "9876543210 తెలుగు"


def test_SEC_012_aead_rejects_row_swap_and_tampering() -> None:
    dek = b"\x22" * 32
    tenant, row = uuid.uuid4(), uuid.uuid4()
    aad = field_aad(tenant, "guardians", "phone", row)
    blob = aead_encrypt(dek, b"secret", aad)
    with pytest.raises(CryptoError):
        aead_decrypt(dek, blob, field_aad(tenant, "guardians", "phone", uuid.uuid4()))
    with pytest.raises(CryptoError):
        aead_decrypt(dek, blob, field_aad(uuid.uuid4(), "guardians", "phone", row))
    header_swapped = blob[:1] + (9).to_bytes(2, "big") + blob[3:]
    with pytest.raises(CryptoError):
        aead_decrypt(dek, header_swapped, aad)
    flipped = bytearray(blob)
    flipped[20] ^= 0xFF
    with pytest.raises(CryptoError):
        aead_decrypt(dek, bytes(flipped), aad)
    with pytest.raises(CryptoError):
        aead_decrypt(b"\x33" * 32, blob, aad)


def test_SEC_012_aead_rejects_bad_inputs() -> None:
    with pytest.raises(CryptoError, match="format version"):
        aead_decrypt(b"\x00" * 32, b"\x02" + b"\x00" * 40, b"")
    with pytest.raises(CryptoError, match="too short"):
        aead_decrypt(b"\x00" * 32, b"\x01\x00", b"")
    with pytest.raises(ValueError, match="32 bytes"):
        aead_encrypt(b"short", b"x", b"")
    with pytest.raises(ValueError, match="key_version"):
        aead_encrypt(b"\x00" * 32, b"x", b"", key_version=70000)


def test_field_aad_is_unambiguous() -> None:
    t, r = uuid.uuid4(), uuid.uuid4()
    assert field_aad(t, "a", "b", r) == f"{t}|a|b|{r}".encode()
    with pytest.raises(ValueError, match="'\\|'"):
        field_aad(t, "a|b", "c", r)


def test_SEC_012_reencrypt_moves_a_value_to_a_new_key_version_in_place() -> None:
    old, new = bytes(range(32)), bytes(range(1, 33))
    aad = field_aad(uuid.uuid4(), "sis.guardians", "address_ciphertext", uuid.uuid4())
    blob = aead_encrypt(old, b"Synthetic lane 3", aad, key_version=1)
    moved = reencrypt(old, new, blob, aad, key_version=2)
    assert ciphertext_key_version(moved) == 2
    assert aead_decrypt(new, moved, aad) == b"Synthetic lane 3"
    with pytest.raises(CryptoError):
        aead_decrypt(old, moved, aad)
    # The same associated data only: a value cannot be re-encrypted into another cell.
    other = field_aad(uuid.uuid4(), "sis.guardians", "address_ciphertext", uuid.uuid4())
    with pytest.raises(CryptoError):
        reencrypt(old, new, blob, other, key_version=2)
    with pytest.raises(CryptoError):
        reencrypt(new, new, blob, aad, key_version=2)
