from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ParamValidationError

from app.provider.classify import classify_provider_error
from app.provider.dto import EmailMessage
from app.provider.errors import PermanentProviderError, ProviderNotConfigured, RetryableProviderError
from app.provider.postbox import YandexCloudPostboxProvider, build_postbox_provider


def _client_error(code: str, status: int, message: str = "boom") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "SendEmail",
    )


def test_error_classification():
    assert isinstance(classify_provider_error(_client_error("Throttling", 429)), RetryableProviderError)
    assert isinstance(classify_provider_error(_client_error("InternalServerError", 500)), RetryableProviderError)
    assert isinstance(classify_provider_error(_client_error("MessageRejected", 400)), PermanentProviderError)
    assert isinstance(classify_provider_error(EndpointConnectionError(endpoint_url="https://postbox.example")), RetryableProviderError)
    assert isinstance(classify_provider_error(ParamValidationError(report="bad request")), PermanentProviderError)
    assert isinstance(classify_provider_error(TimeoutError("timed out")), RetryableProviderError)


def test_postbox_request_shape_and_message_id(monkeypatch):
    import asyncio

    client = MagicMock()
    client.send_email.return_value = {"MessageId": "mid-123"}
    seen: dict = {}

    def _client(service, **kwargs):
        seen["service"] = service
        seen["kwargs"] = kwargs
        return client

    monkeypatch.setattr("app.provider.postbox.boto3.client", _client)
    provider = YandexCloudPostboxProvider(
        access_key_id="access",
        secret_access_key="secret",
        region="ru-central1",
        endpoint_url="https://postbox.cloud.yandex.net",
        from_name="RefIQ",
        from_address="no-reply@refiq.ru",
        timeout_seconds=8,
    )
    result = asyncio.run(
        provider.send(EmailMessage(to="user@example.com", subject="Subject", text="Text", html="<p>Text</p>"))
    )
    assert result.message_id == "mid-123"
    assert seen["service"] == "sesv2"
    assert seen["kwargs"]["endpoint_url"] == "https://postbox.cloud.yandex.net"
    assert seen["kwargs"]["region_name"] == "ru-central1"
    assert seen["kwargs"]["aws_access_key_id"] == "access"
    kwargs = client.send_email.call_args.kwargs
    assert kwargs["FromEmailAddress"] == "RefIQ <no-reply@refiq.ru>"
    assert kwargs["Destination"] == {"ToAddresses": ["user@example.com"]}
    assert kwargs["Content"]["Simple"]["Subject"]["Data"] == "Subject"
    assert kwargs["Content"]["Simple"]["Body"]["Text"]["Data"] == "Text"
    assert kwargs["Content"]["Simple"]["Body"]["Html"]["Data"] == "<p>Text</p>"


def test_postbox_maps_client_and_botocore_errors(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("app.provider.postbox.boto3.client", lambda *args, **kwargs: client)
    provider = YandexCloudPostboxProvider(
        access_key_id="access",
        secret_access_key="secret",
        region="ru-central1",
        endpoint_url="https://postbox.cloud.yandex.net",
        from_name="RefIQ",
        from_address="no-reply@refiq.ru",
        timeout_seconds=8,
    )
    import asyncio

    client.send_email.side_effect = _client_error("Throttling", 429, "slow")
    with pytest.raises(RetryableProviderError) as retryable:
        asyncio.run(provider.send(EmailMessage(to="user@example.com", subject="s", text="t", html="h")))
    assert retryable.value.code == "Throttling"

    client.send_email.side_effect = _client_error("MessageRejected", 400, "no")
    with pytest.raises(PermanentProviderError) as permanent:
        asyncio.run(provider.send(EmailMessage(to="user@example.com", subject="s", text="t", html="h")))
    assert permanent.value.code == "MessageRejected"

    client.send_email.side_effect = EndpointConnectionError(endpoint_url="https://postbox.cloud.yandex.net")
    with pytest.raises(RetryableProviderError):
        asyncio.run(provider.send(EmailMessage(to="user@example.com", subject="s", text="t", html="h")))


def test_missing_provider_configuration_fails_fast():
    class Settings:
        YANDEX_POSTBOX_ACCESS_KEY_ID = ""
        YANDEX_POSTBOX_SECRET_ACCESS_KEY = ""
        YANDEX_POSTBOX_REGION = "ru-central1"
        YANDEX_POSTBOX_ENDPOINT = "https://postbox.cloud.yandex.net"
        EMAIL_FROM = ""
        EMAIL_FROM_NAME = ""
        EMAIL_SEND_TIMEOUT_SECONDS = 8

        @property
        def provider_configured(self) -> bool:
            return False

    with pytest.raises(ProviderNotConfigured):
        build_postbox_provider(Settings())
    with pytest.raises(ProviderNotConfigured):
        YandexCloudPostboxProvider(
            access_key_id="",
            secret_access_key="",
            region="ru-central1",
            endpoint_url="https://postbox.cloud.yandex.net",
            from_name="RefIQ",
            from_address="no-reply@refiq.ru",
            timeout_seconds=8,
        )
