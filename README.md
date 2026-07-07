# Telegram-бот

Мінімальний Telegram-бот на `aiogram 3.x`.

## Налаштування

1. Створи токен для бота в Telegram через BotFather.
2. Додай токен у файл `.env`:

```env
BOT_TOKEN=your_real_token_here
DATABASE_URL=postgresql://your_database_connection_string
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
- `/expense 120 кава` - зберегти витрату в базу даних.
- `/show-expenses` - показати всі збережені витрати.

## Перевірка бази даних

```bash
python -m app.check_database
```

## Docker

Запуск бота:

```bash
docker compose up --build
```
