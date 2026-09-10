import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Path, Query, Response
from pydantic import BaseModel
from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.database import dispose_database, get_session_factory, initialize_database
from app.expenses import TransactionData, save_transaction
from app.models import Category, Transaction, TransactionType, User
from app.schemas import TransactionCreate

logger = logging.getLogger("uvicorn.error")
MAX_TELEGRAM_ID = 9_223_372_036_854_775_807


class TransactionResponse(BaseModel):
    id: int
    type: TransactionType
    amount: Decimal
    category: str
    description: str | None
    date: date
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


@app.post("/api/transactions", response_model=TransactionResponse, status_code=201)
async def create_transaction(
    payload: TransactionCreate,
    telegram_id: int = Query(..., gt=0, le=MAX_TELEGRAM_ID, description="Telegram ID користувача"),
) -> TransactionResponse:
    try:
        transaction = await save_transaction(
            telegram_id=telegram_id,
            transaction_type=payload.type,
            transaction_data=TransactionData(
                amount=payload.amount,
                category_name=payload.category,
                description=payload.description,
                transaction_date=payload.date,
            ),
        )
    except SQLAlchemyError as error:
        logger.error(
            "Transaction save failed: error_type=%s, sqlstate=%s",
            type(error).__name__,
            getattr(getattr(error, "orig", None), "sqlstate", None),
        )
        raise HTTPException(
            status_code=503,
            detail="Не вдалося зберегти операцію. Спробуй ще раз трохи пізніше.",
        ) from None

    logger.info(
        "Transaction created: transaction_id=%s, type=%s",
        transaction.id,
        payload.type.value,
    )
    return TransactionResponse(
        id=transaction.id,
        type=transaction.transaction_type,
        amount=transaction.amount.quantize(Decimal("0.01")),
        category=payload.category,
        description=transaction.description,
        date=transaction.transaction_date,
        created_at=transaction.created_at,
    )


@app.delete("/api/transactions/{id}", status_code=204, response_class=Response)
async def delete_transaction(
    id: int = Path(..., gt=0, le=9_223_372_036_854_775_807),
    telegram_id: int = Query(..., gt=0, le=MAX_TELEGRAM_ID, description="Telegram ID користувача"),
) -> Response:
    # Scope the DELETE itself to the owner, so another user's row cannot be deleted.
    statement = (
        delete(Transaction)
        .where(
            Transaction.id == id,
            Transaction.user_id.in_(select(User.id).where(User.telegram_id == telegram_id)),
        )
        .returning(Transaction.id)
    )

    try:
        async with get_session_factory().begin() as session:
            deleted_id = await session.scalar(statement)
            if deleted_id is None:
                raise HTTPException(status_code=404, detail="Операцію не знайдено.")
    except SQLAlchemyError as error:
        logger.error(
            "Transaction delete failed: transaction_id=%s, error_type=%s, sqlstate=%s",
            id,
            type(error).__name__,
            getattr(getattr(error, "orig", None), "sqlstate", None),
        )
        raise HTTPException(
            status_code=503,
            detail="Не вдалося видалити операцію. Спробуй ще раз трохи пізніше.",
        ) from None

    logger.info("Transaction deleted: transaction_id=%s", deleted_id)
    return Response(status_code=204)


@app.get("/api/transactions", response_model=list[TransactionResponse])
async def get_transactions(
    telegram_id: int = Query(..., gt=0, le=MAX_TELEGRAM_ID, description="Telegram ID користувача"),
) -> list[TransactionResponse]:
    statement = (
        select(
            Transaction.id,
            Transaction.transaction_type.label("type"),
            Transaction.amount,
            Category.name.label("category"),
            Transaction.description,
            Transaction.transaction_date.label("date"),
            Transaction.created_at,
        )
        .join(Category, Category.id == Transaction.category_id)
        .join(User, User.id == Transaction.user_id)
        .where(User.telegram_id == telegram_id)
        .order_by(
            Transaction.transaction_date.desc(),
            Transaction.created_at.desc(),
            Transaction.id.desc(),
        )
    )

    async with get_session_factory()() as session:
        rows = (await session.execute(statement)).mappings().all()

    return [TransactionResponse(**row) for row in rows]


@app.get("/api/summary", response_model=SummaryResponse)
async def get_summary(
    telegram_id: int = Query(..., gt=0, le=MAX_TELEGRAM_ID, description="Telegram ID користувача"),
) -> SummaryResponse:
    total_income = func.coalesce(
        func.sum(
            case(
                (Transaction.transaction_type == TransactionType.INCOME, Transaction.amount),
                else_=0,
            )
        ),
        0,
    ).label("total_income")
    total_expense = func.coalesce(
        func.sum(
            case(
                (Transaction.transaction_type == TransactionType.EXPENSE, Transaction.amount),
                else_=0,
            )
        ),
        0,
    ).label("total_expense")
    statement = (
        select(total_income, total_expense)
        .join(User, User.id == Transaction.user_id)
        .where(User.telegram_id == telegram_id)
    )

    async with get_session_factory()() as session:
        totals = (await session.execute(statement)).one()

    total_income = Decimal(totals.total_income or 0).quantize(Decimal("0.01"))
    total_expense = Decimal(totals.total_expense or 0).quantize(Decimal("0.01"))

    return SummaryResponse(
        total_income=total_income,
        total_expense=total_expense,
        balance=total_income - total_expense,
    )
