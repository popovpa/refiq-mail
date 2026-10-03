from __future__ import annotations

import asyncio

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.provider.classify import classify_provider_error
from app.provider.dto import EmailMessage, ProviderSendResult
from app.provider.errors import ProviderNotConfigured


class YandexCloudPostboxProvider:
    """Sends mail through Yandex Cloud Postbox Amazon-compatible API (not SMTP).

    The call is at-least-once from the client's point of view: Postbox may accept
    the message and the client may still time out before observing MessageId.
    """

    def __init__(
        self,
        *,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        endpoint_url: str,
        from_name: str,
        from_address: str,
        timeout_seconds: float,
    ) -> None:
        if not (access_key_id and secret_access_key and region and endpoint_url and from_name and from_address):
            raise ProviderNotConfigured("Yandex Cloud Postbox is not configured")
        self._from = f"{from_name} <{from_address}>"
        timeout = max(1.0, min(float(timeout_seconds), 15.0))
        self._client = boto3.client(
            "sesv2",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                connect_timeout=min(3.0, timeout),
                read_timeout=timeout,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        )

    async def send(self, message: EmailMessage) -> ProviderSendResult:
        try:
            return await asyncio.to_thread(self._send, message)
        except (ClientError, BotoCoreError) as exc:
            raise classify_provider_error(exc) from exc

    def _send(self, message: EmailMessage) -> ProviderSendResult:
        try:
            response = self._client.send_email(
                FromEmailAddress=self._from,
                Destination={"ToAddresses": [message.to]},
                Content={
                    "Simple": {
                        "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                        "Body": {
                            "Text": {"Data": message.text, "Charset": "UTF-8"},
                            "Html": {"Data": message.html, "Charset": "UTF-8"},
                        },
                    }
                },
            )
        except (ClientError, BotoCoreError):
            raise
        message_id = None
        if isinstance(response, dict):
            raw_id = response.get("MessageId")
            if isinstance(raw_id, str) and raw_id.strip():
                message_id = raw_id.strip()
        return ProviderSendResult(message_id=message_id)


def build_postbox_provider(settings) -> YandexCloudPostboxProvider:
    if not settings.provider_configured:
        raise ProviderNotConfigured("Yandex Cloud Postbox is not configured")
    return YandexCloudPostboxProvider(
        access_key_id=settings.YANDEX_POSTBOX_ACCESS_KEY_ID,
        secret_access_key=settings.YANDEX_POSTBOX_SECRET_ACCESS_KEY,
        region=settings.YANDEX_POSTBOX_REGION,
        endpoint_url=settings.YANDEX_POSTBOX_ENDPOINT,
        from_name=settings.EMAIL_FROM_NAME,
        from_address=settings.EMAIL_FROM,
        timeout_seconds=settings.EMAIL_SEND_TIMEOUT_SECONDS,
    )
