import logging
import os

from dotenv import load_dotenv


DEFAULT_LLM_MODEL = "gemini-3.1-flash-lite"
TEST_PROMPT = (
    "Відповідай одним коротким реченням українською мовою: "
    "чи працює підключення до LLM API?"
)


def request_llm_response(api_key: str, model: str, prompt: str) -> str:
    try:
        from google import genai
    except ImportError as error:
        raise RuntimeError(
            "google-genai is not installed. Run: pip install -r requirements.txt"
        ) from error

    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
    except Exception as error:
        error_message = str(error)
        normalized_error = error_message.lower()
        status_code = getattr(error, "status_code", None)

        if (
            status_code in {500, 502, 503, 504}
            or "high demand" in normalized_error
            or "temporarily unavailable" in normalized_error
        ):
            raise RuntimeError(
                "Gemini API is temporarily unavailable or overloaded. Try again later."
            ) from error

        if "api key" in normalized_error:
            raise RuntimeError("Gemini rejected LLM_API_KEY. Check your .env value.") from error

        if "model" in normalized_error:
            raise RuntimeError("Gemini rejected LLM_MODEL. Check your .env value.") from error

        raise RuntimeError(f"LLM API check failed: {type(error).__name__}: {error}") from error

    response_text = getattr(response, "text", "").strip()
    if not response_text:
        raise RuntimeError("LLM returned an empty response.")

    return response_text


def main() -> None:
    load_dotenv()

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is not set. Add it to .env.")

    model = os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)
    logging.info("Checking LLM API connection with model: %s", model)

    response_text = request_llm_response(
        api_key=api_key,
        model=model,
        prompt=TEST_PROMPT,
    )

    logging.info("LLM API connection OK")
    print(f"Model: {model}")
    print(f"Response: {response_text}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
