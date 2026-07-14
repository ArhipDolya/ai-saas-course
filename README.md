# Telegram-бот

Мінімальний Telegram-бот на `aiogram 3.x`.

## Налаштування

1. Створи токен для бота в Telegram через BotFather.
2. Додай токен у файл `.env`:

```env
BOT_TOKEN=your_real_token_here
DATABASE_URL=postgresql://your_database_connection_string
ADMIN_PASSWORD=your_admin_password_here
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

## API

Запуск API:

```bash
uvicorn app.api:app --reload
```

Endpoint для майбутнього React-фронтенду:

```text
POST /api/admin/verify-password
GET /api/transactions/
POST /api/transactions
DELETE /api/transactions/{id}
GET /api/summary/
```

POST `/api/admin/verify-password` перевіряє пароль адміністратора з `.env`.

POST `/api/transactions` приймає:

```json
{
  "type": "expense",
  "amount": "120.00",
  "category": "кава",
  "description": "необов'язково",
  "date": "2026-07-14T12:00:00Z"
}
```

Для створення операції потрібно передати заголовок `X-Admin-Auth`.

DELETE `/api/transactions/{id}` видаляє операцію за додатним числовим `id`.

## Frontend

Адмінка створена на `React + Vite`.
В адмінці є режим перегляду операцій і форма створення фінансової операції.

Локальна адреса:

```text
http://localhost:5173
```

Backend API у Docker:

```text
http://localhost:8001
```

## Docker

Запуск backend API та frontend:

```bash
docker compose up --build api frontend
```

Запуск усіх сервісів:

```bash
docker compose up --build
```
