import asyncio
import logging
import os
from decimal import Decimal
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import Message
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

from app.database import dispose_database, initialize_database
from app.expenses import (
    TransactionValidationError,
    parse_transaction,
    save_transaction,
)
from app.models import TransactionType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

dp = Dispatcher()

WELCOME_TEXT = (
    "Вітаю! 👋\n\n"
    "Я - твій фінансовий помічник. Зараз це базова версія бота: "
    "я допоможу почати роботу та поясню доступні можливості.\n\n"
    "Надішли /help, щоб побачити список команд."
)

HELP_TEXT = (
    "ℹ️ Довідка про бота\n\n"
    "Це навчальний фінансовий бот. Наразі він має базові команди:\n\n"
    "/start - привітатися та розпочати роботу\n"
    "/help - переглянути цю довідку\n"
    "/expense <сума> <категорія> [| <опис>] - додати витрату\n"
    "/income <сума> <категорія> [| <опис>] - додати дохід\n\n"
    "Приклади:\n"
    "/expense 120 кава | ранкова кава\n"
    "/income 50000 зарплата | вереснева виплата"
)


def log_command(message: Message, command: str) -> None:
    """Записує отриману команду без приватного вмісту повідомлення."""
    user_id = message.from_user.id if message.from_user else None
    logging.info(
        "Отримано команду %s: user_id=%s, chat_id=%s",
        command,
        user_id,
        message.chat.id,
    )


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    """Відповідає коротким привітанням на команду /start."""
    log_command(message, "/start")
    await message.answer(WELCOME_TEXT)


@dp.message(Command("help"))
async def help_handler(message: Message) -> None:
    """Показує коротку довідку про бота та його команди."""
    log_command(message, "/help")
    await message.answer(HELP_TEXT)


def format_amount(amount: Decimal) -> str:
    """Показує суму з двома знаками після коми."""
    return f"{amount:.2f}"


async def save_transaction_from_command(
    message: Message,
    command: CommandObject,
    *,
    command_name: str,
    transaction_type: TransactionType,
    transaction_label: str,
) -> None:
    if message.from_user is None:
        await message.answer("Не вдалося визначити користувача для цієї операції.")
        return

    try:
        transaction_data = parse_transaction(command.args, command_name=command_name)
    except TransactionValidationError as error:
        await message.answer(str(error))
        return

    try:
        transaction = await save_transaction(
            telegram_id=message.from_user.id,
            transaction_data=transaction_data,
            transaction_type=transaction_type,
        )
    except SQLAlchemyError as error:
        logging.error(
            "Не вдалося записати операцію: command=%s, error_type=%s",
            command_name,
            error.__class__.__name__,
        )
        await message.answer("Не вдалося зберегти операцію. Спробуй ще раз трохи пізніше.")
        return

    logging.info(
        "Операцію записано: command=%s, user_id=%s, transaction_id=%s",
        command_name,
        message.from_user.id,
        transaction.id,
    )
    await message.answer(
        f"✅ {transaction_label} записано:\n"
        f"Сума: {format_amount(transaction_data.amount)} грн\n"
        f"Категорія: {transaction_data.category_name}"
        + (
            f"\nОпис: {transaction_data.description}"
            if transaction_data.description
            else ""
        )
    )


@dp.message(Command("expense"))
async def expense_handler(message: Message, command: CommandObject) -> None:
    """Зберігає витрату, передану через команду /expense."""
    log_command(message, "/expense")
    await save_transaction_from_command(
        message,
        command,
        command_name="/expense",
        transaction_type=TransactionType.EXPENSE,
        transaction_label="Витрату",
    )


@dp.message(Command("income"))
async def income_handler(message: Message, command: CommandObject) -> None:
    """Зберігає дохід, переданий через команду /income."""
    log_command(message, "/income")
    await save_transaction_from_command(
        message,
        command,
        command_name="/income",
        transaction_type=TransactionType.INCOME,
        transaction_label="Дохід",
    )


async def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не задано. Додайте токен до локального файлу .env."
        )

    async with Bot(token=token) as bot:
        try:
            await initialize_database()
            bot_user = await bot.get_me()
            logging.info("Бот @%s успішно запущений", bot_user.username)
            await dp.start_polling(bot)
        finally:
            await dispose_database()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(main())
