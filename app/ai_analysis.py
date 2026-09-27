import asyncio
import json
import logging
import random
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
TRANSACTION_ANALYSIS_PROMPT = get_transaction_analysis_prompt()
GEMINI_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
)
GEMINI_REQUEST_TIMEOUT_SECONDS = 15
GEMINI_TOTAL_TIMEOUT_SECONDS = 50
GEMINI_RETRY_DELAYS_SECONDS = (1.0, 2.0)
GEMINI_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


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


class GeminiUnavailableError(GeminiAnalysisError):
    """Gemini тимчасово недоступний після обмежених повторних спроб."""


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


def _generation_config(*, structured: bool) -> types.GenerateContentConfig:
    config: dict[str, Any] = {
        "system_instruction": TRANSACTION_ANALYSIS_PROMPT,
        "temperature": 0.2,
        "thinking_config": types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.LOW,
        ),
        # Retries are coordinated below across models and request modes so the
        # whole HTTP request stays within one predictable deadline.
        "http_options": types.HttpOptions(
            timeout=GEMINI_REQUEST_TIMEOUT_SECONDS * 1_000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    }
    if structured:
        config.update(
            response_mime_type="application/json",
            response_json_schema=TransactionAnalysisResponse.model_json_schema(),
        )
    return types.GenerateContentConfig(**config)


def _parse_analysis_response(response: Any) -> TransactionAnalysisResponse:
    if isinstance(response.parsed, TransactionAnalysisResponse):
        return response.parsed
    if isinstance(response.parsed, dict):
        return TransactionAnalysisResponse.model_validate(response.parsed)

    response_text = (response.text or "").strip()
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        response_text = "\n".join(lines).strip()

    return TransactionAnalysisResponse.model_validate_json(response_text)


def _is_transient_error(error: Exception) -> bool:
    if isinstance(error, (OSError, TimeoutError)):
        return True
    return getattr(error, "code", None) in GEMINI_TRANSIENT_STATUS_CODES


def _log_usage(response: Any) -> None:
    if response.usage_metadata:
        logger.info(
            "Gemini actual usage: prompt=%s, candidates=%s, total=%s",
            response.usage_metadata.prompt_token_count,
            response.usage_metadata.candidates_token_count,
            response.usage_metadata.total_token_count,
        )


async def generate_transaction_analysis(
    transactions: Sequence[dict[str, Any]],
) -> TransactionAnalysisResponse:
    """Надсилає транзакції до Gemini та перевіряє структуровану відповідь."""
    client = genai.Client(api_key=get_gemini_api_key())
    async_client = client.aio

    input_content = build_transaction_analysis_input(transactions)

    last_error: Exception | None = None
    saw_transient_error = False
    # First keep the strict server-side schema. If Gemini's structured path is
    # overloaded, retry via ordinary text generation and validate JSON locally.
    request_modes = (True, False, False)
    try:
        async with asyncio.timeout(GEMINI_TOTAL_TIMEOUT_SECONDS):
            for mode_index, structured in enumerate(request_modes):
                mode_had_transient_error = False
                for model_name in GEMINI_MODELS:
                    try:
                        async with asyncio.timeout(GEMINI_REQUEST_TIMEOUT_SECONDS):
                            response = await async_client.models.generate_content(
                                model=model_name,
                                contents=input_content,
                                config=_generation_config(structured=structured),
                            )
                        analysis = _parse_analysis_response(response)
                    except errors.APIError as error:
                        is_transient = _is_transient_error(error)
                        mode_had_transient_error |= is_transient
                        saw_transient_error |= is_transient
                        logger.warning(
                            "Gemini model %s failed: mode=%s, error_type=%s, "
                            "http_status=%s, detail=%s",
                            model_name,
                            "structured" if structured else "plain_json",
                            type(error).__name__,
                            getattr(error, "code", None),
                            str(getattr(error, "message", ""))[:200],
                        )
                        last_error = error
                        continue
                    except (OSError, TimeoutError) as error:
                        mode_had_transient_error = True
                        saw_transient_error = True
                        logger.warning(
                            "Gemini model %s failed: mode=%s, error_type=%s",
                            model_name,
                            "structured" if structured else "plain_json",
                            type(error).__name__,
                        )
                        last_error = error
                        continue
                    except (ValidationError, ValueError) as error:
                        logger.warning(
                            "Gemini model %s returned invalid analysis: mode=%s, "
                            "error_type=%s",
                            model_name,
                            "structured" if structured else "plain_json",
                            type(error).__name__,
                        )
                        last_error = error
                        continue

                    logger.info(
                        "Gemini successfully generated response using model: %s, mode=%s",
                        model_name,
                        "structured" if structured else "plain_json",
                    )
                    _log_usage(response)
                    return analysis

                if mode_index >= len(request_modes) - 1:
                    break

                # Always try plain JSON once after the structured pass. A second
                # plain pass is useful only for retryable provider/network errors.
                if mode_index > 0 and not mode_had_transient_error:
                    break
                if mode_had_transient_error:
                    delay = GEMINI_RETRY_DELAYS_SECONDS[mode_index]
                    await asyncio.sleep(delay + random.uniform(0, delay * 0.25))
    except TimeoutError as error:
        saw_transient_error = True
        last_error = error
    finally:
        await async_client.aclose()
        client.close()

    logger.error(
        "All Gemini analysis attempts failed: error_type=%s, transient=%s",
        type(last_error).__name__ if last_error else "unknown",
        saw_transient_error,
    )
    if saw_transient_error:
        raise GeminiUnavailableError from last_error
    raise GeminiAnalysisError from last_error
