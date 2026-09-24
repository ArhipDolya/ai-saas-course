import json
from decimal import Decimal

from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy import case, desc, func, select

from app.database import get_session_factory
from app.models import Category, Transaction, TransactionType, User
from app.ai_actions import create_pending_action


@tool
async def prepare_create_transaction(amount: float, category: str, transaction_type: str, date: str, description: str, config: RunnableConfig) -> str:
    """Створює запит (pending action) на додавання нової транзакції.
    
    Args:
        amount: Сума (більше 0).
        category: Назва категорії.
        transaction_type: 'expense' (витрата) або 'income' (дохід).
        date: Дата у форматі 'YYYY-MM-DD'.
        description: Опис (може бути порожнім).
    """
    telegram_id = config.get("configurable", {}).get("telegram_id")
    action_id = create_pending_action(telegram_id, "create_transaction", {
        "amount": amount,
        "category": category,
        "type": transaction_type,
        "date": date,
        "description": description or ""
    })
    return json.dumps({"status": "pending_confirmation", "action_id": action_id, "message": "Розкажи користувачу, що ти підготував цю транзакцію, і попроси підтвердити."})


@tool
async def prepare_update_transaction(transaction_id: int, amount: float, category: str, transaction_type: str, date: str, description: str, config: RunnableConfig) -> str:
    """Створює запит на оновлення існуючої транзакції.
    
    Args:
        transaction_id: ID транзакції, яку треба оновити.
        amount: Нова сума (більше 0).
        category: Нова назва категорії.
        transaction_type: 'expense' (витрата) або 'income' (дохід).
        date: Нова дата у форматі 'YYYY-MM-DD'.
        description: Новий опис.
    """
    telegram_id = config.get("configurable", {}).get("telegram_id")
    action_id = create_pending_action(telegram_id, "update_transaction", {
        "transaction_id": transaction_id,
        "amount": amount,
        "category": category,
        "type": transaction_type,
        "date": date,
        "description": description or ""
    })
    return json.dumps({"status": "pending_confirmation", "action_id": action_id, "message": "Розкажи користувачу, що ти підготував оновлення транзакції, і попроси підтвердити."})


@tool
async def prepare_delete_transaction(transaction_id: int, config: RunnableConfig) -> str:
    """Створює запит на видалення транзакції за її ID. Очікує підтвердження юзера."""
    telegram_id = config.get("configurable", {}).get("telegram_id")
    action_id = create_pending_action(telegram_id, "delete_transaction", {"transaction_id": transaction_id})
    return json.dumps({"status": "pending_confirmation", "action_id": action_id, "message": "Попроси користувача підтвердити видалення транзакції."})



@tool
async def get_transactions_summary(period: str, config: RunnableConfig) -> str:
    """Отримує загальну суму доходів та витрат користувача за вказаний період.

    Args:
        period: Рядок у форматі 'YYYY-MM' (наприклад, '2026-09') або 'all' для всього часу.
    """
    telegram_id = config.get("configurable", {}).get("telegram_id")
    if not telegram_id:
        return json.dumps({"error": "telegram_id не знайдено в контексті"})

    total_income = func.coalesce(
        func.sum(
            case(
                (Transaction.transaction_type == TransactionType.INCOME, Transaction.amount),
                else_=0,
            )
        ),
        0,
    ).label("total_income")
    total_expense = func.coalesce(
        func.sum(
            case(
                (Transaction.transaction_type == TransactionType.EXPENSE, Transaction.amount),
                else_=0,
            )
        ),
        0,
    ).label("total_expense")

    statement = (
        select(total_income, total_expense)
        .join(User, User.id == Transaction.user_id)
        .where(User.telegram_id == telegram_id)
    )

    if period != "all":
        # period має бути формату YYYY-MM
        statement = statement.where(func.to_char(Transaction.transaction_date, "YYYY-MM") == period)

    async with get_session_factory()() as session:
        result = (await session.execute(statement)).one_or_none()

    if not result:
        return json.dumps({"total_income": "0.00", "total_expense": "0.00", "balance": "0.00"})

    income = Decimal(result.total_income or 0).quantize(Decimal("0.01"))
    expense = Decimal(result.total_expense or 0).quantize(Decimal("0.01"))

    return json.dumps({
        "total_income": str(income),
        "total_expense": str(expense),
        "balance": str(income - expense),
        "period": period,
    }, ensure_ascii=False)


@tool
async def get_category_totals(period: str, config: RunnableConfig) -> str:
    """Отримує сумарні доходи та витрати згруповані по категоріях.

    Args:
        period: Рядок у форматі 'YYYY-MM' (наприклад, '2026-09') або 'all' для всього часу.
    """
    telegram_id = config.get("configurable", {}).get("telegram_id")
    if not telegram_id:
        return json.dumps({"error": "telegram_id не знайдено в контексті"})

    total_amount = func.sum(Transaction.amount).label("total")

    statement = (
        select(
            Transaction.transaction_type.label("type"),
            Category.name.label("category"),
            total_amount,
        )
        .join(Category, Category.id == Transaction.category_id)
        .join(User, User.id == Transaction.user_id)
        .where(User.telegram_id == telegram_id)
        .group_by(Transaction.transaction_type, Category.name)
        .order_by(desc("total"))
    )

    if period != "all":
        statement = statement.where(func.to_char(Transaction.transaction_date, "YYYY-MM") == period)

    async with get_session_factory()() as session:
        rows = (await session.execute(statement)).mappings().all()

    if not rows:
        return json.dumps({"message": "Даних за вказаний період немає"}, ensure_ascii=False)

    results = []
    for row in rows:
        results.append({
            "type": row["type"],
            "category": row["category"],
            "total": str(Decimal(row["total"]).quantize(Decimal("0.01"))),
        })

    return json.dumps(results, ensure_ascii=False)


@tool
async def get_top_expenses(period: str, limit: int, config: RunnableConfig) -> str:
    """Отримує список найбільших поодиноких витрат користувача.

    Args:
        period: Рядок у форматі 'YYYY-MM' (наприклад, '2026-09') або 'all'.
        limit: Максимальна кількість транзакцій для повернення (наприклад, 5).
    """
    telegram_id = config.get("configurable", {}).get("telegram_id")
    if not telegram_id:
        return json.dumps({"error": "telegram_id не знайдено в контексті"})

    statement = (
        select(
            Transaction.amount,
            Category.name.label("category"),
            Transaction.description,
            Transaction.transaction_date.label("date"),
        )
        .join(Category, Category.id == Transaction.category_id)
        .join(User, User.id == Transaction.user_id)
        .where(
            User.telegram_id == telegram_id,
            Transaction.transaction_type == TransactionType.EXPENSE,
        )
        .order_by(desc(Transaction.amount))
        .limit(limit)
    )

    if period != "all":
        statement = statement.where(func.to_char(Transaction.transaction_date, "YYYY-MM") == period)

    async with get_session_factory()() as session:
        rows = (await session.execute(statement)).mappings().all()

    if not rows:
        return json.dumps({"message": "Витрат за вказаний період немає"}, ensure_ascii=False)

    results = []
    for row in rows:
        results.append({
            "amount": str(Decimal(row["amount"]).quantize(Decimal("0.01"))),
            "category": row["category"],
            "description": row["description"],
            "date": row["date"].isoformat(),
        })

    return json.dumps(results, ensure_ascii=False)

