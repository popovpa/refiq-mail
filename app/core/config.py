from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.events.crypto import MailCryptoError, decode_key


def parse_retry_delays(value: str) -> tuple[int, ...]:
    parts = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not parts or any(item < 0 for item in parts):
        raise ValueError("MAIL_RETRY_DELAYS_SECONDS must be a comma-separated list of seconds")
    return tuple(parts)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    MAIL_DATABASE_URL: str = "postgresql+asyncpg://refiq:refiq_dev_password@localhost:5432/refiq"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    MAIL_KAFKA_TOPIC: str = "mail-events"
    MAIL_KAFKA_DLQ_TOPIC: str = "mail-events.dlq"
    MAIL_KAFKA_GROUP_ID: str = "mail-service"
    MAIL_KAFKA_CLIENT_ID: str = "refiq-mail"
    MAIL_KAFKA_STARTUP_TIMEOUT_SECONDS: float = 30.0

    MAIL_EVENT_ENCRYPTION_KEY: str = ""
    MAIL_EVENT_ENCRYPTION_KEY_VERSION: str = "v1"

    YANDEX_POSTBOX_ACCESS_KEY_ID: str = ""
    YANDEX_POSTBOX_SECRET_ACCESS_KEY: str = ""
    YANDEX_POSTBOX_REGION: str = "ru-central1"
    YANDEX_POSTBOX_ENDPOINT: str = "https://postbox.cloud.yandex.net"
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = ""
    EMAIL_SEND_TIMEOUT_SECONDS: float = 8.0

    MAIL_WORKER_BATCH_SIZE: int = 10
    MAIL_WORKER_POLL_INTERVAL: float = 2.0
    MAIL_PROCESSING_LEASE_SECONDS: int = 60
    MAIL_MAX_SEND_ATTEMPTS: int = 5
    MAIL_RETRY_DELAYS_SECONDS: str = "60,300,1800,7200"
    MAIL_WORKER_SHUTDOWN_TIMEOUT_SECONDS: float = 30.0

    @model_validator(mode="after")
    def _validate_encryption_key(self) -> Settings:
        try:
            decode_key(self.MAIL_EVENT_ENCRYPTION_KEY)
        except MailCryptoError as exc:
            raise ValueError(str(exc)) from exc
        if not self.MAIL_EVENT_ENCRYPTION_KEY_VERSION.strip():
            raise ValueError("MAIL_EVENT_ENCRYPTION_KEY_VERSION is missing")
        if not self.MAIL_KAFKA_TOPIC.strip() or not self.MAIL_KAFKA_DLQ_TOPIC.strip():
            raise ValueError("mail Kafka topic configuration is missing")
        if self.MAIL_KAFKA_GROUP_ID.strip().startswith("data-ingest"):
            raise ValueError("mail consumer group must not use a data-ingest group")
        return self

    @property
    def retry_delays_seconds(self) -> tuple[int, ...]:
        return parse_retry_delays(self.MAIL_RETRY_DELAYS_SECONDS)

    @property
    def provider_configured(self) -> bool:
        return bool(
            self.YANDEX_POSTBOX_ACCESS_KEY_ID.strip()
            and self.YANDEX_POSTBOX_SECRET_ACCESS_KEY.strip()
            and self.YANDEX_POSTBOX_REGION.strip()
            and self.YANDEX_POSTBOX_ENDPOINT.strip()
            and self.EMAIL_FROM.strip()
            and self.EMAIL_FROM_NAME.strip()
        )

    @property
    def kafka_bootstrap_list(self) -> list[str]:
        return [item.strip() for item in self.KAFKA_BOOTSTRAP_SERVERS.split(",") if item.strip()]


def load_settings() -> Settings:
    return Settings()


settings = load_settings()
