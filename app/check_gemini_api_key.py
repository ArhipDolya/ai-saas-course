import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
REQUEST_TIMEOUT_SECONDS = 20


def get_gemini_api_key() -> str:
    """Повертає Gemini API key з локального .env або змінних середовища."""
    load_dotenv(PROJECT_ROOT / ".env")
    configured_value = os.getenv("GEMINI_API_KEY", "").strip()
    if not configured_value:
        raise ValueError("GEMINI_API_KEY не задано у .env")

    return configured_value


def check_gemini_api_key() -> None:
    """Перевіряє, що Gemini API key приймається API і має generateContent моделі."""
    query = urllib.parse.urlencode({"key": get_gemini_api_key()})
    request = urllib.request.Request(
        f"{GEMINI_MODELS_URL}?{query}",
        headers={"Accept": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))

    models = payload.get("models", [])
    generation_models = [
        model.get("name")
        for model in models
        if "generateContent" in model.get("supportedGenerationMethods", [])
    ]

    if not generation_models:
        raise RuntimeError("Gemini API key працює, але generateContent моделі недоступні")

    logging.info(
        "Gemini API key успішно перевірено: models=%s, generate_content_models=%s, sample_model=%s",
        len(models),
        len(generation_models),
        generation_models[0],
    )


def main() -> int:
    try:
        check_gemini_api_key()
    except urllib.error.HTTPError as error:
        error_status = "unknown"
        try:
            payload = json.loads(error.read().decode("utf-8"))
            error_status = payload.get("error", {}).get("status", error_status)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        logging.error(
            "Не вдалося перевірити Gemini API key: http_status=%s, error_status=%s",
            error.code,
            error_status,
        )
        return 1
    except (OSError, RuntimeError, ValueError) as error:
        logging.error(
            "Не вдалося перевірити Gemini API key: %s",
            error.__class__.__name__,
        )
        return 1

    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    raise SystemExit(main())
