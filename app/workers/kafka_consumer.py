from __future__ import annotations

import asyncio
import signal

import structlog

from app.consumer.ingest import IncomingRecord, handle_kafka_record
from app.core.config import settings
from app.core.database import async_session_factory, engine
from app.core.logging import configure_logging
from app.events.crypto import MailEventCipher, decode_key
from app.kafka import AiokafkaProducer, consumer_kwargs, ensure_mail_topics

logger = structlog.get_logger()


def _request_stop(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_args: stop.set())


async def _run() -> None:
    configure_logging()
    cipher = MailEventCipher(
        decode_key(settings.MAIL_EVENT_ENCRYPTION_KEY),
        settings.MAIL_EVENT_ENCRYPTION_KEY_VERSION,
    )
    if not settings.MAIL_DATABASE_URL.strip():
        raise RuntimeError("MAIL_DATABASE_URL is missing")
    await ensure_mail_topics()

    from aiokafka import AIOKafkaConsumer

    kwargs = consumer_kwargs()
    topics = kwargs.pop("topics")
    consumer = AIOKafkaConsumer(*topics, **kwargs)
    producer = AiokafkaProducer(
        settings.kafka_bootstrap_list,
        f"{settings.MAIL_KAFKA_CLIENT_ID}-dlq",
    )
    await consumer.start()
    await producer.start()
    stop = asyncio.Event()
    _request_stop(stop)
    logger.info(
        "mail_consumer_started",
        topic=settings.MAIL_KAFKA_TOPIC,
        group_id=settings.MAIL_KAFKA_GROUP_ID,
    )

    class _Offsets:
        async def commit(self) -> None:
            await consumer.commit()

    class _Dlq:
        async def publish(self, *, key: bytes | None, value: bytes) -> None:
            await producer.send(topic=settings.MAIL_KAFKA_DLQ_TOPIC, key=key, value=value)

    try:
        while not stop.is_set():
            try:
                message = await asyncio.wait_for(consumer.getone(), timeout=1.0)
            except TimeoutError:
                continue
            record = IncomingRecord(
                topic=message.topic,
                partition=message.partition,
                offset=message.offset,
                key=message.key,
                value=message.value,
            )
            logger.info(
                "mail_kafka_message_received",
                topic=record.topic,
                partition=record.partition,
                offset=record.offset,
            )
            while not stop.is_set():
                try:
                    outcome = await handle_kafka_record(
                        async_session_factory,
                        record,
                        cipher=cipher,
                        offsets=_Offsets(),
                        dlq=_Dlq(),
                    )
                except Exception as exc:
                    logger.warning(
                        "mail_consumer_temporary_failure",
                        error_type=type(exc).__name__,
                        partition=message.partition,
                        offset=message.offset,
                    )
                    outcome = "retry"
                if outcome != "retry":
                    logger.info(
                        "mail_kafka_message_processed",
                        topic=record.topic,
                        partition=record.partition,
                        offset=record.offset,
                        outcome=outcome,
                    )
                    break
                logger.warning(
                    "mail_consumer_retry",
                    partition=message.partition,
                    offset=message.offset,
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except TimeoutError:
                    pass
    finally:
        logger.info("mail_consumer_stopping")
        await consumer.stop()
        await producer.stop()
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
