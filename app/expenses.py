from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database import get_session_factory
from app.models import Category, Transaction, TransactionType, User
from app.transaction_rules import (
    MAX_AMOUNT,
    MAX_CATEGORY_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    today,
)
MONEY_DECIMAL_PLACES = 2


class TransactionValidationError(ValueError):
    """Повідомляє про некоректний формат фінансової команди."""


@dataclass(frozen=True)
class TransactionData:
    amount: Decimal
    category_name: str
    description: str | None
    transaction_date: date | None = None


def parse_transaction(
    arguments: str | None,
    *,
    command_name: str,
) -> TransactionData:
    """Перетворює аргументи команди на валідовану фінансову операцію."""
    usage = f"Використання: {command_name} <сума> <категорія> [| <опис>]"
    if not arguments:
        raise TransactionValidationError(usage)

    parts = arguments.split(maxsplit=1)
    if len(parts) != 2:
        raise TransactionValidationError(usage)

    amount_raw, category_and_description = parts
    try:
        amount = Decimal(amount_raw.replace(",", "."))
    except InvalidOperation as error:
        raise TransactionValidationError(
            "Сума має бути числом, наприклад 120 або 120.50."
        ) from error

    if not amount.is_finite() or amount <= 0:
        raise TransactionValidationError("Сума має бути додатним числом.")
    if amount > MAX_AMOUNT:
        raise TransactionValidationError("Сума не може перевищувати 9 999 999 999,99 грн.")
    if amount.as_tuple().exponent < -MONEY_DECIMAL_PLACES:
        raise TransactionValidationError(
            "Сума може містити не більше двох знаків після коми."
        )

    category_raw, separator, description_raw = category_and_description.partition("|")
    category_name = " ".join(category_raw.split())
    if not category_name:
        raise TransactionValidationError("Вкажи категорію після суми.")
    if len(category_name) > MAX_CATEGORY_LENGTH:
        raise TransactionValidationError(
            "Назва категорії має містити не більше 100 символів."
        )

    description = " ".join(description_raw.split()) if separator else None
    if separator and not description:
        raise TransactionValidationError("Вкажи опис після символу | або прибери його.")
    if description and len(description) > MAX_DESCRIPTION_LENGTH:
        raise TransactionValidationError("Опис має містити не більше 255 символів.")

    return TransactionData(
        amount=amount,
        category_name=category_name,
        description=description,
    )


async def save_transaction(
    *,
    telegram_id: int,
    transaction_data: TransactionData,
    transaction_type: TransactionType,
) -> Transaction:
    """Зберігає дохід або витрату разом із користувачем та категорією."""
    async with get_session_factory().begin() as session:
        # The bot and API may create the same user/category concurrently.
        user_id = await session.scalar(
            insert(User)
            .values(telegram_id=telegram_id)
            .on_conflict_do_nothing(index_elements=[User.telegram_id])
            .returning(User.id)
        )
        if user_id is None:
            user_id = await session.scalar(
                select(User.id).where(User.telegram_id == telegram_id)
            )

        category_id = await session.scalar(
            insert(Category)
            .values(user_id=user_id, name=transaction_data.category_name)
            .on_conflict_do_nothing(index_elements=[Category.user_id, Category.name])
            .returning(Category.id)
        )
        if category_id is None:
            category_id = await session.scalar(
                select(Category.id).where(
                    Category.user_id == user_id,
                    Category.name == transaction_data.category_name,
                )
            )

        transaction = Transaction(
            user_id=user_id,
            category_id=category_id,
            amount=transaction_data.amount,
            transaction_type=transaction_type,
            description=transaction_data.description,
            transaction_date=transaction_data.transaction_date or today(),
        )
        session.add(transaction)
        await session.flush()

        return transaction
