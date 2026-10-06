# Project instructions

Цей файл застосовується до всього репозиторію. Він описує поточну архітектуру
та правила роботи з нею. Якщо код і цей документ розійшлися, спочатку перевір
виконуваний код, а потім онови `AGENTS.md` у тій самій задачі.

## Working principles

- Перед зміною простеж повний шлях даних: caller -> HTTP/Telegram entry point ->
  validation -> business logic -> database or external API -> response.
- Спочатку шукай наявний precedent у сусідньому endpoint, helper або тесті.
- Роби найменшу завершену зміну. Не рефактори непов'язаний код.
- Не змінюй публічний API-контракт, схему БД, deployment entry point або формат
  AI-відповіді без явного обґрунтування та перевірки всіх споживачів.
- Перед написанням або редагуванням коду прочитай `~/.agents/CODING_STYLE.md`.
- Код, назви, коментарі, docstrings і технічні логи пиши англійською. Текст для
  користувача може бути українською.
- Типізуй усі параметри та return values. Функції з двома або більше змістовними
  параметрами роби keyword-only і викликай з keyword arguments.
- Якщо виклик не вміщується в один рядок, розташовуй по одному аргументу на
  рядок і залишай trailing comma.
- Не додавай новий test module без явної потреби. Для нового API-контракту
  доповнюй наявний відповідний test module; новий файл тестів створюй, лише якщо
  користувач явно це попросив.

## Architecture overview

```text
Telegram user -> aiogram bot --------------------\
                                                    -> shared domain logic
Browser -> React dashboard -> /api/* -> FastAPI --/          |
                                      |                       v
                                      |             SQLAlchemy async
                                      |                       |
                                      |                       v
                                      |              PostgreSQL / Neon
                                      |
                                      +-> Gemini analysis
                                      +-> LangGraph chat -> read tools
                                                           -> pending action
                                                           -> user confirm
                                                           -> database write

Render -> Dockerfile.render -> React build + Python runtime
       -> app.render_api:app -> API routes first, static frontend last
```

Проєкт має чотири runtime-частини:

1. Telegram-бот.
2. FastAPI backend.
3. React/Vite frontend.
4. Production Web Service на Render, який об'єднує frontend і backend в одному
   контейнері та на одному origin.

## Backend

### HTTP API

- `app/api.py` - основний FastAPI application і всі HTTP routes.
- Lifespan під час startup викликає `initialize_database()`, а під час shutdown -
  `dispose_database()`.
- `GET /health` є легким liveness endpoint для Render. Він не робить окремий
  SQL-запит; початкове підключення до БД уже відбувається під час startup.
- Бізнесові endpoints мають prefix `/api`. Інфраструктурні `/health`, `/docs` і
  `/openapi.json` не мають цього prefix.
- Поточна ідентифікація власника - query parameter `telegram_id`. Це тимчасова
  заміна автентифікації, тому кожен read/write запит зобов'язаний обмежувати SQL
  даними цього користувача.

Поточні endpoint groups:

- `GET /api/transactions` - список транзакцій користувача.
- `POST /api/transactions` - створення транзакції.
- `DELETE /api/transactions/{id}` - видалення лише власної транзакції.
- `GET /api/summary` - суми income, expense і balance.
- `POST /api/ai/analyze-transactions` - структурований аналіз транзакцій через
  Gemini.
- `POST /api/ai/chat` - AI-чат із thread memory та tools.
- `POST /api/ai/actions/{action_id}/confirm` - явне підтвердження підготовленої
  AI-дії.
- `POST /api/ai/actions/{action_id}/cancel` - скасування підготовленої AI-дії.

### Domain and persistence

- `app/database.py` читає `DATABASE_URL`, нормалізує його до
  `postgresql+psycopg`, ліниво створює один async engine та session factory.
- `app/models.py` містить SQLAlchemy models:
  - `User` з унікальним `telegram_id`;
  - `Category`, унікальну в межах користувача;
  - `Transaction`, яка належить користувачу та категорії й має тип `income` або
    `expense`.
