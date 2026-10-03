from httpx import ASGITransport, AsyncClient
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_health_does_not_touch_provider(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("provider must not be called")

    monkeypatch.setattr("app.provider.postbox.YandexCloudPostboxProvider.send", _boom)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://mail") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_requires_database_encryption_config_and_kafka(monkeypatch):
    async def ready_deps():
        return True

    monkeypatch.setattr("app.health._database_ok", ready_deps)
    monkeypatch.setattr("app.health._kafka_ok", ready_deps)
    monkeypatch.setattr("app.health.settings.YANDEX_POSTBOX_ACCESS_KEY_ID", "access")
    monkeypatch.setattr("app.health.settings.YANDEX_POSTBOX_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr("app.health.settings.EMAIL_FROM", "no-reply@refiq.ru")
    monkeypatch.setattr("app.health.settings.EMAIL_FROM_NAME", "RefIQ")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://mail") as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}

    monkeypatch.setattr("app.health.settings.YANDEX_POSTBOX_ACCESS_KEY_ID", "")
    async with AsyncClient(transport=transport, base_url="http://mail") as client:
        unavailable = await client.get("/ready")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"status": "unavailable"}
