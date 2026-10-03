from typing import Protocol

from app.provider.dto import EmailMessage, ProviderSendResult


class MailProvider(Protocol):
    async def send(self, message: EmailMessage) -> ProviderSendResult: ...
