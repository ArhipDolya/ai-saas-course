import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

dp = Dispatcher()

WELCOME_TEXT = (
    "Вітаю! 👋\n\n"
    "Я — твій фінансовий помічник. Зараз це базова версія бота: "
    "я допоможу почати роботу та поясню доступні можливості.\n\n"
    "Надішли /help, щоб побачити список команд."
)

HELP_TEXT = (
    "ℹ️ Довідка про бота\n\n"
    "Це навчальний фінансовий бот. Наразі він має базові команди:\n\n"
    "/start — привітатися та розпочати роботу\n"
    "/help — переглянути цю довідку\n\n"
    "Нові фінансові можливості можна буде додати наступними кроками."
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


async def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не задано. Додайте токен до локального файлу .env."
        )

    async with Bot(token=token) as bot:
        bot_user = await bot.get_me()
        logging.info("Бот @%s успішно запущений", bot_user.username)
        await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(main())
