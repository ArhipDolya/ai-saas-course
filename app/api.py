import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Path, Query, Response
from pydantic import BaseModel
from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.ai_analysis import GeminiAnalysisError, generate_transaction_analysis
from app.ai_chat import GeminiChatError, generate_chat_response
from app.database import dispose_database, get_session_factory, initialize_database
from app.expenses import TransactionData, save_transaction
from app.models import Category, Transaction, TransactionType, User
from app.schemas import ChatRequest, ChatResponse, TransactionAnalysisResponse, TransactionCreate

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


@app.post(
    "/api/ai/analyze-transactions",
    response_model=TransactionAnalysisResponse,
)
async def analyze_user_transactions(
    telegram_id: int = Query(..., gt=0, le=MAX_TELEGRAM_ID, description="Telegram ID користувача"),
) -> TransactionAnalysisResponse:
    statement = (
        select(
            Transaction.transaction_type.label("type"),
            Transaction.amount,
            Category.name.label("category"),
            Transaction.description,
            Transaction.transaction_date.label("date"),
        )
        .join(Category, Category.id == Transaction.category_id)
        .join(User, User.id == Transaction.user_id)
        .where(User.telegram_id == telegram_id)
        .order_by(
            Transaction.transaction_date.asc(),
            Transaction.created_at.asc(),
            Transaction.id.asc(),
        )
    )

    try:
        async with get_session_factory()() as session:
            rows = (await session.execute(statement)).mappings().all()
    except SQLAlchemyError as error:
        logger.error(
            "Transaction analysis read failed: error_type=%s, sqlstate=%s",
            type(error).__name__,
            getattr(getattr(error, "orig", None), "sqlstate", None),
        )
        raise HTTPException(
            status_code=503,
            detail="Не вдалося отримати операції для аналізу. Спробуй ще раз пізніше.",
        ) from None

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Для цього користувача ще немає транзакцій для аналізу.",
        )

    transactions = [
        {
            "type": row["type"],
            "amount": str(Decimal(row["amount"]).quantize(Decimal("0.01"))),
            "category": row["category"],
            "description": row["description"],
            "date": row["date"].isoformat(),
        }
        for row in rows
    ]

    try:
        analysis = await generate_transaction_analysis(transactions)
    except ValueError:
        logger.error("Gemini transaction analysis is not configured")
        raise HTTPException(
            status_code=503,
            detail="AI-аналіз зараз не налаштований. Спробуй ще раз пізніше.",
        ) from None
    except GeminiAnalysisError:
        raise HTTPException(
            status_code=502,
            detail="Не вдалося отримати коректний аналіз від Gemini. Спробуй ще раз пізніше.",
        ) from None

    logger.info("Transaction analysis completed: transactions_count=%s", len(transactions))
    return analysis


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


@app.post("/api/ai/chat", response_model=ChatResponse)
async def ai_chat(
    payload: ChatRequest,
    telegram_id: int = Query(..., gt=0, le=MAX_TELEGRAM_ID, description="Telegram ID користувача"),
) -> ChatResponse:
    try:
        response_text, pending_action_data = await generate_chat_response(
            thread_id=payload.thread_id,
            user_message=payload.message,
            telegram_id=telegram_id,
        )
    except ValueError:
        logger.error("Gemini chat is not configured")
        raise HTTPException(
            status_code=503,
            detail="AI-чат зараз не налаштований. Спробуй ще раз пізніше.",
        ) from None
    except GeminiChatError:
        raise HTTPException(
            status_code=502,
            detail="Не вдалося отримати відповідь від AI. Спробуй ще раз пізніше.",
        ) from None

    logger.info("Chat response sent: thread_id=%s, has_action=%s", payload.thread_id, bool(pending_action_data))
    return ChatResponse(
        message=response_text, 
        thread_id=payload.thread_id,
        pending_action=pending_action_data
    )

from app.ai_actions import get_pending_action, confirm_pending_action, cancel_pending_action
from app.models import Transaction, Category, User, TransactionType
from pydantic import ValidationError
from app.schemas import TransactionCreate
from datetime import datetime

@app.post("/api/ai/actions/{action_id}/confirm")
async def confirm_ai_action(
    action_id: str,
    telegram_id: int = Query(..., gt=0, le=MAX_TELEGRAM_ID),
):
    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Дію не знайдено.")
    if action["telegram_id"] != telegram_id:
        raise HTTPException(status_code=403, detail="Дія належить іншому користувачу.")
    if action["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Дію вже оброблено (статус: {action['status']}).")

    action_type = action["type"]
    payload = action["payload"]

    try:
        async with get_session_factory()() as session:
            user_result = await session.execute(select(User.id).where(User.telegram_id == telegram_id))
            user_id = user_result.scalar_one_or_none()
            if not user_id:
                raise HTTPException(status_code=404, detail="Користувача не знайдено.")

            if action_type in ("create_transaction", "update_transaction"):
                try:
                    valid_payload = TransactionCreate(**payload)
                except ValidationError as e:
                    raise HTTPException(status_code=400, detail=f"Невалідна структура даних: {e}")

                cat_result = await session.execute(
                    select(Category.id).where(Category.user_id == user_id, Category.name == valid_payload.category)
                )
                category_id = cat_result.scalar_one_or_none()
                if not category_id:
                    new_category = Category(user_id=user_id, name=valid_payload.category)
                    session.add(new_category)
                    await session.flush()
                    category_id = new_category.id

                if action_type == "create_transaction":
                    transaction = Transaction(
                        user_id=user_id,
                        category_id=category_id,
                        amount=valid_payload.amount,
                        transaction_type=valid_payload.type,
                        description=valid_payload.description,
                        transaction_date=valid_payload.date
                    )
                    session.add(transaction)
                else: # update
                    trans_id = payload.get("transaction_id")
                    result = await session.execute(
                        select(Transaction).where(Transaction.id == trans_id, Transaction.user_id == user_id)
                    )
                    transaction = result.scalar_one_or_none()
                    if not transaction:
                        raise HTTPException(status_code=404, detail="Транзакцію для оновлення не знайдено.")
                    
                    transaction.category_id = category_id
                    transaction.amount = valid_payload.amount
                    transaction.transaction_type = valid_payload.type
                    transaction.description = valid_payload.description
                    transaction.transaction_date = valid_payload.date

            elif action_type == "delete_transaction":
                trans_id = payload.get("transaction_id")
                result = await session.execute(
                    select(Transaction).where(Transaction.id == trans_id, Transaction.user_id == user_id)
                )
                transaction = result.scalar_one_or_none()
                if not transaction:
                    raise HTTPException(status_code=404, detail="Транзакцію не знайдено.")
                await session.delete(transaction)

            await session.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to confirm action {action_id}: {e}")
        raise HTTPException(status_code=500, detail="Помилка при виконанні дії.")

    confirm_pending_action(action_id)
    return {"message": "Дію успішно виконано."}


@app.post("/api/ai/actions/{action_id}/cancel")
async def cancel_ai_action(
    action_id: str,
    telegram_id: int = Query(..., gt=0, le=MAX_TELEGRAM_ID),
):
    action = get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Дію не знайдено.")
    if action["telegram_id"] != telegram_id:
        raise HTTPException(status_code=403, detail="Дія належить іншому користувачу.")
    if action["status"] != "pending":
        raise HTTPException(status_code=400, detail="Дію вже оброблено.")

    cancel_pending_action(action_id)
    return {"message": "Дію скасовано."}