- `app/schemas.py` містить Pydantic request/response contracts та їх валідацію.
- `app/transaction_rules.py` є джерелом спільних обмежень суми, довжини тексту,
  допустимої дати та business date у timezone `Europe/Kyiv`.
- `app/expenses.py` містить parsing Telegram-команд і спільний
  `save_transaction()`. API та bot повинні використовувати цю функцію замість
  дублювання створення user/category/transaction.
- Write operations використовують transaction context
  `get_session_factory().begin()`. Read operations використовують коротку async
  session. Не тримай session між HTTP requests.
- Суми зберігаються як `Decimal`, не `float`.
- `Base.metadata.create_all()` створює відсутні таблиці, але не замінює schema
  migrations. Для зміни існуючої таблиці потрібен ідемпотентний migration script
  на кшталт `app/migrate_transaction_date.py`.

### Telegram bot

- `app/main.py` - aiogram entry point і long-polling process.
- Команди `/expense` та `/income` проходять через `parse_transaction()` і
  `save_transaction()`, тому bot і API записують дані за однаковими правилами.
- Bot використовує ту саму PostgreSQL/Neon database, що й Web API.
- Одночасно запускай лише один polling process для одного `BOT_TOKEN`, інакше
  Telegram повертає conflict.
- Кореневий `Dockerfile` за замовчуванням запускає bot. У Docker Compose команда
  API перевизначає цей default CMD.

## Frontend

- `frontend/` - React application, який збирається Vite.
- `frontend/src/App.jsx` керує Telegram ID, summary, transaction list,
  create/delete flows, filters і AI analysis. Telegram ID зберігається в
  `localStorage`.
- `frontend/src/AiChat.jsx` керує chat messages, thread ID та
  confirm/cancel для pending AI actions. Thread ID зберігається окремо для
  кожного Telegram ID.
- Frontend завжди звертається до відносних `/api/...` URL. Не хардкодь
  `localhost`, Render hostname або інший environment-specific origin у
  components.
- У development `frontend/vite.config.js` proxy-ить `/api` до Docker service
  `api:8000` або до `VITE_API_PROXY_TARGET`.
- Frontend validation покращує UX, але не є security boundary. Backend Pydantic
  і ownership checks залишаються обов'язковими.
- Після mutation frontend повторно завантажує server state. Не вважай локальний
  React state джерелом істини для фінансових даних.

## AI functionality

### Transaction analysis

- `app/ai_analysis.py` формує вхід із транзакцій одного користувача, викликає
  Gemini з timeout/retry/fallback та перевіряє відповідь через
  `TransactionAnalysisResponse`.
- `app/prompts.py` є джерелом system prompts. Не розкидай копії prompt text по
  endpoints або tests.
- Невалідна відповідь моделі не повинна проходити напряму до frontend.

### Chat and tools

- `app/ai_chat.py` будує LangGraph graph ліниво, щоб відсутній
  `GEMINI_API_KEY` не ламав import усього API.
- Chat model отримує read tools і tools, які лише готують фінансові зміни.
- `app/ai_tools.py` читає дані користувача та створює pending actions. Tool не
  повинен напряму створювати, оновлювати або видаляти транзакцію.
- `app/ai_actions.py` зберігає pending action зі статусом `pending`,
  `processing`, `confirmed` або `cancelled`.
- Фактичний write виконує confirm endpoint у `app/api.py` після повторної
  Pydantic validation та ownership check.
- AI-proposed writes мають бути human-in-the-loop: prepare -> show user ->
  confirm/cancel -> write.

Поточні обмеження, які не можна приховувати:

- LangGraph memory і pending actions зберігаються в пам'яті Python process.
- Вони губляться після restart/deploy і не синхронізуються між кількома workers.
- Перед horizontal scaling їх треба перенести у shared persistent storage.

