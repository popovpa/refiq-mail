import base64
import os

if not os.environ.get("MAIL_EVENT_ENCRYPTION_KEY"):
    os.environ["MAIL_EVENT_ENCRYPTION_KEY"] = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
os.environ.setdefault("MAIL_EVENT_ENCRYPTION_KEY_VERSION", "v1")
os.environ.setdefault("MAIL_DATABASE_URL", "sqlite+aiosqlite:///./mail_test.db")
os.environ.setdefault("YANDEX_POSTBOX_ACCESS_KEY_ID", "")
os.environ.setdefault("YANDEX_POSTBOX_SECRET_ACCESS_KEY", "")
os.environ.setdefault("EMAIL_FROM", "")
os.environ.setdefault("EMAIL_FROM_NAME", "")

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.persistence.models import MailLog, MailMessage  # noqa: F401

TEST_DATABASE_URL = "sqlite+aiosqlite:///./mail_test.db"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_fk(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def session_factory():
    return TestingSessionLocal
