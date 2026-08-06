# Telegram-бот

Мінімальний Telegram-бот на `aiogram 3.x`.

## Налаштування

1. Створи токен для бота в Telegram через BotFather.
2. Додай токен у файл `.env`:

```env
BOT_TOKEN=your_real_token_here
DATABASE_URL=postgresql://your_database_connection_string
ADMIN_PASSWORD=your_admin_password_here
LLM_API_KEY=your_gemini_api_key_here
LLM_MODEL=gemini-3.1-flash-lite
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

## Перевірка LLM API

Файл `app/check_llm.py` відправляє тестовий промпт у Gemini через ключ `LLM_API_KEY` з `.env`.

```bash
python -m app.check_llm
```

## Перевірка токенів

Файл `app/check_tokens.py` бере фінансові операції з Neon і рахує, скільки токенів займають JSON з операціями та повний AI prompt.

```bash
python -m app.check_tokens
```

## API

Запуск API:

```bash
uvicorn app.api:app --reload
```

Endpoint для майбутнього React-фронтенду:

```text
GET /healthz
POST /api/admin/verify-password
GET /api/transactions/
POST /api/transactions
DELETE /api/transactions/{id}
GET /api/summary/
POST /api/ai/analyze-transactions
POST /api/ai/chat
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

POST `/api/ai/analyze-transactions` читає всі транзакції з Neon, відправляє їх у Gemini через `LLM_API_KEY` і повертає JSON:

```json
{
  "summary": "Короткий загальний висновок",
  "top_expense_categories": ["Їжа", "Транспорт", "Кава"],
  "risks": ["Витрати на каву зростають"],
  "advice": ["Встановити ліміт на каву"]
}
```

POST `/api/ai/chat` приймає нове повідомлення та необов'язковий `thread_id`:

```json
{
  "message": "Привіт! Чим ти можеш допомогти?",
  "thread_id": null
}
```

Відповідь містить текст AI і UUID діалогу:

```json
{
  "answer": "Коротка відповідь AI",
  "thread_id": "uuid діалогу"
}
```

Для локальної розробки чат зберігає короткочасну історію через LangGraph `InMemorySaver`. Пам'ять ізольована за `thread_id`, тому наступне повідомлення з тим самим ідентифікатором бачить попередню розмову та результати виконаних tools. Після restart backend пам'ять очищується.

Для питань про фінансові операції AI-чат може викликати тільки read-only tools на backend:

- `get_transactions_summary(period)` - доходи, витрати, баланс і кількість операцій;
- `get_category_totals(period)` - витрати за категоріями;
- `get_top_expenses(period, limit)` - найбільші окремі витрати;
- `get_recent_transactions(period, limit)` - останні доходи й витрати.

Підтримувані значення `period`: `current_month`, `previous_month`, `last_30_days` або місяць у форматі `YYYY-MM`. Для `limit` доступні значення від `1` до `20`. Tools не створюють, не редагують і не видаляють операції, а frontend отримує лише фінальну текстову відповідь AI.

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

## Local preflight

Preflight - одна команда для основних передкомітних перевірок. Він не запускає
бота, не звертається до Neon або Gemini та не читає реальний `.env`.

Одноразова підготовка:

```bash
./.venv/bin/python -m pip install -r requirements-dev.txt
npm ci --prefix frontend
```

Запуск:

```bash
./scripts/preflight.sh
```

Порядок перевірок: Python compile -> React build -> production Docker build ->
secret scan -> configuration.

## CI та rollback

Workflow [preflight.yml](.github/workflows/preflight.yml) запускає той самий
`./scripts/preflight.sh` для pull request у `main` і для push у `main`. У GitHub
увімкни branch protection для `main` та вимагай успішний check `Preflight` перед
merge.

Інструкція безпечного відновлення production через `git revert` знаходиться в
[docs/rollback.md](docs/rollback.md).

## Deploy на Render

У репозиторії є `Dockerfile.render` для production: він збирає React у першому
етапі, після чого FastAPI віддає frontend і `/api` з одного домену. Через це
frontend використовує відносні `/api/...` URL без окремого CORS налаштування.

1. Закоміть і запуш зміни в GitHub.
2. У Render обери `New` -> `Web Service` і підключи репозиторій.
3. Вкажи такі параметри:

   ```text
   Runtime: Docker
   Dockerfile Path: Dockerfile.render
   Docker Context Directory: .
   Health Check Path: /healthz
   ```

4. Під час створення введи секретні значення `DATABASE_URL`, `ADMIN_PASSWORD` і
   `LLM_API_KEY`. Не додавай їх у Git.
5. Після deploy відкрий URL сервісу. Frontend буде доступний у корені, а
   перевірка готовності - за шляхом `/healthz`.

`/healthz` виконує простий `SELECT 1` до Neon. Render вважає endpoint здоровим,
коли він повертає `2xx`.

Free Web Service може заснути після періоду без вхідних HTTP-запитів, тому перше
відкриття після простою може тривати довше. AI-чат використовує `InMemorySaver`,
отже його історія за `thread_id` очищується після restart або засинання сервісу;
транзакції в Neon при цьому не втрачаються.

### Telegram-бот на Render

Не запускай polling бота в тому самому Web Service: HTTP healthcheck і Telegram
polling мають різні життєві цикли. Для бота створи окремий `Background Worker`
у Render з такими параметрами:

```text
Runtime: Docker
Dockerfile Path: Dockerfile.render
Docker Context Directory: .
Docker Command: python -m app.main
```

Для worker задай `BOT_TOKEN` і `DATABASE_URL`. Background Worker не має
безкоштовного плану в Render. Перед його запуском зупини локальний або інший
запущений екземпляр бота, щоб Telegram не повернув `Conflict` через два
одночасні `getUpdates` polling-процеси.