## Component communication flows

### Dashboard read

1. Browser бере Telegram ID із input/localStorage.
2. React паралельно викликає `/api/summary` і `/api/transactions`.
3. FastAPI валідовує query parameter.
4. SQLAlchemy query join-ить `User`, щоб обмежити rows власником.
5. Pydantic response повертається React, який рендерить cards, chart і table.

### Transaction write

1. React або Telegram command формує дані транзакції.
2. Вхід нормалізується та валідовується за спільними domain rules.
3. `save_transaction()` в одній DB transaction знаходить/створює user і
   category та записує transaction.
4. Після commit API повертає typed response; frontend перечитує server state.

### AI analysis

1. API вибирає лише транзакції запитаного Telegram user.
2. Backend серіалізує мінімальні потрібні поля й передає їх Gemini.
3. Gemini output проходить schema validation.
4. API мапить unavailable/invalid model responses у контрольовані `502`/`503`,
   не повертаючи internal exception або secret.

### AI write with confirmation

1. Chat tool готує pending action у пам'яті, але не торкається БД.
2. API повертає action payload frontend.
3. Користувач натискає Confirm або Cancel.
4. Confirm endpoint перевіряє owner, status та payload ще раз.
5. Лише після цього виконується DB transaction і action стає confirmed.
6. При validation/DB failure reservation звільняється, щоб дію можна було
   повторити.

### Render request

1. `Dockerfile.render` у Node stage виконує `npm ci` та `npm run build`.
2. Python stage копіює backend і `frontend/dist` у `frontend_dist`.
3. Uvicorn запускає `app.render_api:app` на `0.0.0.0:$PORT`.
4. `app/render_api.py` спочатку імпортує всі FastAPI routes, а потім монтує
   static frontend на `/`. Порядок принциповий: `/api`, `/health`, `/docs` і
   `/openapi.json` повинні мати пріоритет над static files.
5. `render.yaml` задає Dockerfile, build context, plan, health check та лише
   назви secret environment variables.

## Rules for new API endpoints

Для нового endpoint дотримуйся такого порядку.

1. **Define the contract.** Визнач method, path, request, response, status codes
   та ownership rule до написання query.
2. **Use the correct module.** Бізнесовий route додавай у `app/api.py`, не в
   `app/render_api.py`. Останній відповідає лише за production static frontend.
3. **Use typed boundaries.** Body описуй Pydantic model у `app/schemas.py`.
   Оголошуй `response_model` і точний success status у decorator. Не повертай
   неструктурований ORM object.
4. **Validate at the boundary.** Для query/path використовуй FastAPI
   `Query`/`Path`; складні domain rules тримай у schema/shared domain helper.
   Не покладайся на frontend validation.
5. **Enforce ownership in SQL.** До read/update/delete query включай
   `telegram_id`/`user_id` restriction. Не завантажуй чужий row з подальшою
   перевіркою лише в Python. Для missing і foreign-owned resource повертай
   однаковий `404`, якщо disclosure не потрібен.
6. **Reuse domain logic.** Якщо операція потрібна і bot, і API, винеси її у
   спільний service/helper на кшталт `save_transaction()`.
7. **Keep writes atomic.** Використовуй `async with
   get_session_factory().begin()`. Не роби partial commit між залежними writes.
8. **Map errors deliberately.** Поточна семантика:
   - `201` - resource created;
   - `204` - delete succeeded, response body empty;
   - `404` - resource/data not found;
   - `409` - action already consumed or conflicting state;
   - `422` - invalid request/domain payload;
   - `502` - external AI повернув невалідний результат;
   - `503` - DB/external dependency тимчасово unavailable.
9. **Do not leak internals.** Логуй error type, safe IDs і SQLSTATE, але не SQL
   credentials, Gemini key, tokenized URL, raw secret або stack trace у response.
10. **Preserve AI confirmation.** Endpoint або tool, ініційований AI, не може
    обходити pending action та явне user confirmation для write operations.
