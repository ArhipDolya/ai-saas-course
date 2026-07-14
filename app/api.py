import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from secrets import compare_digest
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Path, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import (
    check_database_connection,
    create_database_engine,
    create_database_tables,
    create_sessionmaker,
)
from app.models import Category, Transaction, TransactionType, User

ADMIN_TELEGRAM_ID = 0
ADMIN_AUTH_HEADER = "X-Admin-Auth"
MAX_TRANSACTION_AMOUNT = Decimal("999999999.99")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load_dotenv()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env or environment variables.")

    admin_password = os.getenv("ADMIN_PASSWORD")
    if not admin_password:
        raise RuntimeError("ADMIN_PASSWORD is not set. Add it to .env or environment variables.")

    engine = create_database_engine(database_url)
    await check_database_connection(engine)
    await create_database_tables(engine)
    app.state.sessionmaker = create_sessionmaker(engine)
    app.state.admin_password = admin_password
    logging.info("API database connection OK")

    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="Finance Telegram Bot API", lifespan=lifespan)


class TransactionCreate(BaseModel):
    type: TransactionType
    amount: Decimal = Field(gt=Decimal("0"), le=MAX_TRANSACTION_AMOUNT)
    category: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    date: datetime | None = None

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        quantized = value.quantize(Decimal("0.01"))
        if value != quantized:
            raise ValueError("Amount can have at most 2 decimal places")

        return quantized

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        category = value.strip()
        if not category:
            raise ValueError("Category is required")

        if not any(char.isalpha() for char in category):
            raise ValueError("Category must contain text")

        return category

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


class AdminPasswordPayload(BaseModel):
    password: str = Field(min_length=1, max_length=200)


def require_admin_password(request: Request, password: str) -> None:
    admin_password = request.app.state.admin_password
    if not compare_digest(password.encode("utf-8"), admin_password.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid admin password")


def format_money(value: object) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


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


@app.get("/api/transactions", include_in_schema=False)
@app.get("/api/transactions/")
async def get_transactions(request: Request) -> list[dict[str, Any]]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker

    async with sessionmaker() as session:
        result = await session.execute(
            select(Transaction, Category, User)
            .join(Category, Transaction.category_id == Category.id)
            .join(User, Transaction.user_id == User.id)
            .order_by(Transaction.created_at.desc())
        )

        return [
            transaction_to_json(transaction, category, user)
            for transaction, category, user in result.all()
        ]


@app.post("/api/admin/verify-password")
@app.post("/api/admin/verify-password/", include_in_schema=False)
async def verify_admin_password(
    payload: AdminPasswordPayload,
    request: Request,
) -> dict[str, bool]:
    require_admin_password(request, payload.password)
    return {"authenticated": True}


@app.post("/api/transactions", status_code=201)
@app.post("/api/transactions/", status_code=201, include_in_schema=False)
async def create_transaction(
    payload: TransactionCreate,
    request: Request,
    admin_password: str = Header(default="", alias=ADMIN_AUTH_HEADER),
) -> dict[str, Any]:
    require_admin_password(request, admin_password)
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker

    async with sessionmaker() as session:
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
        await session.commit()
        await session.refresh(transaction)

        return transaction_to_json(transaction, category, user)


@app.delete("/api/transactions/{transaction_id}")
@app.delete("/api/transactions/{transaction_id}/", include_in_schema=False)
async def delete_transaction(
    request: Request,
    transaction_id: int = Path(gt=0),
) -> dict[str, int | bool]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker

    async with sessionmaker() as session:
        transaction = await session.get(Transaction, transaction_id)
        if transaction is None:
            raise HTTPException(status_code=404, detail="Transaction not found")

        await session.delete(transaction)
        await session.commit()

    return {"deleted": True, "id": transaction_id}


@app.get("/api/summary", include_in_schema=False)
@app.get("/api/summary/")
async def get_summary(request: Request) -> dict[str, str]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker

    async with sessionmaker() as session:
        result = await session.execute(
            select(Category.type, func.sum(Transaction.amount))
            .join(Category, Transaction.category_id == Category.id)
            .group_by(Category.type)
        )

        totals = {transaction_type.value: amount for transaction_type, amount in result.all()}
        total_income = Decimal(str(totals.get("income", 0)))
        total_expense = Decimal(str(totals.get("expense", 0)))
        balance = total_income - total_expense

        return {
            "total_income": format_money(total_income),
            "total_expense": format_money(total_expense),
            "balance": format_money(balance),
        }


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
