import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Path, Query, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.ai_analysis import (
    GeminiAnalysisError,
    GeminiUnavailableError,
    generate_transaction_analysis,
)
from app.ai_chat import GeminiChatError, generate_chat_response
from app.database import dispose_database, get_session_factory, initialize_database
from app.expenses import TransactionData, save_transaction
from app.models import Category, Transaction, TransactionType, User
from app.schemas import ChatRequest, ChatResponse, PendingActionData, TransactionAnalysisResponse, TransactionCreate

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
    except GeminiUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="Gemini зараз перевантажений. Спробуй ще раз за кілька секунд.",
            headers={"Retry-After": "5"},
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

    logger.info("Chat response sent: thread_id=%s", payload.thread_id)

    pending_action = None
    if pending_action_data:
        pending_action = PendingActionData(**pending_action_data)

    return ChatResponse(
        message=response_text,
        thread_id=payload.thread_id,
        pending_action=pending_action,
    )


@app.post("/api/ai/actions/{action_id}/confirm")
async def confirm_action(
    action_id: str = Path(..., min_length=1, max_length=50),
    telegram_id: int = Query(..., gt=0, le=MAX_TELEGRAM_ID, description="Telegram ID користувача"),
) -> dict:
    from app.ai_actions import (
        ActionType,
        confirm_pending_action,
        get_pending_action,
        release_pending_action,
        reserve_pending_action,
    )

    action = get_pending_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Дію не знайдено.")
    if action.telegram_id != telegram_id:
        raise HTTPException(status_code=403, detail="Ця дія належить іншому користувачу.")

    reserved = reserve_pending_action(action_id)
    if reserved is None:
        raise HTTPException(status_code=409, detail="Дія вже була підтверджена або скасована.")

    try:
        payload = reserved.payload

        if reserved.action_type == ActionType.CREATE_TRANSACTION:
            validated = TransactionCreate(
                type=payload.get("transaction_type"),
                amount=payload.get("amount"),
                category=payload.get("category"),
                description=payload.get("description"),
                date=payload.get("transaction_date"),
            )
            await save_transaction(
                telegram_id=telegram_id,
                transaction_type=validated.type,
                transaction_data=TransactionData(
                    amount=validated.amount,
                    category_name=validated.category,
                    description=validated.description,
                    transaction_date=validated.date,
                ),
            )

        elif reserved.action_type == ActionType.DELETE_TRANSACTION:
            transaction_id = payload.get("transaction_id")
            if isinstance(transaction_id, bool) or not isinstance(transaction_id, int) or transaction_id <= 0:
                raise ValueError("invalid transaction_id")
            statement = (
                delete(Transaction)
                .where(
                    Transaction.id == transaction_id,
                    Transaction.user_id.in_(
                        select(User.id).where(User.telegram_id == telegram_id)
                    ),
                )
                .returning(Transaction.id)
            )
            async with get_session_factory().begin() as session:
                deleted_id = await session.scalar(statement)
                if deleted_id is None:
                    raise HTTPException(status_code=404, detail="Транзакцію не знайдено.")

        elif reserved.action_type == ActionType.UPDATE_TRANSACTION:
            transaction_id = payload.get("transaction_id")
            if isinstance(transaction_id, bool) or not isinstance(transaction_id, int) or transaction_id <= 0:
                raise ValueError("invalid transaction_id")

            async with get_session_factory().begin() as session:
                row = (
                    await session.execute(
                        select(Transaction, Category.name)
                        .join(Category, Category.id == Transaction.category_id)
                        .where(
                            Transaction.id == transaction_id,
                            Transaction.user_id.in_(
                                select(User.id).where(User.telegram_id == telegram_id)
                            ),
                        )
                    )
                ).one_or_none()
                if row is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Транзакцію для оновлення не знайдено.",
                    )

                transaction, current_category = row
                validated = TransactionCreate(
                    type=transaction.transaction_type,
                    amount=payload.get("amount", transaction.amount),
                    category=payload.get("category", current_category),
                    description=payload.get("description", transaction.description),
                    date=payload.get(
                        "transaction_date",
                        transaction.transaction_date.isoformat(),
                    ),
                )

                category_id = await session.scalar(
                    insert(Category)
                    .values(user_id=transaction.user_id, name=validated.category)
                    .on_conflict_do_nothing(
                        index_elements=[Category.user_id, Category.name]
                    )
                    .returning(Category.id)
                )
                if category_id is None:
                    category_id = await session.scalar(
                        select(Category.id).where(
                            Category.user_id == transaction.user_id,
                            Category.name == validated.category,
                        )
                    )

                transaction.category_id = category_id
                transaction.amount = validated.amount
                transaction.description = validated.description
                transaction.transaction_date = validated.date or transaction.transaction_date

        else:
            raise ValueError("unknown action type")
    except HTTPException:
        release_pending_action(action_id)
        raise
    except (ValidationError, ValueError, KeyError):
        release_pending_action(action_id)
        raise HTTPException(
            status_code=422,
            detail="AI підготував некоректні дані операції. Створи запит ще раз.",
        ) from None
    except SQLAlchemyError as error:
        release_pending_action(action_id)
        logger.error(
            "Pending action failed: action_id=%s, error_type=%s, sqlstate=%s",
            action_id,
            type(error).__name__,
            getattr(getattr(error, "orig", None), "sqlstate", None),
        )
        raise HTTPException(
            status_code=503,
            detail="Не вдалося виконати дію. Спробуй ще раз трохи пізніше.",
        ) from None
    except Exception:
        release_pending_action(action_id)
        logger.exception("Unexpected pending action failure: action_id=%s", action_id)
        raise

    confirm_pending_action(action_id)
    logger.info(
        "Pending action confirmed: action_id=%s, action_type=%s",
        action_id,
        reserved.action_type.value,
    )
    return {"status": "confirmed", "action_id": action_id}


@app.post("/api/ai/actions/{action_id}/cancel")
async def cancel_action(
    action_id: str = Path(..., min_length=1, max_length=50),
    telegram_id: int = Query(..., gt=0, le=MAX_TELEGRAM_ID, description="Telegram ID користувача"),
) -> dict:
    from app.ai_actions import cancel_pending_action, get_pending_action

    action = get_pending_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Дію не знайдено.")
    if action.telegram_id != telegram_id:
        raise HTTPException(status_code=403, detail="Ця дія належить іншому користувачу.")

    cancelled = cancel_pending_action(action_id)
    if cancelled is None:
        raise HTTPException(status_code=409, detail="Дія вже була підтверджена або скасована.")

    logger.info("Pending action cancelled: action_id=%s", action_id)
    return {"status": "cancelled", "action_id": action_id}
