import asyncio
import logging
import os
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import (
    check_database_connection,
    create_database_engine,
    create_database_tables,
    create_sessionmaker,
)
from app.models import Category, Transaction, TransactionType, User


dp = Dispatcher()


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer("Привіт! Я мінімальний Telegram-бот.")


@dp.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "Я вмію відповідати на базові команди:\n\n"
        "/start - привітання\n"
        "/help - список доступних команд\n"
        "/expense 120 кава - зберегти витрату\n"
        "/show-expenses - показати всі витрати\n"
        "/show_expenses - показати всі витрати"
    )


@dp.message(Command("expense"))
async def expense_handler(
    message: Message,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    if not message.text or not message.from_user:
        await message.answer("Не можу обробити команду. Спробуй: /expense 120 кава")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /expense 120 кава")
        return

    amount_raw = parts[1].replace(",", ".")
    category_name = parts[2].strip()
    if not category_name:
        await message.answer("Додай категорію. Наприклад: /expense 120 кава")
        return

    try:
        amount = Decimal(amount_raw)
    except InvalidOperation:
        await message.answer("Сума має бути числом. Наприклад: /expense 120 кава")
        return

    if amount <= 0:
        await message.answer("Сума має бути більшою за 0.")
        return

    async with sessionmaker() as session:
        user = await get_or_create_user(session, message.from_user)
        category = await get_or_create_category(
            session=session,
            user_id=user.id,
            name=category_name,
            transaction_type=TransactionType.expense,
        )
        transaction = Transaction(
            user_id=user.id,
            category_id=category.id,
            amount=amount,
        )
        session.add(transaction)
        await session.commit()

    await message.answer(f"Все зберіг: {amount} - {category_name}")


@dp.message(Command("show_expenses"))
@dp.message(F.text == "/show-expenses")
async def show_expenses_handler(
    message: Message,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    if not message.from_user:
        await message.answer("Не можу визначити користувача.")
        return

    async with sessionmaker() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        if not user:
            await message.answer("У тебе ще немає збережених витрат.")
            return

        result = await session.execute(
            select(Transaction, Category)
            .join(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.user_id == user.id,
                Category.type == TransactionType.expense,
            )
            .order_by(Transaction.created_at.desc())
        )
        expenses = result.all()

    if not expenses:
        await message.answer("У тебе ще немає збережених витрат.")
        return

    total = sum((transaction.amount for transaction, _ in expenses), Decimal("0"))
    lines = ["Твої витрати:", ""]

    for transaction, category in expenses:
        created_at = transaction.created_at.strftime("%d.%m.%Y %H:%M")
        lines.append(f"- {created_at} | {transaction.amount} | {category.name}")

    lines.extend(["", f"Разом: {total}"])

    for chunk in split_message("\n".join(lines)):
        await message.answer(chunk)


def split_message(text: str, max_length: int = 3500) -> list[str]:
    chunks: list[str] = []
    current = ""

    for line in text.splitlines():
        next_part = f"{current}\n{line}" if current else line
        if len(next_part) > max_length:
            chunks.append(current)
            current = line
        else:
            current = next_part

    if current:
        chunks.append(current)

    return chunks


async def get_or_create_user(session: AsyncSession, telegram_user) -> User:
    user = await session.scalar(
        select(User).where(User.telegram_id == telegram_user.id)
    )
    if user:
        user.username = telegram_user.username
        user.first_name = telegram_user.first_name
        user.last_name = telegram_user.last_name
        return user

    user = User(
        telegram_id=telegram_user.id,
        username=telegram_user.username,
        first_name=telegram_user.first_name,
        last_name=telegram_user.last_name,
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


async def main() -> None:
    load_dotenv()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Add it to .env or environment variables.")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env or environment variables.")

    engine = create_database_engine(database_url)
    await check_database_connection(engine)
    await create_database_tables(engine)
    logging.info("Database connection OK")
    sessionmaker = create_sessionmaker(engine)

    bot = Bot(token=token)
    try:
        await dp.start_polling(bot, sessionmaker=sessionmaker)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
