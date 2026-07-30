from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Category, Transaction, TransactionType


MIN_TOOL_LIMIT = 1
MAX_TOOL_LIMIT = 20
MAX_TRANSACTION_AMOUNT = Decimal("999999999.99")
PERIOD_FORMAT = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DATE_FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_FORMAT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_TIME_FORMAT = re.compile(
    r"^(\d{4}-\d{2}-\d{2})(?:,\s*|\s+)((?:[01]\d|2[0-3]):[0-5]\d)$"
)
KYIV_TIMEZONE = ZoneInfo("Europe/Kyiv")

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
            "Використовуй для загального огляду останніх операцій. Для точного "
            "пошуку перед зміною або видаленням використовуй find_transactions."
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
        "name": "find_transactions",
        "description": (
            "Шукає конкретні операції й повертає їхні ID. Використовуй перед "
            "зміною суми, категорії або видаленням, коли користувач назвав "
            "дату, час, суму, категорію чи тип, але не вказав ID. Дата й час "
            "інтерпретуються у часовому поясі Europe/Kyiv."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "current_month, previous_month, last_30_days або YYYY-MM.",
                },
                "type": {"type": "string", "enum": ["income", "expense"]},
                "amount": {
                    "type": "number",
                    "description": "Точна додатна сума операції.",
                },
                "category": {
                    "type": "string",
                    "description": "Точна категорія, без урахування регістру.",
                },
                "description": {
                    "type": "string",
                    "description": "Точний опис операції, без урахування регістру.",
                },
                "date": {
                    "type": "string",
                    "description": "Дата операції у форматі YYYY-MM-DD.",
                },
                "time": {
                    "type": "string",
                    "description": "Необов'язковий точний час у форматі HH:MM.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Кількість результатів від 1 до 20.",
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


@dataclass(frozen=True)
class TransactionMatch:
    period: PeriodRange
    limit: int
    transaction_type: TransactionType | None
    amount: Decimal | None
    category: str | None
    description: str | None
    created_at_start: datetime | None
    created_at_end: datetime | None

    def as_json(self) -> dict[str, str]:
        filters: dict[str, str] = {}
        if self.transaction_type is not None:
            filters["type"] = self.transaction_type.value
        if self.amount is not None:
            filters["amount"] = money(self.amount)
        if self.category is not None:
            filters["category"] = self.category
        if self.description is not None:
            filters["description"] = self.description
        if self.created_at_start is not None:
            filters["created_at_from"] = self.created_at_start.astimezone(
                KYIV_TIMEZONE
            ).isoformat()
        if self.created_at_end is not None:
            filters["created_at_to"] = self.created_at_end.astimezone(
                KYIV_TIMEZONE
            ).isoformat()
        return filters


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


def require_optional_transaction_type(value: object) -> TransactionType | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidToolArgumentsError("Тип операції має бути income або expense.")
    try:
        return TransactionType(value)
    except ValueError as error:
        raise InvalidToolArgumentsError(
            "Тип операції має бути income або expense."
        ) from error


def require_optional_amount(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidToolArgumentsError("Сума має бути числом.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidToolArgumentsError("Сума має бути числом.") from error

    if not amount.is_finite() or not Decimal("0") < amount <= MAX_TRANSACTION_AMOUNT:
        raise InvalidToolArgumentsError("Сума має бути додатною та в допустимому діапазоні.")
    if amount != amount.quantize(Decimal("0.01")):
        raise InvalidToolArgumentsError("Сума може мати не більше двох знаків після коми.")
    return amount


def require_optional_category(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidToolArgumentsError("Категорія має бути текстом.")
    category = value.strip()
    if not category or len(category) > 100 or not any(char.isalpha() for char in category):
        raise InvalidToolArgumentsError("Категорія має містити текст до 100 символів.")
    return category


def require_optional_description(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidToolArgumentsError("Опис має бути текстом.")
    description = value.strip()
    if not description or len(description) > 500:
        raise InvalidToolArgumentsError("Опис має містити від 1 до 500 символів.")
    return description


def resolve_match_time_range(
    date_value: object,
    time_value: object,
) -> tuple[datetime | None, datetime | None]:
    if isinstance(date_value, str):
        date_time_match = DATE_TIME_FORMAT.fullmatch(date_value.strip())
        if date_time_match:
            parsed_date, parsed_time = date_time_match.groups()
            if time_value is not None and time_value != parsed_time:
                raise InvalidToolArgumentsError(
                    "Дата й час операції суперечать один одному."
                )
            date_value = parsed_date
            time_value = parsed_time

    if date_value is None:
        if time_value is not None:
            raise InvalidToolArgumentsError("Час можна вказати лише разом з датою.")
        return None, None
    if not isinstance(date_value, str) or not DATE_FORMAT.fullmatch(date_value):
        raise InvalidToolArgumentsError("Дата має бути у форматі YYYY-MM-DD.")
    if time_value is not None and (
        not isinstance(time_value, str) or not TIME_FORMAT.fullmatch(time_value)
    ):
        raise InvalidToolArgumentsError("Час має бути у форматі HH:MM.")

    try:
        value = f"{date_value} {time_value or '00:00'}"
        local_start = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(
            tzinfo=KYIV_TIMEZONE
        )
    except ValueError as error:
        raise InvalidToolArgumentsError("Дата або час операції некоректні.") from error

    local_end = local_start + (timedelta(minutes=1) if time_value else timedelta(days=1))
    return (
        local_start.astimezone(timezone.utc),
        local_end.astimezone(timezone.utc),
    )


def require_transaction_match(arguments: dict[str, Any]) -> TransactionMatch:
    period = require_period(arguments)
    transaction_type = require_optional_transaction_type(arguments.get("type"))
    amount = require_optional_amount(arguments.get("amount"))
    category = require_optional_category(arguments.get("category"))
    description = require_optional_description(arguments.get("description"))
    created_at_start, created_at_end = resolve_match_time_range(
        arguments.get("date"),
        arguments.get("time"),
    )

    if not any(
        (transaction_type, amount is not None, category, description, created_at_start)
    ):
        raise InvalidToolArgumentsError(
            "Для пошуку конкретної операції потрібен хоча б один фільтр."
        )

    return TransactionMatch(
        period=period,
        limit=require_limit(arguments.get("limit")),
        transaction_type=transaction_type,
        amount=amount,
        category=category,
        description=description,
        created_at_start=created_at_start,
        created_at_end=created_at_end,
    )


class FinanceTools:
    """Read-only financial queries available to the AI chat workflow."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: object | None = None,
    ) -> dict[str, Any]:
        del context
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
        if name == "find_transactions":
            return await self.find_transactions(require_transaction_match(arguments))

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

    async def find_transactions(self, match: TransactionMatch) -> dict[str, Any]:
        filters = [
            Transaction.created_at >= match.period.start,
            Transaction.created_at < match.period.end,
        ]
        if match.transaction_type is not None:
            filters.append(Category.type == match.transaction_type)
        if match.amount is not None:
            filters.append(Transaction.amount == match.amount)
        if match.category is not None:
            filters.append(func.lower(Category.name) == match.category.lower())
        if match.description is not None:
            filters.append(
                func.lower(func.coalesce(Transaction.description, ""))
                == match.description.lower()
            )
        if match.created_at_start is not None and match.created_at_end is not None:
            filters.extend(
                (
                    Transaction.created_at >= match.created_at_start,
                    Transaction.created_at < match.created_at_end,
                )
            )

        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Transaction, Category)
                .join(Category, Transaction.category_id == Category.id)
                .where(*filters)
                .order_by(Transaction.created_at.desc(), Transaction.id.desc())
                .limit(match.limit)
            )
            rows = result.all()

        return {
            "period": match.period.as_json(),
            "filters": match.as_json(),
            "transactions": [
                self._transaction_json(transaction, category)
                for transaction, category in rows
            ],
            "transactions_count": len(rows),
        }

    @staticmethod
    def _transaction_json(transaction: Transaction, category: Category) -> dict[str, Any]:
        created_at = transaction.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        return {
            "id": transaction.id,
            "type": category.type.value,
            "amount": money(transaction.amount),
            "category": category.name,
            "description": transaction.description or "",
            "created_at": created_at.astimezone(KYIV_TIMEZONE).isoformat(),
            "timezone": "Europe/Kyiv",
        }
