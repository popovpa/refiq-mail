import base64
import json

import pytest

from app.events.crypto import KEY_BYTES, MailCryptoError, MailEventCipher, decode_key

KEY = b"0123456789abcdef0123456789abcdef"
OTHER_KEY = b"fedcba9876543210fedcba9876543210"
PAYLOAD = {
    "recipient": {"email": "user@example.com"},
    "variables": {"confirmUrl": "https://app.example/confirm-account?token=plaintext-token", "ttlHours": 24},
}


def _cipher(key: bytes = KEY, version: str = "v1") -> MailEventCipher:
    return MailEventCipher(key, version)


def test_round_trip():
    blob = _cipher().encrypt(PAYLOAD, event_id="evt-1")
    decoded = _cipher().decrypt(
        event_id="evt-1",
        nonce=blob.nonce,
        ciphertext=blob.ciphertext,
        key_version="v1",
    )
    assert decoded == PAYLOAD


def test_nonce_changes_between_events():
    cipher = _cipher()
    first = cipher.encrypt(PAYLOAD, event_id="evt-1")
    second = cipher.encrypt(PAYLOAD, event_id="evt-1")
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


def test_tampered_ciphertext_is_rejected():
    blob = _cipher().encrypt(PAYLOAD, event_id="evt-1")
    raw = bytearray(base64.b64decode(blob.ciphertext))
    raw[-1] ^= 0x01
    tampered = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(MailCryptoError):
        _cipher().decrypt(event_id="evt-1", nonce=blob.nonce, ciphertext=tampered, key_version="v1")


def test_wrong_key_is_rejected():
    blob = _cipher().encrypt(PAYLOAD, event_id="evt-1")
    with pytest.raises(MailCryptoError):
        _cipher(OTHER_KEY).decrypt(
            event_id="evt-1",
            nonce=blob.nonce,
            ciphertext=blob.ciphertext,
            key_version="v1",
        )


def test_invalid_base64_key_and_payload_are_rejected():
    with pytest.raises(MailCryptoError):
        decode_key("not base64!!!")
    with pytest.raises(MailCryptoError):
        decode_key(base64.b64encode(b"short").decode())
    cipher = _cipher()
    with pytest.raises(MailCryptoError):
        cipher.decrypt(event_id="evt-1", nonce="@@@", ciphertext="@@@", key_version="v1")


def test_contract_vector_is_stable():
    blob = _cipher().encrypt_with_nonce(PAYLOAD, event_id="evt-contract", nonce=b"\x22" * 12)
    assert len(base64.b64decode(blob.nonce)) == 12
    assert len(KEY) == KEY_BYTES
    decoded = _cipher().decrypt(
        event_id="evt-contract",
        nonce=blob.nonce,
        ciphertext=blob.ciphertext,
        key_version="v1",
    )
    assert decoded["recipient"]["email"] == "user@example.com"
    serialized = json.dumps(
        {"nonce": blob.nonce, "ciphertext": blob.ciphertext, "algorithm": blob.algorithm},
        sort_keys=True,
    )
    assert "user@example.com" not in serialized
    assert "plaintext-token" not in serialized
    assert "confirmUrl" not in serialized


def test_swapped_event_id_is_rejected():
    blob = _cipher().encrypt(PAYLOAD, event_id="evt-1")
    with pytest.raises(MailCryptoError):
        _cipher().decrypt(event_id="evt-2", nonce=blob.nonce, ciphertext=blob.ciphertext, key_version="v1")
