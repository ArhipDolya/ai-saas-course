import asyncio
import logging
from app.ai_analysis import generate_transaction_analysis

logging.basicConfig(level=logging.INFO)

async def main():
    sample_transactions = [
        {"type": "income", "amount": "50000.00", "category": "Зарплата", "description": "Аванс за вересень", "date": "2026-09-01"},
        {"type": "expense", "amount": "150.00", "category": "Кава", "description": "", "date": "2026-09-02"},
        {"type": "expense", "amount": "1200.00", "category": "Продукти", "description": "Сільпо", "date": "2026-09-03"},
        {"type": "expense", "amount": "500.00", "category": "Таксі", "description": "Уклон додому", "date": "2026-09-04"},
    ]
    try:
        response = await generate_transaction_analysis(sample_transactions)
        print(f"Success! Response: {response}")
    except Exception as e:
        print(f"Failed! Exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())

