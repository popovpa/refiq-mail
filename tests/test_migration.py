import asyncio
import base64
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[1]
ADMIN_DSN = os.environ.get(
    "MAIL_MIGRATION_ADMIN_URL",
    "postgresql://refiq:refiq_dev_password@localhost:5432/postgres",
)
DATABASE_URL = os.environ.get(
    "MAIL_MIGRATION_DATABASE_URL",
    "postgresql+asyncpg://refiq:refiq_dev_password@localhost:5432/refiq_mail_migtest",
)
DATABASE_DSN = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


def test_mail_alembic_uses_its_own_version_table():
    env = (ROOT / "alembic" / "env.py").read_text()
    migration = (ROOT / "alembic" / "versions" / "001_mail_tables.py").read_text()
    assert 'VERSION_TABLE = "alembic_version_mail"' in env
    assert "mail_message" in migration
    assert "mail_log" in migration
    assert "uq_mail_message_event_id" in migration
    assert "uq_mail_message_idempotency_key" in migration
    assert "ix_mail_message_status_next_attempt" in migration
    assert "ix_mail_message_lease_until" in migration
    assert "ix_mail_log_message_created" in migration
    assert "fk_mail_log_mail_message_id" in migration


async def _postgres_available() -> bool:
    try:
        conn = await asyncpg.connect(ADMIN_DSN)
    except Exception:
        return False
    await conn.close()
    return True


@pytest.mark.asyncio
async def test_alembic_upgrade_head_creates_mail_tables():
    if not await _postgres_available():
        pytest.skip("postgres is not available for mail migration test")

    admin = await asyncpg.connect(ADMIN_DSN)
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = 'refiq_mail_migtest' AND pid <> pg_backend_pid()"
        )
        await admin.execute("DROP DATABASE IF EXISTS refiq_mail_migtest")
        await admin.execute("CREATE DATABASE refiq_mail_migtest")
    finally:
        await admin.close()

    env = os.environ.copy()
    env["MAIL_DATABASE_URL"] = DATABASE_URL
    env["MAIL_EVENT_ENCRYPTION_KEY"] = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    env["MAIL_EVENT_ENCRYPTION_KEY_VERSION"] = "v1"
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        conn = await asyncpg.connect(DATABASE_DSN)
        try:
            tables = {row["tablename"] for row in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
            assert "mail_message" in tables
            assert "mail_log" in tables
            assert "alembic_version_mail" in tables
            assert "alembic_version" not in tables
            indexes = {row["indexname"] for row in await conn.fetch("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
            assert "uq_mail_message_event_id" in indexes or await _constraint(conn, "uq_mail_message_event_id")
            assert await _constraint(conn, "uq_mail_message_event_id")
            assert await _constraint(conn, "uq_mail_message_idempotency_key")
            assert "ix_mail_message_status_next_attempt" in indexes
            assert "ix_mail_message_lease_until" in indexes
            assert "ix_mail_message_created_at" in indexes
            assert "ix_mail_log_message_created" in indexes
            assert "ix_mail_log_event_type" in indexes
            assert "ix_mail_log_created_at" in indexes
            fk = await conn.fetchval(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conname = 'fk_mail_log_mail_message_id'"
            )
            assert fk == "mail_message"
        finally:
            await conn.close()
        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "base"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        admin = await asyncpg.connect(ADMIN_DSN)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = 'refiq_mail_migtest' AND pid <> pg_backend_pid()"
            )
            await admin.execute("DROP DATABASE IF EXISTS refiq_mail_migtest")
        finally:
            await admin.close()


async def _constraint(conn, name: str) -> bool:
    found = await conn.fetchval("SELECT 1 FROM pg_constraint WHERE conname = $1", name)
    return bool(found)
