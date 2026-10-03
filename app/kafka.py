from __future__ import annotations

import asyncio
import time

import structlog

from app.core.config import settings

logger = structlog.get_logger()


class AiokafkaProducer:
    def __init__(self, bootstrap_servers: list[str], client_id: str) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._client_id = client_id
        self._producer = None

    async def start(self) -> None:
        if self._producer is not None:
            return
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            client_id=self._client_id,
            acks="all",
            enable_idempotence=True,
            request_timeout_ms=10_000,
            retry_backoff_ms=200,
        )
        try:
            await producer.start()
        except Exception:
            await producer.stop()
            raise
        self._producer = producer

    async def stop(self) -> None:
        producer = self._producer
        self._producer = None
        if producer is not None:
            await producer.stop()

    async def send(self, *, topic: str, key: bytes | None, value: bytes) -> None:
        if self._producer is None:
            await self.start()
        await self._producer.send_and_wait(topic, value=value, key=key)


def consumer_kwargs() -> dict:
    return {
        "topics": (settings.MAIL_KAFKA_TOPIC,),
        "bootstrap_servers": settings.kafka_bootstrap_list,
        "group_id": settings.MAIL_KAFKA_GROUP_ID,
        "client_id": f"{settings.MAIL_KAFKA_CLIENT_ID}-consumer",
        "enable_auto_commit": False,
        "auto_offset_reset": "earliest",
    }


async def list_topic_names(timeout: float = 2.0) -> set[str]:
    from aiokafka.admin import AIOKafkaAdminClient

    client = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_list,
        client_id=f"{settings.MAIL_KAFKA_CLIENT_ID}-admin",
        request_timeout_ms=int(max(timeout, 1.0) * 1000),
    )
    await client.start()
    try:
        names = await asyncio.wait_for(client.list_topics(), timeout=timeout)
    finally:
        await client.close()
    return set(names)


async def ensure_mail_topics() -> None:
    required = {settings.MAIL_KAFKA_TOPIC, settings.MAIL_KAFKA_DLQ_TOPIC}
    deadline = time.monotonic() + settings.MAIL_KAFKA_STARTUP_TIMEOUT_SECONDS
    last_error = "kafka unavailable"
    while True:
        try:
            existing = await list_topic_names(timeout=2.0)
            missing = sorted(required - existing)
            if not missing:
                return
            last_error = "missing kafka topics: " + ", ".join(missing)
        except Exception as exc:
            last_error = f"kafka unavailable: {type(exc).__name__}"
        if time.monotonic() >= deadline:
            logger.error("mail_kafka_startup_failed", error=last_error)
            raise RuntimeError(last_error)
        await asyncio.sleep(1)
