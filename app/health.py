from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session_factory
from app.events.crypto import MailCryptoError, decode_key


async def _database_ok() -> bool:
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _encryption_ok() -> bool:
    try:
        decode_key(settings.MAIL_EVENT_ENCRYPTION_KEY)
        return bool(settings.MAIL_EVENT_ENCRYPTION_KEY_VERSION.strip())
    except MailCryptoError:
        return False


async def _kafka_ok() -> bool:
    try:
        from app.kafka import list_topic_names

        names = await asyncio.wait_for(list_topic_names(timeout=1.0), timeout=1.5)
    except Exception:
        return False
    return settings.MAIL_KAFKA_TOPIC in names and settings.MAIL_KAFKA_DLQ_TOPIC in names


async def readiness() -> dict[str, bool]:
    database, kafka = await asyncio.gather(_database_ok(), _kafka_ok())
    checks = {
        "database": database,
        "encryption": _encryption_ok(),
        "configuration": settings.provider_configured,
        "kafka": kafka,
    }
    checks["ready"] = all(checks.values())
    return checks
