from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Category, Transaction, TransactionType


MIN_TOOL_LIMIT = 1
MAX_TOOL_LIMIT = 20
PERIOD_FORMAT = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

TOOL_SCHEMAS = (
    {
        "name": "get_transactions_summary",
        "description": (
            "Повертає загальні доходи, витрати, баланс і кількість операцій "
            "за вказаний період. Використовуй для загального фінансового зведення."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": (
                        "Один з форматів: current_month, previous_month, "
                        "last_30_days або YYYY-MM."
                    ),
                }
            },
            "required": ["period"],
        },
    },
    {
        "name": "get_category_totals",
        "description": (
            "Повертає категорії витрат, суми та кількість операцій у кожній "
            "категорії за вказаний період. Використовуй для порівняння витрат."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": (
                        "Один з форматів: current_month, previous_month, "
                        "last_30_days або YYYY-MM."
                    ),
                }
            },
            "required": ["period"],
        },
    },
    {
        "name": "get_top_expenses",
        "description": (
            "Повертає найбільші окремі витрати за вказаний період. "
            "Використовуй для запитів про найдорожчі покупки."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": (
                        "Один з форматів: current_month, previous_month, "
                        "last_30_days або YYYY-MM."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Кількість операцій від 1 до 20.",
                },
            },
            "required": ["period", "limit"],
        },
    },
    {
        "name": "get_recent_transactions",
        "description": (
            "Повертає останні доходи й витрати за вказаний період. "
            "Використовуй для запитів про конкретні або останні операції."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": (
                        "Один з форматів: current_month, previous_month, "
                        "last_30_days або YYYY-MM."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Кількість операцій від 1 до 20.",
                },
            },
            "required": ["period", "limit"],
        },
    },
)


class FinanceToolError(Exception):
    """Raised for a controlled read-only finance tool failure."""


class InvalidToolArgumentsError(FinanceToolError):
    pass


class UnknownToolError(FinanceToolError):
    pass


@dataclass(frozen=True)
class PeriodRange:
    key: str
    start: datetime
    end: datetime

    def as_json(self) -> dict[str, str]:
        return {
            "key": self.key,
            "from": self.start.date().isoformat(),
            "to": self.end.date().isoformat(),
        }


def money(value: object) -> str:
    return str(Decimal(str(value or 0)).quantize(Decimal("0.01")))


def resolve_period(period: str, now: datetime | None = None) -> PeriodRange:
    if not isinstance(period, str):
        raise InvalidToolArgumentsError("Період має бути рядком.")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    current_month_start = current_time.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    if period == "current_month":
        return PeriodRange(
            key=period,
            start=current_month_start,
            end=next_month_start(current_month_start),
        )

    if period == "previous_month":
        previous_month_end = current_month_start
        previous_month_start = previous_month_end.replace(day=1) - timedelta(days=1)
        previous_month_start = previous_month_start.replace(day=1)
        return PeriodRange(
            key=period,
            start=previous_month_start,
            end=previous_month_end,
        )

    if period == "last_30_days":
        return PeriodRange(
            key=period,
            start=current_time - timedelta(days=30),
            end=current_time,
        )

    if PERIOD_FORMAT.fullmatch(period):
        year, month = map(int, period.split("-"))
        month_start = datetime(year, month, 1, tzinfo=timezone.utc)
        return PeriodRange(
            key=period,
            start=month_start,
            end=next_month_start(month_start),
        )

    raise InvalidToolArgumentsError(
        "Непідтримуваний період. Використовуйте current_month, previous_month, "
        "last_30_days або YYYY-MM."
    )


def next_month_start(month_start: datetime) -> datetime:
    if month_start.month == 12:
        return month_start.replace(year=month_start.year + 1, month=1)
    return month_start.replace(month=month_start.month + 1)


def require_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidToolArgumentsError("limit має бути цілим числом.")
    if not MIN_TOOL_LIMIT <= value <= MAX_TOOL_LIMIT:
        raise InvalidToolArgumentsError(
            f"limit має бути від {MIN_TOOL_LIMIT} до {MAX_TOOL_LIMIT}."
        )
    return value


