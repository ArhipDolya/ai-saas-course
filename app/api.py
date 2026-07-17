import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from secrets import compare_digest
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Path, Request
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.check_llm import DEFAULT_LLM_MODEL
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
    app.state.llm_api_key = os.getenv("LLM_API_KEY")
    app.state.llm_model = os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)
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


class FinancialAnalysis(BaseModel):
    summary: str
    top_expense_categories: list[str]
    risks: list[str]
    advice: list[str]


class LLMAuthenticationError(Exception):
    pass


class LLMModelError(Exception):
    pass


class LLMUnavailableError(Exception):
    pass


class LLMInvalidResponseError(Exception):
    pass


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


def transaction_to_ai_payload(transaction: Transaction, category: Category) -> dict[str, str]:
    return {
        "type": category.type.value,
        "amount": str(transaction.amount),
        "category": category.name,
        "description": transaction.description or "",
        "created_at": transaction.created_at.isoformat(),
    }


def build_financial_analysis_prompt(transactions: list[dict[str, str]]) -> str:
    transactions_json = json.dumps(transactions, ensure_ascii=False, indent=2)

    return f"""
Проаналізуй список фінансових операцій користувача.

Завдання:
- дай короткий загальний висновок по цих операціях;
- визнач, на які категорії товарів або послуг було витрачено найбільше грошей;
- знайди можливі фінансові ризики;
- дай рівно 3 поради з фінансової грамотності;
- відповідай українською мовою;
- не вигадуй операції, яких немає у списку.

Поверни тільки JSON без Markdown і без додаткового тексту.
JSON має мати рівно такі поля:
{{
  "summary": "Короткий загальний висновок",
  "top_expense_categories": ["Їжа", "Транспорт", "Кава"],
  "risks": ["Витрати на каву зростають"],
  "advice": ["Встановити ліміт на каву"]
}}

Операції:
{transactions_json}
""".strip()


def request_financial_analysis_from_llm(
    api_key: str,
    model: str,
    prompt: str,
) -> FinancialAnalysis:
    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise LLMUnavailableError(
            "google-genai is not installed. Run: pip install -r requirements.txt"
        ) from error

    client = genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=FinancialAnalysis,
            ),
        )
    except Exception as error:
        error_message = str(error)
        normalized_error = error_message.lower()
        status_code = getattr(error, "status_code", None)

        if (
            status_code in {429, 500, 502, 503, 504}
            or "high demand" in normalized_error
            or "temporarily unavailable" in normalized_error
        ):
            raise LLMUnavailableError("Gemini API is temporarily unavailable.") from error

        if "api key" in normalized_error or status_code in {401, 403}:
            raise LLMAuthenticationError("Gemini rejected LLM_API_KEY.") from error

        if status_code == 404 or "model" in normalized_error:
            raise LLMModelError("Gemini rejected LLM_MODEL.") from error

        raise LLMUnavailableError(
            f"LLM API request failed: {type(error).__name__}: {error}"
        ) from error

    response_text = getattr(response, "text", "").strip()
    if not response_text:
        raise LLMInvalidResponseError("Gemini returned an empty response.")

    try:
        return FinancialAnalysis.model_validate_json(response_text)
    except ValidationError as error:
        raise LLMInvalidResponseError("Gemini returned invalid analysis JSON.") from error


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


@app.post("/api/ai/analyze-transactions")
@app.post("/api/ai/analyze-transactions/", include_in_schema=False)
async def analyze_transactions(request: Request) -> dict[str, Any]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker

    async with sessionmaker() as session:
        result = await session.execute(
            select(Transaction, Category)
            .join(Category, Transaction.category_id == Category.id)
            .order_by(Transaction.created_at.asc())
        )
        transactions = [
            transaction_to_ai_payload(transaction, category)
            for transaction, category in result.all()
        ]

    if not transactions:
        raise HTTPException(
            status_code=404,
            detail="Немає транзакцій для AI-аналізу.",
        )

    llm_api_key = request.app.state.llm_api_key
    if not llm_api_key:
        raise HTTPException(
            status_code=503,
            detail="LLM_API_KEY не налаштований. Додайте ключ у .env.",
        )

    prompt = build_financial_analysis_prompt(transactions)

    try:
        analysis = await asyncio.to_thread(
            request_financial_analysis_from_llm,
            llm_api_key,
            request.app.state.llm_model,
            prompt,
        )
    except LLMAuthenticationError:
        logging.warning("Gemini authentication failed")
        raise HTTPException(
            status_code=401,
            detail="Gemini не прийняв LLM_API_KEY. Перевірте .env.",
        ) from None
    except LLMModelError:
        logging.warning("Gemini model rejected: %s", request.app.state.llm_model)
        raise HTTPException(
            status_code=503,
            detail="Gemini не прийняв LLM_MODEL. Перевірте .env.",
        ) from None
    except LLMInvalidResponseError:
        logging.warning("Gemini returned invalid financial analysis JSON")
        raise HTTPException(
            status_code=502,
            detail="Gemini повернув некоректний JSON. Спробуйте ще раз.",
        ) from None
    except LLMUnavailableError as error:
        logging.warning("Gemini unavailable: %s", str(error))
        raise HTTPException(
            status_code=503,
            detail="Gemini тимчасово недоступний. Спробуйте пізніше.",
        ) from None

    return analysis.model_dump()


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
