"""AES-256-GCM encryption for mail command payloads.

Keep this module identical in api/app/mail_events/crypto.py and mail/app/events/crypto.py.
Ciphertext is bound to event_id with AES-GCM associated data.
A new 12-byte nonce is generated for every event.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ALGORITHM = "AES-256-GCM"
NONCE_BYTES = 12
KEY_BYTES = 32


class MailCryptoError(Exception):
    """Invalid key material or a failed authenticated decrypt."""


def decode_key(value: str) -> bytes:
    if not value or not value.strip():
        raise MailCryptoError("MAIL_EVENT_ENCRYPTION_KEY is missing")
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except Exception as exc:
        raise MailCryptoError("MAIL_EVENT_ENCRYPTION_KEY is not valid base64") from exc
    if len(raw) != KEY_BYTES:
        raise MailCryptoError("MAIL_EVENT_ENCRYPTION_KEY must decode to 32 bytes")
    return raw


def _aad(event_id: str) -> bytes:
    if not event_id:
        raise MailCryptoError("event_id is required for mail payload encryption")
    return event_id.encode("utf-8")


def _plaintext(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class EncryptedBlob:
    algorithm: str
    key_version: str
    nonce: str
    ciphertext: str


class MailEventCipher:
    def __init__(self, key: bytes, key_version: str) -> None:
        if len(key) != KEY_BYTES:
            raise MailCryptoError("encryption key must be 32 bytes")
        if not key_version or not str(key_version).strip():
            raise MailCryptoError("encryption key version is missing")
        self._key = key
        self._aes = AESGCM(key)
        self.key_version = str(key_version)

    def encrypt(self, payload: dict, *, event_id: str) -> EncryptedBlob:
        return self.encrypt_with_nonce(payload, event_id=event_id, nonce=os.urandom(NONCE_BYTES))

    def encrypt_with_nonce(self, payload: dict, *, event_id: str, nonce: bytes) -> EncryptedBlob:
        if len(nonce) != NONCE_BYTES:
            raise MailCryptoError("encryption nonce must be 12 bytes")
        ciphertext = self._aes.encrypt(nonce, _plaintext(payload), _aad(event_id))
        return EncryptedBlob(
            algorithm=ALGORITHM,
            key_version=self.key_version,
            nonce=base64.b64encode(nonce).decode("ascii"),
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
        )

    def decrypt(
        self,
        *,
        event_id: str,
        nonce: str,
        ciphertext: str,
        key_version: str | None = None,
    ) -> dict:
        if key_version is not None and key_version != self.key_version:
            raise MailCryptoError("unsupported encryption key version")
        try:
            nonce_raw = base64.b64decode(nonce, validate=True)
            data = base64.b64decode(ciphertext, validate=True)
        except Exception as exc:
            raise MailCryptoError("encrypted payload is not valid base64") from exc
        if len(nonce_raw) != NONCE_BYTES:
            raise MailCryptoError("encryption nonce must be 12 bytes")
        try:
            plain = self._aes.decrypt(nonce_raw, data, _aad(event_id))
        except Exception as exc:
            raise MailCryptoError("mail payload authentication failed") from exc
        try:
            decoded = json.loads(plain.decode("utf-8"))
        except Exception as exc:
            raise MailCryptoError("decrypted mail payload is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise MailCryptoError("decrypted mail payload must be an object")
        return decoded