11. **Update consumers.** Якщо contract змінюється, перевір React fetch call,
    Telegram bot/shared service, OpenAPI schema та README examples.
12. **Verify through public behavior.** Доповни відповідні scenarios у
    `tests/test_transactions_api.py` або `tests/test_ai_behavior.py`. Перевір
    happy path, invalid input, owner isolation, dependency failure і retry/
    idempotency там, де вони релевантні.

## Secrets and configuration

Дозволені місця для secret values:

- локальний кореневий `.env`, який ігнорується Git і Docker build context;
- secret environment variables у Render Dashboard;
- secret store майбутнього CI provider.

У `.env.example` зберігай лише очевидні fake placeholders. Ніколи не записуй
реальні значення `BOT_TOKEN`, `DATABASE_URL`, `GEMINI_API_KEY`, passwords, API
keys або bearer tokens у:

- Python/JavaScript source;
- tests, fixtures або snapshots;
- `Dockerfile*`, `docker-compose.yml` чи `render.yaml`;
- `README.md`, `AGENTS.md` або commands у Git;
- logs, exception messages, screenshots або comments.

Додаткові правила:

- AI не має права просити вставити secret у source file «тимчасово».
- Не читай і не друкуй весь `.env` у terminal output. Для діагностики перевіряй
  лише наявність змінної або масковану форму.
- `load_dotenv()` потрібен для local development; у Render значення приходять
  через environment.
- Якщо реальний secret потрапив у Git або logs, видалення рядка недостатньо:
  secret треба негайно revoke/rotate, а потім очистити витік відповідно до
  погодженого плану.

Перед завершенням зміни запусти з кореня:

```bash
.venv/bin/detect-secrets scan \
  --all-files \
  --exclude-files '(^|/)(\.git|\.venv|node_modules|dist)(/|$)|(^|/)\.env$' \
  --exclude-lines 'username[:]password[@]host'
```

Команда сканує tracked і нові untracked files, пропускає generated/vendor
directories та локальний `.env`. `--exclude-lines` дозволяє лише точний fake
credential substring з `.env.example`; не розширюй цей regex для придушення
справжніх findings. У JSON output поле `results` має бути `{}`. Не коміть output
scan.

## Commands

Усі команди нижче запускаються з кореня репозиторію, якщо не вказано інше.

### First-time setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
npm --prefix frontend ci
cp .env.example .env
source .venv/bin/activate
```

Після копіювання заповни secrets лише у локальному `.env`.

### Primary local development with Docker

```bash
docker compose up --build
```

Services:

- dashboard: `http://localhost:5173`;
- API docs: `http://localhost:8001/docs`;
- API health: `http://localhost:8001/health`;
- Telegram bot: background `bot` service.

Useful operations:

```bash
docker compose logs -f api
docker compose logs -f bot
docker compose logs -f frontend
docker compose down
```

### Run without Docker Compose

Запускай кожну команду в окремому terminal:

```bash
.venv/bin/python -m app.main
.venv/bin/uvicorn app.api:app --host 0.0.0.0 --port 8000 --reload
npm --prefix frontend run dev -- --host 0.0.0.0
```

Для direct frontend development задай `VITE_API_PROXY_TARGET`, якщо API не
доступний за default Docker hostname `http://api:8000`.

### Tests and static checks

```bash
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m unittest discover -s tests -v
npm --prefix frontend run lint
npm --prefix frontend run build
```

Повні integration tests потребують `TEST_DATABASE_URL`, який вказує лише на
disposable PostgreSQL database з назвою `finance_test`. Зберігай значення в
локальному `.env`/secret environment і не вставляй URL у command history. Без
цієї змінної DB integration tests будуть skipped; це треба явно зазначити у
звіті. Наразі проєкт не має окремо налаштованого Python formatter/linter, тому
не стверджуй, що така перевірка пройшла.

### Full preflight

```bash
python scripts/preflight.py
```

