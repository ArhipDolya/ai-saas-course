import asyncio
import logging
import os

from dotenv import load_dotenv

from app.database import check_database_connection, create_database_engine


async def main() -> None:
    load_dotenv()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env.")

    engine = create_database_engine(database_url)
    try:
        await check_database_connection(engine)
        logging.info("Database connection OK")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
