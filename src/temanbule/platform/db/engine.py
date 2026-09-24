"""Async SQLAlchemy engine factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from temanbule.platform.settings import ConfigurationError, Settings


def create_engine(settings: Settings) -> AsyncEngine:
    url = settings.effective_database_url()
    if not url:
        raise ConfigurationError(["DATABASE_URL atau POSTGRES_PASSWORD wajib diisi"])
    return create_async_engine(
        url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
