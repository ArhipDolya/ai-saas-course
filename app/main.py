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
from app.expenses import ExpenseValidationError, parse_expense, save_expense

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
    "/expense <сума> <категорія> - додати витрату\n\n"
    "Приклад: /expense 120 кава"
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


@dp.message(Command("expense"))
async def expense_handler(message: Message, command: CommandObject) -> None:
    """Зберігає витрату, передану як /expense <сума> <категорія>."""
    log_command(message, "/expense")

    if message.from_user is None:
        await message.answer("Не вдалося визначити користувача для цієї витрати.")
        return

    try:
        expense = parse_expense(command.args)
    except ExpenseValidationError as error:
        await message.answer(str(error))
        return

    try:
        transaction = await save_expense(
            telegram_id=message.from_user.id,
            expense=expense,
        )
    except SQLAlchemyError as error:
        logging.error(
            "Не вдалося записати витрату: error_type=%s",
            error.__class__.__name__,
        )
        await message.answer("Не вдалося зберегти витрату. Спробуй ще раз трохи пізніше.")
        return

    logging.info(
        "Витрату записано: user_id=%s, transaction_id=%s",
        message.from_user.id,
        transaction.id,
    )
    await message.answer(
        "✅ Витрату записано:\n"
        f"Сума: {format_amount(expense.amount)} грн\n"
        f"Категорія: {expense.category_name}"
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
