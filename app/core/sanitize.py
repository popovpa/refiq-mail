from __future__ import annotations

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_LIMIT = 500

_FORBIDDEN_LOG_KEYS = {
    "email",
    "recipient",
    "token",
    "confirm_url",
    "reset_url",
    "confirmurl",
    "reseturl",
    "html",
    "text",
    "body",
    "password",
    "secret",
    "authorization",
    "encrypted_payload",
    "encryptedpayload",
    "ciphertext",
    "encryption_key",
    "variables",
}


def sanitize_error(message: str | None, *, limit: int = _LIMIT) -> str:
    text = _EMAIL.sub("[redacted-email]", message or "")
    text = _URL.sub("[redacted-url]", text)
    text = text.replace("\n", " ").strip()
    return text[:limit]


def email_domain(address: str) -> str:
    if "@" not in address:
        return "unknown"
    return address.rsplit("@", 1)[-1]


def drop_sensitive_log_keys(_logger, _method, event_dict: dict) -> dict:
    for key in list(event_dict):
        normalized = str(key).replace("-", "_").lower()
        if (
            normalized in _FORBIDDEN_LOG_KEYS
            or "token" in normalized
            or "secret" in normalized
            or "payload" in normalized
            or normalized.endswith("_url")
        ):
            event_dict.pop(key, None)
    error = event_dict.get("error")
    if isinstance(error, str):
        event_dict["error"] = sanitize_error(error)
    error_message = event_dict.get("error_message")
    if isinstance(error_message, str):
        event_dict["error_message"] = sanitize_error(error_message)
    return event_dict
