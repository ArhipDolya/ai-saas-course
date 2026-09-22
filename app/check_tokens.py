import logging

import tiktoken
from google import genai

from app.ai_analysis import build_transaction_analysis_input, estimate_tokens_with_tiktoken
from app.check_gemini_api_key import get_gemini_api_key
from app.prompts import get_transaction_analysis_prompt

logging.basicConfig(level=logging.INFO)

def main():
    print("=== ПІДГОТОВКА ДАНИХ ===")
    system_prompt = get_transaction_analysis_prompt()
    
    # Фейкові транзакції для перевірки
    sample_transactions = [
        {"type": "income", "amount": "50000.00", "category": "Зарплата", "description": "Аванс за вересень", "date": "2026-09-01"},
        {"type": "expense", "amount": "150.00", "category": "Кава", "description": "", "date": "2026-09-02"},
        {"type": "expense", "amount": "1200.00", "category": "Продукти", "description": "Сільпо", "date": "2026-09-03"},
        {"type": "expense", "amount": "500.00", "category": "Таксі", "description": "Уклон додому", "date": "2026-09-04"},
    ]
    
    input_content = build_transaction_analysis_input(sample_transactions)
    full_text = system_prompt + "\n" + input_content

    print("Дані згенеровано. Промпт + 4 транзакції.\n")

    print("=== 1. ЛОКАЛЬНА ОЦІНКА ЧЕРЕЗ TIKTOKEN ===")
    tokens_tiktoken = estimate_tokens_with_tiktoken(full_text)
    print(f"tiktoken (o200k_base): {tokens_tiktoken} токенів\n")

    print("=== 2. ТОЧНИЙ ПІДРАХУНОК ЧЕРЕЗ GEMINI API (count_tokens) ===")
    try:
        # Цей запит робить лише математичний підрахунок токенів на стороні Google, 
        # він не генерує текст, тому працює швидше і частіше буває доступним.
        client = genai.Client(api_key=get_gemini_api_key())
        response = client.models.count_tokens(
            model="gemini-3.6-flash",
            contents=full_text,
        )
        print(f"Gemini API: {response.total_tokens} токенів")
    except Exception as e:
        print(f"Gemini API недоступне (ймовірно, 503 помилка триває): {e}")

if __name__ == "__main__":
    main()

