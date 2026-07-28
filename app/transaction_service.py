from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Transaction, TransactionType, User


ADMIN_TELEGRAM_ID = 0
MAX_TRANSACTION_AMOUNT = Decimal("999999999.99")


class TransactionNotFoundError(Exception):
    pass


def validate_transaction_amount(value: Decimal) -> Decimal:
    quantized = value.quantize(Decimal("0.01"))
    if value != quantized:
        raise ValueError("Amount can have at most 2 decimal places")
    return quantized


def normalize_category_name(value: str) -> str:
    category = value.strip()
    if not category:
        raise ValueError("Category is required")
    if not any(char.isalpha() for char in category):
        raise ValueError("Category must contain text")
    return category


class TransactionCreate(BaseModel):
    """Validated input shared by the form API and confirmed AI actions."""

    type: TransactionType
    amount: Decimal = Field(gt=Decimal("0"), le=MAX_TRANSACTION_AMOUNT)
    category: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    date: datetime | None = None

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return validate_transaction_amount(value)

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        return normalize_category_name(value)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        description = value.strip()
        return description or None

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        if value > datetime.now(timezone.utc):
            raise ValueError("Date cannot be in the future")
        return value


class TransactionCategoryUpdate(BaseModel):
    transaction_id: int = Field(gt=0)
    category: str = Field(min_length=1, max_length=100)

    @field_validator("transaction_id", mode="before")
    @classmethod
    def validate_transaction_id(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Transaction ID must be an integer")
        return value

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        return normalize_category_name(value)


class TransactionAmountUpdate(BaseModel):
    transaction_id: int = Field(gt=0)
    amount: Decimal = Field(gt=Decimal("0"), le=MAX_TRANSACTION_AMOUNT)

    @field_validator("transaction_id", mode="before")
    @classmethod
    def validate_transaction_id(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Transaction ID must be an integer")
        return value

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return validate_transaction_amount(value)


class TransactionDelete(BaseModel):
    transaction_id: int = Field(gt=0)

    @field_validator("transaction_id", mode="before")
    @classmethod
    def validate_transaction_id(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Transaction ID must be an integer")
        return value


async def get_or_create_admin_user(session: AsyncSession) -> User:
    user = await session.scalar(
        select(User).where(User.telegram_id == ADMIN_TELEGRAM_ID)
    )
    if user:
        return user

    user = User(
        telegram_id=ADMIN_TELEGRAM_ID,
        username="admin_dashboard",
        first_name="Admin",
        last_name="Dashboard",
    )
    session.add(user)
    await session.flush()
    return user


async def get_or_create_category(
    session: AsyncSession,
    user_id: int,
    name: str,
    transaction_type: TransactionType,
) -> Category:
    category = await session.scalar(
        select(Category).where(
            Category.user_id == user_id,
            Category.name == name,
            Category.type == transaction_type,
        )
    )
    if category:
        return category

    category = Category(
        user_id=user_id,
        name=name,
        type=transaction_type,
    )
    session.add(category)
    await session.flush()
    return category


async def create_admin_transaction(
    session: AsyncSession,
    payload: TransactionCreate,
) -> tuple[Transaction, Category, User]:
    """Create a transaction in the caller's database transaction."""

    user = await get_or_create_admin_user(session)
    category = await get_or_create_category(
        session=session,
        user_id=user.id,
        name=payload.category,
        transaction_type=payload.type,
    )
    transaction = Transaction(
        user_id=user.id,
        category_id=category.id,
        amount=payload.amount,
        description=payload.description,
        created_at=payload.date or datetime.now(timezone.utc),
    )
    session.add(transaction)
    await session.flush()
    return transaction, category, user


async def update_admin_transaction_category(
    session: AsyncSession,
    payload: TransactionCategoryUpdate,
) -> tuple[Transaction, Category, User]:
    user = await get_or_create_admin_user(session)
    transaction, current_category = await get_admin_transaction_for_update(
        session,
        user_id=user.id,
        transaction_id=payload.transaction_id,
    )
    category = await get_or_create_category(
        session=session,
        user_id=user.id,
        name=payload.category,
        transaction_type=current_category.type,
    )
    transaction.category_id = category.id
    await session.flush()
    return transaction, category, user


async def update_admin_transaction_sum(
    session: AsyncSession,
    payload: TransactionAmountUpdate,
) -> tuple[Transaction, Category, User]:
    user = await get_or_create_admin_user(session)
    transaction, category = await get_admin_transaction_for_update(
        session,
        user_id=user.id,
        transaction_id=payload.transaction_id,
    )
    transaction.amount = payload.amount
    await session.flush()
    return transaction, category, user


async def delete_admin_transaction(
    session: AsyncSession,
    payload: TransactionDelete,
) -> int:
    user = await get_or_create_admin_user(session)
    transaction, _ = await get_admin_transaction_for_update(
        session,
        user_id=user.id,
        transaction_id=payload.transaction_id,
    )
    transaction_id = transaction.id
    await session.delete(transaction)
    await session.flush()
    return transaction_id


async def get_admin_transaction_for_update(
    session: AsyncSession,
    *,
    user_id: int,
    transaction_id: int,
) -> tuple[Transaction, Category]:
    result = await session.execute(
        select(Transaction, Category)
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.id == transaction_id,
            Transaction.user_id == user_id,
        )
        .with_for_update()
    )
    row = result.one_or_none()
    if row is None:
        raise TransactionNotFoundError("Transaction not found.")
    return row


def transaction_to_json(
    transaction: Transaction,
    category: Category,
    user: User,
) -> dict[str, Any]:
    return {
        "id": transaction.id,
        "user_id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "category_id": category.id,
        "category_name": category.name,
        "type": category.type.value,
        "amount": str(transaction.amount),
        "description": transaction.description,
        "created_at": transaction.created_at.isoformat(),
    }
