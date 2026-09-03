import asyncio
import logging

from sqlalchemy import text

from app.database import dispose_database, get_engine, initialize_database

DROP_EXTRA_COLUMNS = (
    "ALTER TABLE users "
    "DROP COLUMN IF EXISTS username, "
    "DROP COLUMN IF EXISTS first_name, "
    "DROP COLUMN IF EXISTS last_name",
    "ALTER TABLE categories "
    "DROP COLUMN IF EXISTS normalized_name",
    "ALTER TABLE transactions "
    "DROP COLUMN IF EXISTS description",
)

MERGE_DUPLICATE_CATEGORIES = """
WITH duplicates AS (
    SELECT user_id, name, MIN(id) AS kept_id
    FROM categories
    GROUP BY user_id, name
    HAVING COUNT(*) > 1
), reassigned_transactions AS (
    UPDATE transactions AS transaction
    SET category_id = duplicate.kept_id
    FROM duplicates AS duplicate
    JOIN categories AS category
        ON category.user_id = duplicate.user_id
        AND category.name = duplicate.name
    WHERE transaction.category_id = category.id
        AND transaction.category_id <> duplicate.kept_id
)
DELETE FROM categories AS category
USING duplicates AS duplicate
WHERE category.user_id = duplicate.user_id
    AND category.name = duplicate.name
    AND category.id <> duplicate.kept_id
"""

DROP_CATEGORY_TYPE = "ALTER TABLE categories DROP COLUMN IF EXISTS type"

ADD_CATEGORY_UNIQUENESS = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'categories'::regclass
            AND conname = 'uq_categories_user_name'
    ) THEN
        ALTER TABLE categories
        ADD CONSTRAINT uq_categories_user_name UNIQUE (user_id, name);
    END IF;
END $$;
"""

CONVERT_CREATED_AT_TO_TIMESTAMP = """
DO $$
DECLARE
    target_table text;
BEGIN
    FOREACH target_table IN ARRAY ARRAY['users', 'categories', 'transactions']
    LOOP
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
                AND information_schema.columns.table_name = target_table
                AND column_name = 'created_at'
                AND data_type = 'timestamp with time zone'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I ALTER COLUMN created_at TYPE timestamp '
                'USING created_at AT TIME ZONE ''UTC''',
                target_table
            );
        END IF;
    END LOOP;
END $$;
"""


async def migrate_schema() -> None:
    """Приводить існуючу схему Neon до контракту трьох таблиць."""
    await initialize_database()

    async with get_engine().begin() as connection:
        for statement in DROP_EXTRA_COLUMNS:
            await connection.execute(text(statement))
        await connection.execute(text(MERGE_DUPLICATE_CATEGORIES))
        await connection.execute(text(DROP_CATEGORY_TYPE))
        await connection.execute(text(ADD_CATEGORY_UNIQUENESS))
        await connection.execute(text(CONVERT_CREATED_AT_TO_TIMESTAMP))


async def main() -> None:
    try:
        await migrate_schema()
    finally:
        await dispose_database()

    logging.info("Схему бази даних приведено до контракту expense-бота.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(main())
