"""Postgres-backed durable state: users + apps.

Redis still holds the hot path (queues, worker registrations, results, sessions
with TTL). Postgres holds anything that must survive restarts and have an owner.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, DateTime, Integer, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    apps: Mapped[list["App"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class App(Base):
    __tablename__ = "apps"
    app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255))
    gpu: Mapped[str] = mapped_column(String(64))
    autoscaler: Mapped[dict] = mapped_column(JSON)
    cpu: Mapped[int] = mapped_column(Integer, default=2)
    memory: Mapped[str] = mapped_column(String(32), default="16Gi")
    request_timeout_s: Mapped[int] = mapped_column(Integer, default=600)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    owner: Mapped[User] = relationship(back_populates="apps")


class Request(Base):
    __tablename__ = "requests"
    request_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64), ForeignKey("apps.app_id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    output: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_stream: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


_engine = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return url


async def init_db() -> None:
    global _engine, _sessionmaker
    _engine = create_async_engine(get_database_url(), pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Idempotent column adds for in-place upgrades — Base.metadata.create_all
        # only creates missing tables, not missing columns on existing ones.
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE"
        ))


async def seed_admin_user() -> None:
    """If ADMIN_USERNAME + ADMIN_PASSWORD env are set, upsert that user with
    is_admin=true. Idempotent: re-running is safe and doesn't overwrite the
    password if the user already exists."""
    import os as _os
    from .auth import hash_password
    username = _os.environ.get("ADMIN_USERNAME", "").strip()
    password = _os.environ.get("ADMIN_PASSWORD", "").strip()
    if not username or not password:
        return
    async with session_factory()() as session:
        existing = await get_user_by_username(session, username)
        if existing is None:
            session.add(User(
                username=username,
                password_hash=hash_password(password),
                is_admin=True,
            ))
            await session.commit()
        elif not existing.is_admin:
            existing.is_admin = True
            await session.commit()


async def shutdown_db() -> None:
    if _engine is not None:
        await _engine.dispose()


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("db not initialized")
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


async def list_all_apps(session: AsyncSession) -> list[App]:
    result = await session.execute(select(App))
    return list(result.scalars().all())


async def get_app(session: AsyncSession, app_id: str) -> Optional[App]:
    return await session.get(App, app_id)


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> Optional[User]:
    return await session.get(User, user_id)
