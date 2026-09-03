import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONNECTION_TIMEOUT_SECONDS = 10

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_database_url() -> URL:
    """Повертає PostgreSQL URL для драйвера psycopg 3."""
    load_dotenv(PROJECT_ROOT / ".env")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL не задано у .env")

    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL має використовувати схему postgresql://")

    return url.set(drivername="postgresql+psycopg")


def get_engine() -> AsyncEngine:
    """Створює один асинхронний Engine на час роботи застосунку."""
    global _engine

    if _engine is None:
        _engine = create_async_engine(
            get_database_url(),
            connect_args={"connect_timeout": CONNECTION_TIMEOUT_SECONDS},
            pool_pre_ping=True,
        )

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Повертає фабрику короткоживучих сесій для операцій з БД."""
    global _session_factory

    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)

    return _session_factory


async def initialize_database() -> None:
    """Створює відсутні таблиці без зміни вже наявних."""
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def dispose_database() -> None:
    """Закриває пул підключень під час зупинки застосунку."""
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
