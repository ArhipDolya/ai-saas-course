from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Base


def prepare_database_url(database_url: str) -> tuple[str, dict[str, object]]:
    connect_args: dict[str, object] = {}
    prepared_url = database_url

    if prepared_url.startswith("postgres://"):
        prepared_url = prepared_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif prepared_url.startswith("postgresql://"):
        prepared_url = prepared_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parts = urlsplit(prepared_url)
    query_params = dict(parse_qsl(parts.query, keep_blank_values=True))

    sslmode = query_params.pop("sslmode", None)
    query_params.pop("channel_binding", None)

    if sslmode == "require":
        connect_args["ssl"] = True

    prepared_url = urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query_params),
            parts.fragment,
        )
    )

    return prepared_url, connect_args


def create_database_engine(database_url: str) -> AsyncEngine:
    prepared_url, connect_args = prepare_database_url(database_url)

    return create_async_engine(
        prepared_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_database_connection(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("select 1"))


async def create_database_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text("alter table transactions add column if not exists description varchar(500)")
        )
        await connection.execute(
            text(
                """
                do $$
                begin
                    if exists (
                        select 1 from pg_type where typname = 'pending_action_status'
                    ) then
                        alter type pending_action_status add value if not exists 'confirmed';
                        alter type pending_action_status add value if not exists 'canceled';
                    end if;
                end $$;
                """
            )
        )