def require_period(arguments: dict[str, Any]) -> PeriodRange:
    return resolve_period(arguments.get("period"))


class FinanceTools:
    """Read-only financial queries available to the AI chat workflow."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise InvalidToolArgumentsError("Параметри tool мають бути об'єктом.")

        if name == "get_transactions_summary":
            return await self.get_transactions_summary(require_period(arguments))
        if name == "get_category_totals":
            return await self.get_category_totals(require_period(arguments))
        if name == "get_top_expenses":
            return await self.get_top_expenses(
                require_period(arguments),
                require_limit(arguments.get("limit")),
            )
        if name == "get_recent_transactions":
            return await self.get_recent_transactions(
                require_period(arguments),
                require_limit(arguments.get("limit")),
            )

        raise UnknownToolError("Запитаний tool недоступний.")

    async def get_transactions_summary(self, period: PeriodRange) -> dict[str, Any]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(
                    Category.type,
                    func.sum(Transaction.amount),
                    func.count(Transaction.id),
                )
                .join(Category, Transaction.category_id == Category.id)
                .where(
                    Transaction.created_at >= period.start,
                    Transaction.created_at < period.end,
                )
                .group_by(Category.type)
            )

            totals = {
                transaction_type: (Decimal(str(amount or 0)), int(count))
                for transaction_type, amount, count in result.all()
            }

        income, income_count = totals.get(TransactionType.income, (Decimal("0"), 0))
        expense, expense_count = totals.get(TransactionType.expense, (Decimal("0"), 0))
        return {
            "period": period.as_json(),
            "total_income": money(income),
            "total_expense": money(expense),
            "balance": money(income - expense),
            "transactions_count": income_count + expense_count,
        }

    async def get_category_totals(self, period: PeriodRange) -> dict[str, Any]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(
                    Category.name,
                    func.sum(Transaction.amount),
                    func.count(Transaction.id),
                )
                .join(Category, Transaction.category_id == Category.id)
                .where(
                    Category.type == TransactionType.expense,
                    Transaction.created_at >= period.start,
                    Transaction.created_at < period.end,
                )
                .group_by(Category.name)
                .order_by(func.sum(Transaction.amount).desc(), Category.name.asc())
            )
            rows = result.all()

        return {
            "period": period.as_json(),
            "categories": [
                {
                    "category": category_name,
                    "total_expense": money(total_expense),
                    "transactions_count": int(transactions_count),
                }
                for category_name, total_expense, transactions_count in rows
            ],
            "transactions_count": sum(int(row[2]) for row in rows),
        }

    async def get_top_expenses(self, period: PeriodRange, limit: int) -> dict[str, Any]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Transaction, Category)
                .join(Category, Transaction.category_id == Category.id)
                .where(
                    Category.type == TransactionType.expense,
                    Transaction.created_at >= period.start,
                    Transaction.created_at < period.end,
                )
                .order_by(Transaction.amount.desc(), Transaction.created_at.desc())
                .limit(limit)
            )
            rows = result.all()

        return {
            "period": period.as_json(),
            "expenses": [
                self._transaction_json(transaction, category)
                for transaction, category in rows
            ],
            "transactions_count": len(rows),
        }

    async def get_recent_transactions(self, period: PeriodRange, limit: int) -> dict[str, Any]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Transaction, Category)
                .join(Category, Transaction.category_id == Category.id)
                .where(
                    Transaction.created_at >= period.start,
                    Transaction.created_at < period.end,
                )
                .order_by(Transaction.created_at.desc(), Transaction.id.desc())
                .limit(limit)
            )
            rows = result.all()

        return {
            "period": period.as_json(),
            "transactions": [
                self._transaction_json(transaction, category)
                for transaction, category in rows
            ],
            "transactions_count": len(rows),
        }

    @staticmethod
    def _transaction_json(transaction: Transaction, category: Category) -> dict[str, str]:
        return {
            "type": category.type.value,
            "amount": money(transaction.amount),
            "category": category.name,
            "description": transaction.description or "",
            "created_at": transaction.created_at.isoformat(),
        }
