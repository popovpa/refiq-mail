from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class ProviderSendResult:
    message_id: str | None
