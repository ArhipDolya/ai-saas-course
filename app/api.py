import logging
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import check_database_connection, create_database_engine, create_sessionmaker
from app.models import Category, Transaction, User


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load_dotenv()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env or environment variables.")

    engine = create_database_engine(database_url)
    await check_database_connection(engine)
    app.state.sessionmaker = create_sessionmaker(engine)
    logging.info("API database connection OK")

    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="Finance Telegram Bot API", lifespan=lifespan)


def format_money(value: object) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


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
            {
                "id": transaction.id,
                "user_id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "category_id": category.id,
                "category_name": category.name,
                "type": category.type.value,
                "amount": str(transaction.amount),
                "created_at": transaction.created_at.isoformat(),
            }
            for transaction, category, user in result.all()
        ]


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
