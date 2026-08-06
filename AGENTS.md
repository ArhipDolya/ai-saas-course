# Правила проєкту

## Архітектура

- `frontend/` - React + Vite адмінка. Вона звертається тільки до backend через
  відносні URL `/api/...`.
- `app/` - FastAPI API, Telegram-бот, SQLAlchemy-моделі та інтеграції з Neon і
  Gemini.
- Frontend не звертається напряму до Neon, Gemini або змінних середовища.
- `Dockerfile.render` збирає frontend і віддає його разом з API через
  `app.render_app`.

## Команди

- Встановити залежності для перевірок:
  `./.venv/bin/python -m pip install -r requirements-dev.txt`
- Встановити frontend-залежності: `npm ci --prefix frontend`
- Запустити весь локальний preflight: `./scripts/preflight.sh`
- Запустити локальні сервіси: `docker compose up --build`

## Секрети

- Реальні `BOT_TOKEN`, `DATABASE_URL`, `ADMIN_PASSWORD` і `LLM_API_KEY`
  зберігаються лише в локальному `.env` або у secret storage середовища
  деплою.
- `.env` не можна читати, друкувати в логах або додавати в Git.
- `.env.example` містить лише безпечні шаблонні значення.
- Перед комітом або передачею проєкту запускай secret scan через preflight.

## API

- API - це контракт між frontend і backend. Не додавай, не перейменовуй і не
  змінюй маршрути, HTTP-методи або JSON-поля без явної задачі.
- Якщо контракт API змінюється за погодженою задачею, онови backend, frontend
  і документацію в одному наборі змін.
- Публічний frontend не отримує API keys, `DATABASE_URL` або інші секрети.

## Preflight

- Перед завершенням задачі, комітом або деплоєм запускай
  `./scripts/preflight.sh`.
- Preflight у фіксованому порядку перевіряє Python compile, React build,
  production Docker build, secret scan і статичну конфігурацію.
- Не оголошуй задачу готовою, доки preflight не завершився успішно або причина
  пропуску не зафіксована явно.
