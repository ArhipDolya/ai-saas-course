from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.database import get_session_factory
from app.models import Category, Transaction, User

MAX_CATEGORY_LENGTH = 100
MONEY_DECIMAL_PLACES = 2


class ExpenseValidationError(ValueError):
    """Повідомляє про некоректний формат команди /expense."""


@dataclass(frozen=True)
class ExpenseData:
    amount: Decimal
    category_name: str


def parse_expense(arguments: str | None) -> ExpenseData:
    """Перетворює аргументи /expense на валідовані суму та категорію."""
    if not arguments:
        raise ExpenseValidationError("Використання: /expense <сума> <категорія>")

    parts = arguments.split(maxsplit=1)
    if len(parts) != 2:
        raise ExpenseValidationError("Використання: /expense <сума> <категорія>")

    amount_raw, category_raw = parts
    try:
        amount = Decimal(amount_raw.replace(",", "."))
    except InvalidOperation as error:
        raise ExpenseValidationError("Сума має бути числом, наприклад 120 або 120.50.") from error

    if not amount.is_finite() or amount <= 0:
        raise ExpenseValidationError("Сума має бути додатним числом.")
    if amount.as_tuple().exponent < -MONEY_DECIMAL_PLACES:
        raise ExpenseValidationError("Сума може містити не більше двох знаків після коми.")

    category_name = " ".join(category_raw.split())
    if not category_name:
        raise ExpenseValidationError("Вкажи категорію витрати після суми.")
    if len(category_name) > MAX_CATEGORY_LENGTH:
        raise ExpenseValidationError("Назва категорії має містити не більше 100 символів.")

    return ExpenseData(amount=amount, category_name=category_name)


async def save_expense(
    *,
    telegram_id: int,
    expense: ExpenseData,
) -> Transaction:
    """Зберігає витрату разом із користувачем і його категорією."""
    async with get_session_factory().begin() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id)
        )
        if user is None:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.flush()

        category = await session.scalar(
            select(Category).where(
                Category.user_id == user.id,
                Category.name == expense.category_name,
            )
        )
        if category is None:
            category = Category(
                user_id=user.id,
                name=expense.category_name,
            )
            session.add(category)
            await session.flush()

        transaction = Transaction(
            user_id=user.id,
            category_id=category.id,
            amount=expense.amount,
        )
        session.add(transaction)
        await session.flush()

        return transaction
