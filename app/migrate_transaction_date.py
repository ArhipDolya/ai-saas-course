"""Add operation dates without changing timestamps or removing existing data."""

import asyncio
import logging

from sqlalchemy import text

from app.database import dispose_database, get_engine, initialize_database

ADD_TRANSACTION_DATE = (
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS transaction_date DATE",
    "UPDATE transactions SET transaction_date = created_at::date "
    "WHERE transaction_date IS NULL",
    "ALTER TABLE transactions ALTER COLUMN transaction_date "
    "SET DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Kyiv')::date",
    "ALTER TABLE transactions ALTER COLUMN transaction_date SET NOT NULL",
)


async def migrate_transaction_date() -> None:
    await initialize_database()
    async with get_engine().begin() as connection:
        await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        for statement in ADD_TRANSACTION_DATE:
            await connection.execute(text(statement))


async def main() -> None:
    try:
        await migrate_transaction_date()
    except Exception as error:
        logging.error("Date migration failed: error_type=%s", type(error).__name__)
        raise SystemExit(1) from None
    finally:
        await dispose_database()
    logging.info("Transaction dates are ready; existing records preserved.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
