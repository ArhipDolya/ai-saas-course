# Finance SaaS Project Guide

## Purpose and Scope

This repository contains a personal finance product with three entry points:

- a Telegram bot for adding and viewing expenses;
- a FastAPI backend that owns database access, the HTTP API, LLM calls, and AI actions;
- a React admin dashboard for viewing and managing finance data.

Before changing code, inspect the relevant producer, transport, and consumer.
For example, a change to an API response requires checking the Pydantic response
model, backend handler, React fetch call, React state normalization, and the UI
that renders the value. Keep changes narrow and do not rewrite unrelated parts
of the dashboard, Telegram bot, or AI analysis feature.

## Architecture

```text
Telegram user
  -> app/main.py (aiogram polling)
  -> Neon PostgreSQL through SQLAlchemy async + asyncpg

Browser
  -> React/Vite admin dashboard (frontend/)
  -> /api/*
  -> FastAPI (app/api.py)
  -> Neon PostgreSQL through SQLAlchemy async + asyncpg

FastAPI AI chat
  -> app/ai_chat_graph.py (LangGraph + InMemorySaver)
  -> Gemini through google-genai and LLM_API_KEY
  -> approved read-only tools or pending-action proposals
```

### Backend

- `app/api.py` is the FastAPI application and public HTTP API. Its lifespan
  loads environment variables, creates the async database engine, checks Neon,
  ensures database tables exist, and puts shared services in `app.state`.
- `app/database.py` owns the SQLAlchemy async engine/sessionmaker setup and
  normalizes Neon URLs for `asyncpg`. Do not create a second engine or
  sessionmaker in feature modules.
- `app/models.py` contains ORM models and database enums: `User`, `Category`,
  `Transaction`, `PendingAction`, and `AuditLog`.
- `app/transaction_service.py` owns validated create/update/delete operations
  for the dashboard and confirmed AI actions.
- `app/finance_tools.py` contains read-only financial queries used by AI.
- `app/chat_tools.py` routes a Gemini tool call to either a read-only finance
  tool or a pending-action proposal.
- `app/pending_actions.py` validates, stores, confirms, or cancels AI write
  proposals. It is the only path from an AI write tool to a database mutation.
- `app/audit_log.py` records each confirmed AI action in the same database
  transaction as the mutation.
- `app/ai_chat_graph.py` owns the LangGraph state, Gemini adapter, tool loop,
  and chat-level validation.
- `app/render_app.py` imports the FastAPI application and mounts the built React
  files for a single-domain Render deployment.

### Telegram Bot

- `app/main.py` runs aiogram polling.
- `/expense <amount> <category>` creates an expense directly in Neon for the
  Telegram user.
- `/show-expenses` and `/show_expenses` list that Telegram user's expenses.
- The bot and FastAPI share database models and database helpers, but the bot
  does not call the HTTP API.
- Run one polling instance only. Running local polling and a Docker/Render
  polling instance together causes Telegram `getUpdates` conflicts.

### Frontend

- `frontend/` is a React 18 + Vite application.
- `frontend/src/pages/AdminDashboard.jsx` owns dashboard data loading, the
  transaction form, filters, AI analysis, AI chat state, and pending-action
  confirmation/cancellation.
- `frontend/src/components/PendingActionCard.jsx` renders an AI write proposal.
- In local development, Vite proxies `/api` to the FastAPI container. The React
  code must use relative `/api/...` URLs, never a hard-coded host or LLM URL.
- The frontend never accesses Neon, Gemini, `DATABASE_URL`, `LLM_API_KEY`, or
  any other server secret directly.

## Data Model

- `users`: Telegram identity and profile data.
- `categories`: a category belongs to one user and has type `income` or
  `expense`; `(user_id, name, type)` is unique.
- `transactions`: belongs to a user and category; `amount` is positive,
  `description` is optional, and `created_at` is timezone-aware.
- `pending_actions`: a validated proposal from the AI. Its status begins as
  `pending` and changes to `confirmed` or `canceled` only through the action
  API. Older enum values remain only for compatibility with legacy rows.
- `audit_logs`: immutable record created only after a pending action is
  confirmed. It stores action ID, thread ID, action type, payload, and time.

Do not bypass the model relationships, database constraints, or service layer
with ad hoc SQL. Prefer the current SQLAlchemy sessionmaker and service helpers.

## Local Development

Create `.env` from `.env.example` and fill in real values locally. Never commit
`.env`.

Required environment variable names:

```env
BOT_TOKEN=
DATABASE_URL=
ADMIN_PASSWORD=
LLM_API_KEY=
LLM_MODEL=gemini-3.1-flash-lite
```

Run all local services with hot reload:

```bash
docker compose up --build
```

Useful local addresses:

- React admin dashboard: `http://localhost:5173`
- FastAPI: `http://localhost:8001`
- readiness check: `http://localhost:8001/healthz`

To start only the HTTP services:

```bash
docker compose up --build api frontend
```

To run without Docker after activating `.venv`:

```bash
.venv/bin/python -m app.main
.venv/bin/uvicorn app.api:app --reload
.venv/bin/python -m app.check_database
.venv/bin/python -m app.check_llm
```

