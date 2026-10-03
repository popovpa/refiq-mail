from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.events.crypto import MailCryptoError, MailEventCipher
from app.templates.registry import TemplateContractError, missing_variables

SCHEMA_VERSION = 1
EVENT_TYPE = "MAIL_SEND_REQUESTED"


class PermanentIngestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TemporaryIngestError(Exception):
    pass


class EncryptionMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    algorithm: str
    keyVersion: str
    nonce: str


class MailSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    id: str


class MailEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schemaVersion: int
    eventId: str = Field(min_length=1, max_length=64)
    eventType: str
    occurredAt: str
    templateCode: str = Field(min_length=1, max_length=80)
    templateVersion: int
    idempotencyKey: str = Field(min_length=1, max_length=160)
    source: MailSource
    requestId: str | None = Field(default=None, max_length=64)
    correlationId: str | None = Field(default=None, max_length=64)
    encryption: EncryptionMeta
    encryptedPayload: str


def utc_timestamp(value: datetime | None = None) -> str:
    current = (value or datetime.now(timezone.utc)).astimezone(timezone.utc)
    millis = current.microsecond // 1000
    return current.strftime("%Y-%m-%dT%H:%M:%S.") + f"{millis:03d}Z"


def parse_envelope(raw: bytes) -> MailEnvelope:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise PermanentIngestError("INVALID_JSON", "mail event is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise PermanentIngestError("INVALID_JSON", "mail event must be a JSON object")
    version = decoded.get("schemaVersion")
    if version != SCHEMA_VERSION:
        raise PermanentIngestError("UNSUPPORTED_SCHEMA", "unsupported mail event schemaVersion")
    if decoded.get("eventType") != EVENT_TYPE:
        raise PermanentIngestError("UNSUPPORTED_SCHEMA", "unsupported mail eventType")
    try:
        envelope = MailEnvelope.model_validate(decoded)
    except ValidationError as exc:
        raise PermanentIngestError("INVALID_PAYLOAD", "mail envelope failed validation") from exc
    if envelope.encryption.algorithm != "AES-256-GCM":
        raise PermanentIngestError("INVALID_PAYLOAD", "unsupported mail encryption algorithm")
    if not envelope.encryption.nonce or not envelope.encryptedPayload:
        raise PermanentIngestError("INVALID_PAYLOAD", "mail encryption metadata is incomplete")
    if len(envelope.source.type) > 50 or len(envelope.source.id) > 64:
        raise PermanentIngestError("INVALID_PAYLOAD", "mail source is invalid")
    return envelope


def decrypt_variables(envelope: MailEnvelope, cipher: MailEventCipher) -> dict:
    if envelope.encryption.keyVersion != cipher.key_version:
        raise PermanentIngestError("DECRYPTION_FAILED", "unsupported encryption key version")
    try:
        payload = cipher.decrypt(
            event_id=envelope.eventId,
            nonce=envelope.encryption.nonce,
            ciphertext=envelope.encryptedPayload,
            key_version=envelope.encryption.keyVersion,
        )
    except MailCryptoError as exc:
        raise PermanentIngestError("DECRYPTION_FAILED", "mail payload could not be decrypted") from exc
    recipient = payload.get("recipient")
    variables = payload.get("variables")
    if not isinstance(recipient, dict) or not isinstance(variables, dict):
        raise PermanentIngestError("INVALID_PAYLOAD", "mail payload is missing recipient or variables")
    email = recipient.get("email")
    if not isinstance(email, str) or "@" not in email or len(email) > 320:
        raise PermanentIngestError("INVALID_PAYLOAD", "mail payload recipient is invalid")
    try:
        missing = missing_variables(envelope.templateCode, envelope.templateVersion, variables)
    except TemplateContractError as exc:
        raise PermanentIngestError(exc.code, exc.message) from exc
    if missing:
        raise PermanentIngestError("MISSING_VARIABLES", "mail template is missing required variables")
    return payload
