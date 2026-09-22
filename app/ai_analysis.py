import json
import logging
from collections.abc import Sequence
from typing import Any

import tiktoken
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from app.check_gemini_api_key import get_gemini_api_key
from app.prompts import get_transaction_analysis_prompt
from app.schemas import TransactionAnalysisResponse

logger = logging.getLogger("uvicorn.error")


def estimate_tokens_with_tiktoken(text: str) -> int:
    """Приблизна оцінка кількості токенів за допомогою tiktoken (алгоритм OpenAI)."""
    try:
        # o200k_base - це найновіше кодування OpenAI (використовується в GPT-4o)
        encoding = tiktoken.get_encoding("o200k_base")
        return len(encoding.encode(text))
    except Exception as e:
        logger.warning("Не вдалося підрахувати токени через tiktoken: %s", e)
        return 0


class GeminiAnalysisError(RuntimeError):
    """Gemini не зміг повернути валідний аналіз транзакцій."""


def build_transaction_analysis_input(transactions: Sequence[dict[str, Any]]) -> str:
    """Готує транзакції одного користувача як дані для Gemini."""
    serialized_transactions = json.dumps(
        list(transactions),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "Проаналізуй усі наведені нижче транзакції одного користувача. "
        "Масив відсортовано від найстарішої операції до найновішої.\n"
        f"<transactions_json>{serialized_transactions}</transactions_json>"
    )


async def generate_transaction_analysis(
    transactions: Sequence[dict[str, Any]],
) -> TransactionAnalysisResponse:
    """Надсилає транзакції до Gemini та перевіряє структуровану відповідь."""
    client = genai.Client(api_key=get_gemini_api_key())
    async_client = client.aio

    # Підготовка текстів для запиту та підрахунку токенів
    system_prompt = get_transaction_analysis_prompt()
    input_content = build_transaction_analysis_input(transactions)
    
    # Оцінюємо загальну кількість токенів (промпт + дані) за допомогою tiktoken
    estimated_tokens = estimate_tokens_with_tiktoken(system_prompt + "\n" + input_content)
    logger.info("tiktoken: приблизна оцінка вхідних токенів = %d", estimated_tokens)

    FALLBACK_MODELS = [
        "gemini-3-flash-preview",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    ]
    
    last_error = None
    response = None
    
    for model_name in FALLBACK_MODELS:
        try:
            response = await async_client.models.generate_content(
                model=model_name,
                contents=input_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_json_schema=TransactionAnalysisResponse.model_json_schema(),
                    temperature=0.2,
                ),
            )
            logger.info("Gemini successfully generated response using model: %s", model_name)
            break
        except errors.APIError as error:
            logger.warning(
                "Gemini model %s failed: error_type=%s, http_status=%s",
                model_name,
                type(error).__name__,
                getattr(error, "code", None),
            )
            last_error = error
        except OSError as error:
            logger.warning(
                "Gemini model %s failed: error_type=%s",
                model_name,
                type(error).__name__,
            )
            last_error = error

    if not response:
        await async_client.aclose()
        client.close()
        logger.error("All Gemini models failed. Last error: %s", last_error)
        if last_error:
            raise GeminiAnalysisError from last_error
        else:
            raise GeminiAnalysisError("All models failed")

    try:
        # Логуємо реальну статистику витрат від Gemini (100% точну)
        if response.usage_metadata:
            logger.info(
                "Gemini actual usage: prompt=%d, candidates=%d, total=%d",
                response.usage_metadata.prompt_token_count,
                response.usage_metadata.candidates_token_count,
                response.usage_metadata.total_token_count,
            )
    finally:
        await async_client.aclose()
        client.close()

    try:
        if isinstance(response.parsed, TransactionAnalysisResponse):
            return response.parsed
        return TransactionAnalysisResponse.model_validate_json(response.text or "")
    except (ValidationError, ValueError) as error:
        logger.error(
            "Gemini returned invalid transaction analysis: error_type=%s",
            type(error).__name__,
        )
        raise GeminiAnalysisError from error