Do not start the local bot and Docker bot at the same time.

## Preflight

Before a handoff, commit, or deployment, run the project checks from the
repository root:

```bash
.venv/bin/python scripts/preflight.py
```

The script stops at the first failed step and checks Python compilation, the
React production build, local and Render Docker image builds, and potential
hard-coded secrets through `detect-secrets`. It builds images but does not
start containers, call Neon, or call Gemini.

## Continuous Integration

`.github/workflows/preflight.yml` runs the same preflight on every pull request
whose target branch is `main`, and it can also be started manually from the
GitHub Actions page. It uses a clean Ubuntu runner with Python 3.12, Node 22,
and Docker, then invokes `.venv/bin/python scripts/preflight.py`.

The CI workflow must not receive `BOT_TOKEN`, `DATABASE_URL`, `LLM_API_KEY`,
`ADMIN_PASSWORD`, or any other runtime secret. The checks must remain build and
source-safety checks only. Before opening a pull request, run the same command
locally so failures are found before GitHub Actions starts.

## Render Deployment

`Dockerfile.render` is the production image. It builds `frontend/` first,
copies the generated `frontend_dist` into the Python image, and starts:

```text
uvicorn app.render_app:app --host 0.0.0.0 --port $PORT
```

Create a Render Web Service with:

```text
Runtime: Docker
Dockerfile Path: Dockerfile.render
Docker Build Context Directory: .
Health Check Path: /healthz
Docker Command: leave empty
```

Set `DATABASE_URL`, `ADMIN_PASSWORD`, `LLM_API_KEY`, and optionally `LLM_MODEL`
in the Render environment settings. `BOT_TOKEN` is not needed by the Web Service
unless it also runs the Telegram bot.

The Web Service serves both the React dashboard at `/` and API routes at
`/api/...`; therefore production frontend requests remain relative URLs.
`GET /healthz` performs `SELECT 1` against Neon and must return `2xx` for a
healthy deployment.

Telegram polling must run separately as a Render Background Worker using the
same `Dockerfile.render` and command:

```bash
python -m app.main
```

Set `BOT_TOKEN` and `DATABASE_URL` for that worker. A Render Background Worker
is not included in the free plan. Do not put bot polling in the Web Service.

## Secrets and Security

### Required Rules

- Secrets belong only in local `.env` or the Render environment settings.
- `.env.example` contains variable names and non-secret placeholders only.
- Never hard-code, print, serialize, return, or include in a prompt any real
  `BOT_TOKEN`, `DATABASE_URL`, `LLM_API_KEY`, `ADMIN_PASSWORD`, private key,
  bearer token, or full `.env` content.
- Do not log request headers containing credentials, environment objects, full
  Gemini prompts, or raw financial tool results.
- Do not expose internal exception text to the browser. Return a short safe
  error message and log only the minimum diagnostic context server-side.
- Read environment variables at application boundaries. `python-dotenv` loads
  `.env` locally; Docker Compose and Render inject environment variables at
  runtime.
- Do not add secrets to source files, test fixtures, README examples, Docker
  files, frontend variables, or Git history.

### detect-secrets

Use `detect-secrets` before a commit, handoff, or deployment. The local
environment currently provides it at `.venv/bin/detect-secrets`. It is a
development safety tool, not a runtime dependency of the application.

Run a scan while excluding the local secret file:

```bash
.venv/bin/detect-secrets scan --all-files --exclude-files '(^|/)\.env$' .
```

Review every finding. Do not add broad allowlists to silence a result. If an
example value is confirmed safe and must remain, use a narrow, documented,
line-level allowlist only. A real secret found in Git requires immediate
rotation, removal from the code, and history remediation when applicable.

Before changing a public API, check `.gitignore`, `.env.example`, and a staged
diff for accidental secret exposure. When changing security behavior, preserve
the current error contract and avoid leaking why authentication failed.

### Write Authentication

`POST /api/transactions` and AI action confirmation/cancellation require
`X-Admin-Auth`, compared server-side to `ADMIN_PASSWORD`. The frontend obtains
this only after `/api/admin/verify-password` succeeds.

All future mutating endpoints must require appropriate authorization. Audit any
existing mutation endpoint before using it as a template; do not assume an old
endpoint has sufficient protection merely because it already exists.

## HTTP API Contracts

Keep the request and response contracts below synchronized with both
`app/api.py`/Pydantic models and `frontend/src/pages/AdminDashboard.jsx`. If a
contract changes, update this document in the same change set.

