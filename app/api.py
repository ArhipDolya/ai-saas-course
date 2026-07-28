import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path as FilePath
from secrets import compare_digest
from typing import Any, AsyncIterator
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Path, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_chat_graph import (
    AIChatRequest,
    AIChatResponse,
    ChatConfigurationError,
    ChatLLMError,
    FinanceChatGraph,
    GeminiChatResponder,
    resolve_thread_id,
)
from app.chat_tools import FinanceChatToolExecutor
from app.check_llm import DEFAULT_LLM_MODEL
from app.database import (
    check_database_connection,
    create_database_engine,
    create_database_tables,
    create_sessionmaker,
)
from app.finance_tools import FinanceTools
from app.models import Category, PendingActionStatus, Transaction, User
from app.pending_actions import (
    PendingActionNotFoundError,
    PendingActionPayloadError,
    PendingActionService,
    PendingActionStatusError,
    PendingActionTargetNotFoundError,
    UnsupportedPendingActionError,
)
from app.transaction_service import (
    TransactionCreate,
    create_admin_transaction,
    transaction_to_json,
)

ADMIN_AUTH_HEADER = "X-Admin-Auth"
FINANCIAL_ANALYSIS_PROMPT_PATH = (
    FilePath(__file__).resolve().parent.parent
    / "prompts"
    / "financial_analysis_prompt.txt"
)


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
    app.state.pending_actions = PendingActionService(app.state.sessionmaker)
    app.state.ai_chat_graph = FinanceChatGraph(
        GeminiChatResponder(
            api_key=app.state.llm_api_key,
            model=app.state.llm_model,
        ),
        FinanceChatToolExecutor(
            FinanceTools(app.state.sessionmaker),
            app.state.pending_actions,
        ),
    )
    logging.info("API database connection OK")

    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="Finance Telegram Bot API", lifespan=lifespan)


class AdminPasswordPayload(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class FinancialAnalysis(BaseModel):
    summary: str
    top_expense_categories: list[str]
    risks: list[str]
    advice: list[str]


class PendingActionDecisionPayload(BaseModel):
    thread_id: UUID


class PendingActionConfirmResponse(BaseModel):
    action_id: int
    status: PendingActionStatus
    result: dict[str, Any]


class PendingActionCancelResponse(BaseModel):
    action_id: int
    status: PendingActionStatus


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
    prompt_template = FINANCIAL_ANALYSIS_PROMPT_PATH.read_text(encoding="utf-8")

    return prompt_template.replace("{{transactions_json}}", transactions_json).strip()


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
        async with session.begin():
            transaction, category, user = await create_admin_transaction(session, payload)

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


@app.post("/api/ai/chat", response_model=AIChatResponse)
@app.post("/api/ai/chat/", response_model=AIChatResponse, include_in_schema=False)
async def chat_with_ai(payload: AIChatRequest, request: Request) -> AIChatResponse:
    thread_id = resolve_thread_id(payload.thread_id)
    chat_graph: FinanceChatGraph = request.app.state.ai_chat_graph

    try:
        chat_response = await chat_graph.respond(payload.message, thread_id)
    except ChatConfigurationError:
        raise HTTPException(
            status_code=503,
            detail="AI-сервіс тимчасово недоступний. Спробуйте пізніше.",
        ) from None
    except ChatLLMError as error:
        logging.warning("AI chat request failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail="AI-сервіс тимчасово недоступний. Спробуйте пізніше.",
        ) from None
    except Exception as error:
        logging.exception("Unexpected AI chat error: %s", type(error).__name__)
        raise HTTPException(
            status_code=500,
            detail="Не вдалося обробити повідомлення AI. Спробуйте ще раз.",
        ) from None

    return AIChatResponse(
        answer=chat_response.answer,
        thread_id=thread_id,
        pending_action=chat_response.pending_action,
    )


@app.post(
    "/api/ai/actions/{action_id}/confirm",
    response_model=PendingActionConfirmResponse,
)
@app.post(
    "/api/ai/actions/{action_id}/confirm/",
    response_model=PendingActionConfirmResponse,
    include_in_schema=False,
)
async def confirm_ai_action(
    payload: PendingActionDecisionPayload,
    request: Request,
    action_id: int = Path(gt=0),
    admin_password: str = Header(default="", alias=ADMIN_AUTH_HEADER),
) -> PendingActionConfirmResponse:
    require_admin_password(request, admin_password)
    pending_actions: PendingActionService = request.app.state.pending_actions

    try:
        confirmed_action = await pending_actions.confirm(
            action_id=action_id,
            thread_id=str(payload.thread_id),
        )
    except PendingActionNotFoundError:
        raise HTTPException(status_code=404, detail="Чернетку дії не знайдено.") from None
    except PendingActionStatusError:
        raise HTTPException(
            status_code=409,
            detail="Цю дію вже підтверджено або скасовано.",
        ) from None
    except PendingActionPayloadError:
        raise HTTPException(
            status_code=422,
            detail="Payload чернетки дії некоректний.",
        ) from None
    except PendingActionTargetNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Операцію для цієї дії не знайдено.",
        ) from None
    except UnsupportedPendingActionError:
        raise HTTPException(
            status_code=422,
            detail="Цей тип дії ще не підтримує підтвердження.",
        ) from None

    return PendingActionConfirmResponse(
        action_id=confirmed_action.action.action_id,
        status=PendingActionStatus(confirmed_action.action.status),
        result=confirmed_action.result,
    )


@app.post(
    "/api/ai/actions/{action_id}/cancel",
    response_model=PendingActionCancelResponse,
)
@app.post(
    "/api/ai/actions/{action_id}/cancel/",
    response_model=PendingActionCancelResponse,
    include_in_schema=False,
)
async def cancel_ai_action(
    payload: PendingActionDecisionPayload,
    request: Request,
    action_id: int = Path(gt=0),
    admin_password: str = Header(default="", alias=ADMIN_AUTH_HEADER),
) -> PendingActionCancelResponse:
    require_admin_password(request, admin_password)
    pending_actions: PendingActionService = request.app.state.pending_actions

    try:
        action = await pending_actions.cancel(
            action_id=action_id,
            thread_id=str(payload.thread_id),
        )
    except PendingActionNotFoundError:
        raise HTTPException(status_code=404, detail="Чернетку дії не знайдено.") from None
    except PendingActionStatusError:
        raise HTTPException(
            status_code=409,
            detail="Цю дію вже підтверджено або скасовано.",
        ) from None

    return PendingActionCancelResponse(
        action_id=action.action_id,
        status=PendingActionStatus(action.status),
    )


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
