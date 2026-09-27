from google import genai

from app.ai_analysis import GEMINI_MODELS
from app.check_gemini_api_key import get_gemini_api_key


def main():
    client = genai.Client(api_key=get_gemini_api_key())
    try:
        for model_name in GEMINI_MODELS:
            print(f"Testing {model_name}...")
            try:
                client.models.generate_content(model=model_name, contents="Hi")
                print(f"Success with {model_name}!")
            except Exception as error:
                print(f"Error with {model_name}: {type(error).__name__}")
    finally:
        client.close()

if __name__ == "__main__":
    main()
