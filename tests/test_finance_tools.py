from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase, TestCase

from app.finance_tools import (
    FinanceTools,
    InvalidToolArgumentsError,
    require_limit,
    resolve_period,
)
from app.models import Category, Transaction, TransactionType


class FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class FakeSession:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self._rows)


class FinancePeriodTests(TestCase):
    def test_resolve_explicit_month(self) -> None:
        period = resolve_period(
            "2026-07",
            now=datetime(2026, 7, 23, tzinfo=timezone.utc),
        )

        self.assertEqual(period.as_json(), {"key": "2026-07", "from": "2026-07-01", "to": "2026-08-01"})

    def test_resolve_previous_month(self) -> None:
        period = resolve_period(
            "previous_month",
            now=datetime(2026, 1, 8, tzinfo=timezone.utc),
        )

        self.assertEqual(period.as_json(), {"key": "previous_month", "from": "2025-12-01", "to": "2026-01-01"})

    def test_rejects_unknown_period_and_invalid_limit(self) -> None:
        with self.assertRaises(InvalidToolArgumentsError):
            resolve_period("липень")
        with self.assertRaises(InvalidToolArgumentsError):
            require_limit(21)


class FinanceToolsTests(IsolatedAsyncioTestCase):
    period = resolve_period("2026-07", now=datetime(2026, 7, 23, tzinfo=timezone.utc))

    async def test_summary_returns_income_expense_balance_and_count(self) -> None:
        session = FakeSession(
            [
                (TransactionType.income, Decimal("1000"), 1),
                (TransactionType.expense, Decimal("120.5"), 2),
            ]
        )
        tools = FinanceTools(lambda: session)

        result = await tools.get_transactions_summary(self.period)

        self.assertEqual(result["total_income"], "1000.00")
        self.assertEqual(result["total_expense"], "120.50")
        self.assertEqual(result["balance"], "879.50")
        self.assertEqual(result["transactions_count"], 3)
        self.assertEqual(len(session.statements), 1)

    async def test_category_totals_return_expense_categories(self) -> None:
        session = FakeSession(
            [
                ("Кава", Decimal("80"), 2),
                ("Транспорт", Decimal("40"), 1),
            ]
        )
        tools = FinanceTools(lambda: session)

        result = await tools.get_category_totals(self.period)

        self.assertEqual(result["transactions_count"], 3)
        self.assertEqual(result["categories"][0]["category"], "Кава")
        self.assertEqual(result["categories"][0]["total_expense"], "80.00")

    async def test_top_expenses_return_limited_expense_rows(self) -> None:
        transaction = Transaction(
            amount=Decimal("450"),
            description="Ноутбук",
            created_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        )
        category = Category(name="Техніка", type=TransactionType.expense, user_id=1)
        session = FakeSession([(transaction, category)])
        tools = FinanceTools(lambda: session)

        result = await tools.get_top_expenses(self.period, limit=3)

        self.assertEqual(result["transactions_count"], 1)
        self.assertEqual(result["expenses"][0]["category"], "Техніка")
        self.assertEqual(result["expenses"][0]["amount"], "450.00")

    async def test_recent_transactions_return_income_and_expense_rows(self) -> None:
        transaction = Transaction(
            amount=Decimal("2000"),
            description=None,
            created_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
        )
        category = Category(name="Зарплата", type=TransactionType.income, user_id=1)
        session = FakeSession([(transaction, category)])
        tools = FinanceTools(lambda: session)

        result = await tools.get_recent_transactions(self.period, limit=5)

        self.assertEqual(result["transactions_count"], 1)
        self.assertEqual(result["transactions"][0]["type"], "income")
        self.assertEqual(result["transactions"][0]["description"], "")
