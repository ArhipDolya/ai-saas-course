# Telegram-бот

Мінімальний Telegram-бот на `aiogram 3.x`.

## Налаштування

1. Створи токен для бота в Telegram через BotFather.
2. Додай токен у файл `.env`:

```env
BOT_TOKEN=your_real_token_here
```

3. Встанови залежності:

```bash
pip install -r requirements.txt
```

4. Запусти бота:

```bash
python -m app.main
```

## Команди

- `/start` - коротке привітання.
- `/help` - список доступних команд.
