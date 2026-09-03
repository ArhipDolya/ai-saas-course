import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.database import CONNECTION_TIMEOUT_SECONDS, get_database_url


def check_connection() -> None:
    """Відкриває реальне з'єднання з БД і виконує простий SQL-запит."""
    engine: Engine | None = None
    try:
        engine = create_engine(
            get_database_url(),
            connect_args={"connect_timeout": CONNECTION_TIMEOUT_SECONDS},
            poolclass=NullPool,
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        if engine is not None:
            engine.dispose()


def main() -> int:
    try:
        check_connection()
    except (SQLAlchemyError, ValueError) as error:
        logging.error(
            "Не вдалося підключитися до бази даних: %s",
            error.__class__.__name__,
        )
        return 1

    logging.info("Підключення до бази даних успішне.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    raise SystemExit(main())
