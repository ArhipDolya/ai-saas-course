"""HTTP and PostgreSQL integration tests, using an isolated schema per test.

Requires TEST_DATABASE_URL pointing to a disposable database named finance_test.
Never reads DATABASE_URL or connects to the application's Neon database.
"""

import asyncio
import os
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import func, make_url, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import app
from app.expenses import TransactionData, parse_transaction, save_transaction
from app.migrate_transaction_date import migrate_transaction_date
from app.models import Base, Category, Transaction, TransactionType, User
from app.transaction_rules import today


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to a disposable PostgreSQL database")
class TransactionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = make_url(os.environ["TEST_DATABASE_URL"])
        if url.database != "finance_test":
            raise RuntimeError("Tests require a disposable database named finance_test")
        self.schema = f"test_transactions_{uuid4().hex}"
        self.admin_engine = create_async_engine(url)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(
            url, connect_args={"options": f"-c search_path={self.schema}"}
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.database_patch = patch.multiple(
            "app.database", _engine=self.engine, _session_factory=factory
        )
        self.database_patch.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database_patch.stop()
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    async def post(self, values=None, telegram_id=101):
        return await self.client.post(
            "/api/transactions",
            params={"telegram_id": telegram_id},
            json=values or {"type": "expense", "amount": "100.25", "category": "хліб"},
        )

    async def counts(self):
        async with self.engine.connect() as connection:
            return [
                (await connection.execute(select(func.count()).select_from(model))).scalar_one()
                for model in (User, Category, Transaction)
            ]

    async def test_create_read_summary_and_owner_isolation(self):
        historical_date = (today() - timedelta(days=10)).isoformat()
        result = await self.post({
            "type": "expense", "amount": "100,25", "category": "  хліб  ",
            "description": "  покупка хліба  ", "date": historical_date,
        })
        self.assertEqual(result.status_code, 201, result.text)
        expense = result.json()
        self.assertEqual(expense["amount"], "100.25")
        self.assertEqual(expense["category"], "хліб")
        self.assertEqual(expense["description"], "покупка хліба")
        self.assertEqual(expense["date"], historical_date)
        self.assertNotEqual(expense["created_at"][:10], historical_date)

        income = await self.post({"type": "income", "amount": 500, "category": "зарплата"})
        self.assertEqual(income.status_code, 201, income.text)
        self.assertEqual(income.json()["date"], today().isoformat())
        self.assertIsNone(income.json()["description"])
        rows = (await self.client.get("/api/transactions?telegram_id=101")).json()
        self.assertEqual([row["id"] for row in rows], [income.json()["id"], expense["id"]])
        self.assertEqual(rows[1], expense)
        summary = (await self.client.get("/api/summary?telegram_id=101")).json()
        self.assertEqual(summary, {"total_income": "500.00", "total_expense": "100.25", "balance": "399.75"})
        self.assertEqual((await self.client.get("/api/transactions?telegram_id=202")).json(), [])
        self.assertEqual((await self.client.get("/api/summary?telegram_id=202")).json()["balance"], "0.00")

    async def test_invalid_fields_never_write(self):
        invalid_values = {
            "type": ["transfer", "", None, 1],
            "amount": ["", "0", "-1", "1.001", "1000000000000", "10000000000", "NaN", "Infinity", True, None, {}, "1e3"],
            "category": ["", "   ", "x" * 101, None, 123, "bad\x00text"],
            "description": ["x" * 256, 123, "bad\x00text"],
            "date": ["2023-02-29", "2024-13-10", "10 вересня", "1999-12-31", "20260910", (today() + timedelta(days=1)).isoformat(), 1],
        }
        baseline = {"type": "expense", "amount": "100", "category": "хліб"}
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    response = await self.post({**baseline, field: value})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn(field, [error["loc"][-1] for error in response.json()["detail"]])
        for field in ("type", "amount", "category"):
            values = {name: value for name, value in baseline.items() if name != field}
            self.assertEqual((await self.post(values)).status_code, 422)
        for identifier in (0, -1, "abc", 9223372036854775808):
            self.assertEqual((await self.post(telegram_id=identifier)).status_code, 422)
        self.assertEqual((await self.client.post("/api/transactions", json=baseline)).status_code, 422)
        self.assertEqual((await self.post({**baseline, "user_id": 999})).status_code, 422)
        self.assertEqual(await self.counts(), [0, 0, 0])

    async def test_boundaries_and_empty_optional_fields(self):
        result = await self.post({
            "type": "income", "amount": "9999999999.99", "category": "x" * 100,
            "description": "x" * 255, "date": "2000-01-01",
        })
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()["amount"], "9999999999.99")
        for empty in (None, ""):
            response = await self.post({
                "type": "expense", "amount": "0.01", "category": "хліб",
                "description": empty, "date": empty,
            })
            self.assertEqual(response.status_code, 201, response.text)
            self.assertIsNone(response.json()["description"])
            self.assertEqual(response.json()["date"], today().isoformat())
        self.assertEqual(await self.counts(), [1, 2, 3])

    async def test_concurrent_requests_share_user_and_category(self):
        responses = await asyncio.gather(*(self.post() for _ in range(5)))
        for response in responses:
            self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(len({response.json()["id"] for response in responses}), 5)
        self.assertEqual(await self.counts(), [1, 1, 5])

    async def test_same_category_belongs_to_each_user_separately(self):
        await self.post(telegram_id=101)
        await self.post(telegram_id=202)
        self.assertEqual(await self.counts(), [2, 2, 2])

    async def test_commit_failure_rolls_back_user_category_and_transaction(self):
        async with self.engine.begin() as connection:
            await connection.execute(text("""
                CREATE FUNCTION fail_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'test commit failure'; END $$
            """))
            await connection.execute(text("""
                CREATE CONSTRAINT TRIGGER reject_transaction AFTER INSERT ON transactions
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_commit()
            """))
        response = await self.post()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("detail", response.json())
        self.assertNotIn("test commit failure", response.text)
        self.assertEqual(await self.counts(), [0, 0, 0])

    async def test_bot_and_api_use_same_persistence(self):
        parsed = parse_transaction("120 кава | ранок", command_name="/expense")
        transaction = await save_transaction(
            telegram_id=101, transaction_data=parsed, transaction_type=TransactionType.EXPENSE
        )
        await save_transaction(
            telegram_id=101,
            transaction_data=TransactionData(Decimal("500"), "зарплата", None),
            transaction_type=TransactionType.INCOME,
        )
        rows = (await self.client.get("/api/transactions?telegram_id=101")).json()
        self.assertIn(transaction.id, [row["id"] for row in rows])
        self.assertTrue(all(row["date"] == today().isoformat() for row in rows))
        self.assertEqual((await self.client.get("/api/summary?telegram_id=101")).json()["balance"], "380.00")

    async def test_delete_expense_and_income_updates_summary(self):
        expense = (await self.post()).json()
        income = (await self.post({"type": "income", "amount": "500", "category": "зарплата"})).json()
        retained = (await self.post({"type": "expense", "amount": "25", "category": "кава"})).json()

        response = await self.client.delete(f"/api/transactions/{expense['id']}?telegram_id=101")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"")
        self.assertEqual(await self.counts(), [1, 3, 2])
        self.assertEqual((await self.client.get("/api/summary?telegram_id=101")).json(), {
            "total_income": "500.00", "total_expense": "25.00", "balance": "475.00",
        })

        response = await self.client.delete(f"/api/transactions/{income['id']}?telegram_id=101")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual((await self.client.get("/api/transactions?telegram_id=101")).json(), [retained])
        self.assertEqual((await self.client.get("/api/summary?telegram_id=101")).json(), {
            "total_income": "0.00", "total_expense": "25.00", "balance": "-25.00",
        })
        self.assertEqual(await self.counts(), [1, 3, 1])

    async def test_delete_missing_other_owner_and_already_deleted(self):
        transaction = (await self.post(telegram_id=202)).json()
        path = f"/api/transactions/{transaction['id']}"
        wrong_owner = await self.client.delete(f"{path}?telegram_id=101")
        missing = await self.client.delete(f"/api/transactions/{transaction['id'] + 1}?telegram_id=202")
        self.assertEqual(wrong_owner.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(wrong_owner.json(), missing.json())
        self.assertEqual(await self.counts(), [1, 1, 1])
        self.assertEqual((await self.client.get("/api/transactions?telegram_id=202")).json(), [transaction])

        self.assertEqual((await self.client.delete(f"{path}?telegram_id=202")).status_code, 204)
        self.assertEqual((await self.client.delete(f"{path}?telegram_id=202")).status_code, 404)
        self.assertEqual(await self.counts(), [1, 1, 0])
        self.assertEqual((await self.client.get("/api/summary?telegram_id=202")).json(), {
            "total_income": "0.00", "total_expense": "0.00", "balance": "0.00",
        })

    async def test_delete_invalid_identifiers_never_changes_data(self):
        transaction = (await self.post()).json()
        for identifier in (0, -1, "abc", "1.5", 9223372036854775808):
            with self.subTest(identifier=identifier):
                response = await self.client.delete(f"/api/transactions/{identifier}?telegram_id=101")
                self.assertEqual(response.status_code, 422, response.text)
                response = await self.client.delete(f"/api/transactions/{transaction['id']}?telegram_id={identifier}")
                self.assertEqual(response.status_code, 422, response.text)
        response = await self.client.delete(f"/api/transactions/{transaction['id']}")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(await self.counts(), [1, 1, 1])

    async def test_delete_commit_failure_preserves_transaction(self):
        transaction = (await self.post()).json()
        async with self.engine.begin() as connection:
            await connection.execute(text("""
                CREATE FUNCTION fail_delete_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'test delete failure'; END $$
            """))
            await connection.execute(text("""
                CREATE CONSTRAINT TRIGGER reject_delete AFTER DELETE ON transactions
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_delete_commit()
            """))
        response = await self.client.delete(f"/api/transactions/{transaction['id']}?telegram_id=101")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("detail", response.json())
        self.assertNotIn("test delete failure", response.text)
        self.assertEqual(await self.counts(), [1, 1, 1])
        self.assertEqual((await self.client.get("/api/transactions?telegram_id=101")).json(), [transaction])

    async def test_concurrent_delete_removes_only_one_record(self):
        transaction = (await self.post()).json()
        responses = await asyncio.gather(*(
            self.client.delete(f"/api/transactions/{transaction['id']}?telegram_id=101")
            for _ in range(2)
        ))
        self.assertEqual(sorted(response.status_code for response in responses), [204, 404])
        self.assertEqual(await self.counts(), [1, 1, 0])

    async def test_migration_preserves_old_rows_and_is_repeatable(self):
        await self.post()
        async with self.engine.begin() as connection:
            await connection.execute(text("ALTER TABLE transactions DROP COLUMN transaction_date"))
            await connection.execute(text("UPDATE transactions SET created_at = '2024-02-29 12:00:00'"))
        await migrate_transaction_date()
        await migrate_transaction_date()
        async with self.engine.connect() as connection:
            row = (await connection.execute(text("SELECT amount, created_at, transaction_date FROM transactions"))).one()
            self.assertEqual(str(row.transaction_date), "2024-02-29")
            self.assertEqual(str(row.created_at), "2024-02-29 12:00:00")
            self.assertEqual(row.amount, Decimal("100.25"))
        self.assertEqual(await self.counts(), [1, 1, 1])
