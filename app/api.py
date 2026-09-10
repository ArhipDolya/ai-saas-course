from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.database import dispose_database, get_session_factory, initialize_database
from app.models import Category, Transaction, User


class TransactionResponse(BaseModel):
    id: int
    amount: Decimal
    category: str
    created_at: datetime


class SummaryResponse(BaseModel):
    total_income: Decimal
    total_expense: Decimal
    balance: Decimal


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await initialize_database()
    try:
        yield
    finally:
        await dispose_database()


app = FastAPI(title="Finance SaaS API", lifespan=lifespan)


@app.get("/api/transactions", response_model=list[TransactionResponse])
async def get_transactions(
    telegram_id: int = Query(..., gt=0, description="Telegram ID користувача"),
) -> list[TransactionResponse]:
    statement = (
        select(
            Transaction.id,
            Transaction.amount,
            Category.name.label("category"),
            Transaction.created_at,
        )
        .join(Category, Category.id == Transaction.category_id)
        .join(User, User.id == Transaction.user_id)
        .where(User.telegram_id == telegram_id)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
    )

    async with get_session_factory()() as session:
        rows = (await session.execute(statement)).mappings().all()

    return [TransactionResponse(**row) for row in rows]


@app.get("/api/summary", response_model=SummaryResponse)
async def get_summary(
    telegram_id: int = Query(..., gt=0, description="Telegram ID користувача"),
) -> SummaryResponse:
    statement = (
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .join(User, User.id == Transaction.user_id)
        .where(User.telegram_id == telegram_id)
    )

    async with get_session_factory()() as session:
        total_expense = await session.scalar(statement)

    total_income = Decimal("0.00")
    total_expense = Decimal(total_expense or 0).quantize(Decimal("0.01"))

    return SummaryResponse(
        total_income=total_income,
        total_expense=total_expense,
        balance=total_income - total_expense,
    )
