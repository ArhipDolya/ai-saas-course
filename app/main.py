import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv


dp = Dispatcher()


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer("Привіт! Я мінімальний Telegram-бот.")


@dp.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "Я вмію відповідати на базові команди:\n\n"
        "/start - привітання\n"
        "/help - список доступних команд"
    )


async def main() -> None:
    load_dotenv()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Add it to .env or environment variables.")

    bot = Bot(token=token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
