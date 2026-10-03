from __future__ import annotations

import asyncio
import signal

import structlog

from app.core.config import settings
from app.core.database import async_session_factory, engine
from app.core.logging import configure_logging
from app.events.crypto import MailEventCipher, decode_key
from app.provider.errors import ProviderNotConfigured
from app.provider.postbox import build_postbox_provider
from app.workers.delivery import MailWorker

logger = structlog.get_logger()


def _request_stop(worker: MailWorker) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_args: worker.request_stop())


async def _run() -> None:
    configure_logging()
    if not settings.MAIL_DATABASE_URL.strip():
        raise RuntimeError("MAIL_DATABASE_URL is missing")
    try:
        decode_key(settings.MAIL_EVENT_ENCRYPTION_KEY)
        provider = build_postbox_provider(settings)
    except ProviderNotConfigured as exc:
        raise RuntimeError("mail provider is not configured") from exc
    cipher = MailEventCipher(decode_key(settings.MAIL_EVENT_ENCRYPTION_KEY), settings.MAIL_EVENT_ENCRYPTION_KEY_VERSION)
    worker = MailWorker(
        async_session_factory,
        provider,
        cipher,
        batch_size=settings.MAIL_WORKER_BATCH_SIZE,
        lease_seconds=settings.MAIL_PROCESSING_LEASE_SECONDS,
        max_attempts=settings.MAIL_MAX_SEND_ATTEMPTS,
        delays=settings.retry_delays_seconds,
        send_timeout=settings.EMAIL_SEND_TIMEOUT_SECONDS,
    )
    _request_stop(worker)
    logger.info("mail_worker_started", worker_id=worker.worker_id)
    try:
        while not worker._stop.is_set():
            try:
                await worker.run_once()
            except Exception as exc:
                logger.warning("mail_worker_cycle_failed", error_type=type(exc).__name__)
            try:
                await asyncio.wait_for(worker._stop.wait(), timeout=settings.MAIL_WORKER_POLL_INTERVAL)
            except TimeoutError:
                pass
    finally:
        logger.info("mail_worker_stopping", worker_id=worker.worker_id)
        await engine.dispose()


def main() -> None:
    try:
        asyncio.run(_run())
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