| Endpoint | Request | Response / behavior |
| --- | --- | --- |
| `GET /healthz` | none | `{ "status": "ok" }` after a Neon `SELECT 1`; `503` when unavailable. |
| `GET /api/transactions/` | none | List of transaction JSON objects, newest first. |
| `GET /api/summary/` | none | `{ "total_income": "...", "total_expense": "...", "balance": "..." }`. |
| `POST /api/admin/verify-password` | `{ "password": "..." }` | `{ "authenticated": true }`; `401` for invalid password. |
| `POST /api/transactions` | `X-Admin-Auth` plus type, amount, category, optional description/date | `201` and one transaction object. |
| `DELETE /api/transactions/{id}` | positive integer ID | `{ "deleted": true, "id": 123 }` or `404`. |
| `POST /api/ai/analyze-transactions` | none | JSON with `summary`, `top_expense_categories`, `risks`, and `advice`. Keep this feature independent from the chat. |
| `POST /api/ai/chat` | `{ "message": "...", "thread_id": "UUID or null" }` | `{ "answer": "...", "thread_id": "UUID", "pending_action": "object or null" }`. |
| `POST /api/ai/actions/{action_id}/confirm` | positive ID, `X-Admin-Auth`, `{ "thread_id": "UUID" }` | `{ "action_id": 123, "status": "confirmed", "result": { ... } }`. |
| `POST /api/ai/actions/{action_id}/cancel` | positive ID, `X-Admin-Auth`, `{ "thread_id": "UUID" }` | `{ "action_id": 123, "status": "canceled" }`. |

For create/update forms:

- `type` is exactly `income` or `expense`.
- `amount` is a positive decimal with the bounds enforced by the existing
  Pydantic model and database constraint.
- `category` is non-empty text, not a number-only value.
- `description` is optional.
- an explicit date may not be in the future; omission uses creation time.
- route IDs and `thread_id` are validated on the backend. Do not rely on only
  frontend validation.

The API accepts some routes with and without a trailing slash for compatibility.
Do not remove an alias casually: the frontend, old clients, or Render paths may
still use it.

## AI Chat, Tools, and Pending Actions

### Chat Memory and LLM

- `POST /api/ai/chat` delegates to `FinanceChatGraph`.
- A missing `thread_id` becomes a new UUID; an existing valid UUID keeps the
  same conversation.
- LangGraph `InMemorySaver` stores short-term state keyed by `thread_id`.
  Messages from different IDs must never be mixed.
- In-memory history disappears when the API process restarts or a free Render
  service sleeps. It can later be replaced by a persistent checkpointer without
  changing the frontend contract.
- Gemini is accessed only by `GeminiChatResponder` with `LLM_API_KEY` and
  `LLM_MODEL`. Do not create a second provider client or place the key in React.
- The current system instruction requires Ukrainian, concise responses, factual
  answers based on tools, and no claims that an unexecuted action succeeded.

### Read-only Tools

AI may query only these controlled tools:

- `get_transactions_summary(period)`;
- `get_category_totals(period)`;
- `get_top_expenses(period, limit)`;
- `get_recent_transactions(period, limit)`;
- `find_transactions(period, limit, optional type/amount/category/description/date/time)`.

Supported `period` values are `current_month`, `previous_month`,
`last_30_days`, and `YYYY-MM`. `limit` must be an integer from `1` to `20`.
Never execute model-generated SQL or allow a model direct access to a database
session or connection string.

### Write Tools and Action Lifecycle

The model may prepare, but never immediately execute, these write proposals:

- `create_transaction`;
- `update_transaction_category`;
- `update_transaction_sum`;
- `delete_transaction`.

The lifecycle is deliberately two-phase:

```text
User message
  -> LangGraph/Gemini chooses a write tool
  -> PendingActionService validates payload and stores status=pending
  -> API returns pending_action to React
  -> React shows PendingActionCard
  -> user confirms or cancels
  -> confirm: validate ownership/thread/status/payload, mutate DB, audit log
  -> cancel: set status=canceled; data remains unchanged
```

Rules for this boundary:

- A write tool creates only a `PendingAction`; it must not mutate a transaction.
- `confirm` requires the matching `action_id`, `thread_id`, current admin
  authorization, and `pending` status. It locks the proposal while executing.
- The payload is revalidated during confirmation. Updates/deletes also verify
  that the target transaction belongs to the controlled admin user.
- Confirmation writes the financial change and the immutable audit record in
  one database transaction.
- `cancel` requires the same action/thread ownership and changes only the
  action status.
- Frontend cards disable both decisions while one request is in flight. After a
  successful confirm, reload transaction and summary data; after cancel, keep
  finance data unchanged and show `Відхилено`.
- Never add a generic `run_sql` tool or a tool that mutates data without this
  pending/confirm boundary.

When adding a new AI action type, update all of these together:

1. `PendingActionType` and validation in `app/pending_actions.py`.
2. The Gemini tool schema and `WRITE_ACTION_TOOL_NAMES`.
3. Confirmation execution in `PendingActionService`.
4. The pending-action response/UI rendering in React.
5. Audit-log behavior and this API/action documentation.

## Change Discipline

- Preserve the existing AI analysis button and endpoint when changing AI chat.
- Keep frontend API calls on relative `/api/...` paths.
- Prefer existing services, models, and session helpers over duplicate logic.
- Make database changes consciously. `create_database_tables()` is not a
  substitute for a migration strategy in a production schema change.
- Do not silently rename public JSON fields, enum values, route paths, or
  action types.
- Check `git diff` and `git status` before committing. Confirm that `.env` and
  secrets are absent from the diff.
- Run the relevant smoke check or build after an implementation change and
  report clearly what was and was not verified.