Команда передбачає активований virtualenv (`source .venv/bin/activate`). Перед
запуском мають бути встановлені Python dependencies з `requirements-dev.txt`,
frontend dependencies через `npm ci`, а Docker daemon має працювати. Перший
Docker build може потребувати network для завантаження base images і packages.

Preflight послідовно й у fail-fast режимі перевіряє:

1. Python compilation для `app/`;
2. React production build;
3. build кореневого `Dockerfile`;
4. build `Dockerfile.render`;
5. відсутність secrets через detect-secrets.

Кожен крок друкує назву, точну command і її stdout/stderr. Після першої помилки
наступні кроки не запускаються. Secret scan показує лише filename, line і тип
finding, але не secret value.

### Database checks and migrations

```bash
docker compose build
docker compose run --rm --no-deps bot python -m app.check_connection_to_db
docker compose run --rm --no-deps bot python -m app.check_gemini_api_key
docker compose run --rm --no-deps api python -m app.migrate_transaction_date
docker compose run --rm --no-deps bot python -m app.migrate_schema
```

Migration commands запускай лише після перевірки цільового environment і
backup/recovery plan. Migration має бути repeatable та не видаляти user data без
окремого явного дозволу.

### Render production image

```bash
docker build -f Dockerfile.render -t finance-saas-render .
docker run --rm --env-file .env -e PORT=10000 -p 10000:10000 finance-saas-render
curl --fail http://localhost:10000/
curl --fail http://localhost:10000/health
curl --fail http://localhost:10000/docs
```

Render settings for this repository:

- Docker Build Context Directory: `.`;
- Dockerfile Path: `./Dockerfile.render`;
- Health Check Path: `/health`;
- secret values: тільки Render Dashboard, не `render.yaml`.

Push у tracked branch запускає Auto-Deploy, якщо він увімкнений. Інакше у
Render використовуй **Manual Deploy -> Deploy latest commit**. Web Service
запускає dashboard та API; Telegram polling потребує окремого Background Worker.

## Preflight and CI status

`scripts/preflight.py` є canonical local entry point для cross-stack build і
security checks. Він не запускає destructive migrations, production DB writes,
реальні Gemini/Telegram calls або integration tests з PostgreSQL.

`.github/workflows/ci.yml` запускає job `Preflight` для кожного pull request у
`main`, після push у `main` і вручну через `workflow_dispatch`. GitHub-hosted
runner готує Python 3.12 і Node.js 22, встановлює backend та frontend
dependencies і викликає ту саму canonical command:

```bash
python scripts/preflight.py
```

Workflow використовує read-only `contents` permission. Не додавай secret values
у YAML: якщо майбутній check потребуватиме credentials, передавай їх через
GitHub Actions Secrets.

Сам workflow лише створює status check і не блокує merge. Після його першого
успішного запуску налаштуй для `main` GitHub ruleset або branch protection:

1. Відкрий `Settings -> Rules -> Rulesets` (або `Settings -> Branches`).
2. Створи правило для default branch `main`.
3. Увімкни `Require a pull request before merging`.
4. Увімкни `Require status checks to pass` і вибери `Preflight` (`CI / Preflight`
   у деяких екранах GitHub).
5. За потреби увімкни `Require branches to be up to date` та заборони bypass.

Поки це repository rule не ввімкнене на GitHub, CI показує результат, але не
гарантує блокування merge.

## Before saying done

1. Переглянь `git diff` і переконайся, що немає unrelated changes.
2. Для cross-stack змін запусти `python scripts/preflight.py`; для вузьких змін -
   релевантні backend tests та/або frontend lint/build.
3. Для deployment changes збери `Dockerfile.render` і перевір `/`, `/health`,
   `/docs` із запущеного container.
4. Запусти detect-secrets command із цього файлу.
5. Вкажи, які checks пройшли, які були skipped і чому.
6. Не commit і не push без явного дозволу користувача.
