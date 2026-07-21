import asyncio
import json
import os

from dotenv import load_dotenv
from sqlalchemy import select

from app.api import build_financial_analysis_prompt, transaction_to_ai_payload
from app.database import create_database_engine, create_sessionmaker
from app.models import Category, Transaction


TOKEN_ENCODING = "o200k_base"


def count_tokens(text: str) -> int:
    try:
        import tiktoken
    except ImportError as error:
        raise RuntimeError(
            "tiktoken is not installed. Run: pip install -r requirements.txt"
        ) from error

    encoding = tiktoken.get_encoding(TOKEN_ENCODING)
    return len(encoding.encode(text))


async def main() -> None:
    load_dotenv()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env.")

    engine = create_database_engine(database_url)
    sessionmaker = create_sessionmaker(engine)

    try:
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
            raise RuntimeError("No transactions found in database.")

        transactions_json = json.dumps(transactions, ensure_ascii=False, indent=2)
        full_prompt = build_financial_analysis_prompt(transactions)

        print("Token check for financial operations")
        print(f"Encoding: {TOKEN_ENCODING}")
        print(f"Transactions count: {len(transactions)}")
        print(f"Transactions JSON tokens: {count_tokens(transactions_json)}")
        print(f"Full AI prompt tokens: {count_tokens(full_prompt)}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
