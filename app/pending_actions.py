from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit_log import record_confirmed_action
from app.models import PendingAction, PendingActionStatus, PendingActionType, Transaction
from app.transaction_service import (
    TransactionAmountUpdate,
    TransactionCategoryUpdate,
    TransactionCreate,
    TransactionDelete,
    TransactionNotFoundError,
    create_admin_transaction,
    delete_admin_transaction,
    get_or_create_admin_user,
    transaction_to_json,
    update_admin_transaction_category,
    update_admin_transaction_sum,
)


CREATE_TRANSACTION_TOOL = "create_transaction"
UPDATE_TRANSACTION_CATEGORY_TOOL = "update_transaction_category"
UPDATE_TRANSACTION_SUM_TOOL = "update_transaction_sum"
DELETE_TRANSACTION_TOOL = "delete_transaction"
PENDING_ACTION_TOOL_RESULT_KEY = "__pending_action"

WRITE_ACTION_TOOL_NAMES = frozenset(
    {
        CREATE_TRANSACTION_TOOL,
        UPDATE_TRANSACTION_CATEGORY_TOOL,
        UPDATE_TRANSACTION_SUM_TOOL,
        DELETE_TRANSACTION_TOOL,
    }
)

PENDING_ACTION_TOOL_SCHEMAS = (
    {
        "name": CREATE_TRANSACTION_TOOL,
        "description": (
            "Готує чернетку створення фінансової операції для підтвердження "
            "користувача. Не створює транзакцію до confirm. Викликай, коли "
            "користувач явно вказав тип, суму й категорію. Після успішного "
            "виклику не проси текстове підтвердження: інтерфейс покаже картку."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["income", "expense"]},
                "amount": {
                    "type": "number",
                    "description": "Додатна сума з максимум двома знаками після коми.",
                },
                "category": {"type": "string", "description": "Текстова категорія."},
                "description": {"type": "string", "description": "Необов'язковий опис."},
                "date": {
                    "type": "string",
                    "description": "Необов'язкова дата YYYY-MM-DD, не в майбутньому.",
                },
            },
            "required": ["type", "amount", "category"],
        },
    },
    {
        "name": UPDATE_TRANSACTION_CATEGORY_TOOL,
        "description": (
            "Готує чернетку зміни категорії конкретної транзакції. "
            "Не змінює дані до confirm. Викликай відразу, коли відомі ID і "
            "нова категорія; інтерфейс покаже картку підтвердження."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "integer", "minimum": 1},
                "category": {"type": "string", "description": "Нова текстова категорія."},
            },
            "required": ["transaction_id", "category"],
        },
    },
    {
        "name": UPDATE_TRANSACTION_SUM_TOOL,
        "description": (
            "Готує чернетку зміни суми конкретної транзакції. "
            "Не змінює дані до confirm. Викликай відразу, коли відомі ID і "
            "нова сума; інтерфейс покаже картку підтвердження."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "integer", "minimum": 1},
                "amount": {
                    "type": "number",
                    "description": "Нова додатна сума з максимум двома знаками після коми.",
                },
            },
            "required": ["transaction_id", "amount"],
        },
    },
    {
        "name": DELETE_TRANSACTION_TOOL,
        "description": (
            "Готує чернетку видалення конкретної транзакції. "
            "Не видаляє дані до confirm. Викликай відразу, коли відомий ID; "
            "інтерфейс покаже картку підтвердження."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "integer", "minimum": 1},
            },
            "required": ["transaction_id"],
        },
    },
)


class PendingActionNotFoundError(Exception):
    pass


class PendingActionStatusError(Exception):
    pass


class PendingActionPayloadError(Exception):
    pass


class PendingActionTargetNotFoundError(Exception):
    pass


class UnsupportedPendingActionError(Exception):
    pass


class PendingActionResponse(BaseModel):
    action_id: int
    thread_id: str
    action_type: str
    payload: dict[str, Any]
    status: PendingActionStatus


@dataclass(frozen=True)
class ConfirmedAction:
    action: PendingAction
    result: dict[str, Any]


def validate_create_transaction_payload(payload: Mapping[str, Any]) -> TransactionCreate:
    return _validate_payload(TransactionCreate, payload)


def validate_pending_action_payload(
    action_type: str,
    payload: Mapping[str, Any],
) -> BaseModel:
    if action_type in {
        PendingActionType.create_transaction.value,
        PendingActionType.create_finance_transaction.value,
    }:
        return validate_create_transaction_payload(payload)
    if action_type == PendingActionType.update_transaction_category.value:
        return _validate_payload(TransactionCategoryUpdate, payload)
    if action_type == PendingActionType.update_transaction_sum.value:
        return _validate_payload(TransactionAmountUpdate, payload)
    if action_type == PendingActionType.delete_transaction.value:
        return _validate_payload(TransactionDelete, payload)
    raise UnsupportedPendingActionError("Unsupported pending action type.")


def _validate_payload(model: type[BaseModel], payload: Mapping[str, Any]) -> BaseModel:
    try:
        return model.model_validate(dict(payload))
    except ValidationError as error:
        raise PendingActionPayloadError("Invalid pending action payload.") from error


def pending_action_to_response(action: PendingAction) -> PendingActionResponse:
    return PendingActionResponse(
        action_id=action.action_id,
        thread_id=action.thread_id,
        action_type=action.action_type,
        payload=action.payload,
        status=action.status,
    )


class PendingActionService:
    """Stores AI proposals and executes only user-confirmed allowed actions."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create_proposal(
        self,
        *,
        thread_id: str,
        action_type: str,
        payload: Mapping[str, Any],
    ) -> PendingAction:
        if action_type not in WRITE_ACTION_TOOL_NAMES:
            raise UnsupportedPendingActionError("Unsupported pending action type.")

        action_payload = validate_pending_action_payload(action_type, payload)
        async with self._sessionmaker() as session:
            async with session.begin():
                user = await get_or_create_admin_user(session)
                await self._ensure_target_transaction_exists(
                    session,
                    user_id=user.id,
                    action_type=action_type,
                    payload=action_payload,
                )
                action = PendingAction(
                    user_id=user.id,
                    thread_id=thread_id,
                    action_type=action_type,
                    payload=action_payload.model_dump(mode="json"),
                    status=PendingActionStatus.pending,
                )
                session.add(action)
                await session.flush()

            await session.refresh(action)
        return action

    @staticmethod
    async def _ensure_target_transaction_exists(
        session: AsyncSession,
        *,
        user_id: int,
        action_type: str,
        payload: BaseModel,
    ) -> None:
        if action_type == CREATE_TRANSACTION_TOOL:
            return

        transaction_id = getattr(payload, "transaction_id", None)
        if not isinstance(transaction_id, int):
            raise PendingActionPayloadError("Transaction ID is invalid.")

        target_id = await session.scalar(
            select(Transaction.id).where(
                Transaction.id == transaction_id,
                Transaction.user_id == user_id,
            )
        )
        if target_id is None:
            raise PendingActionTargetNotFoundError("Transaction was not found.")

    async def create_finance_transaction_proposal(
        self,
        *,
        thread_id: str,
        payload: Mapping[str, Any],
    ) -> PendingAction:
        """Compatibility wrapper for callers created before generic write tools."""

        return await self.create_proposal(
            thread_id=thread_id,
            action_type=CREATE_TRANSACTION_TOOL,
            payload=payload,
        )

    async def confirm(
        self,
        *,
        action_id: int,
        thread_id: str,
    ) -> ConfirmedAction:
        async with self._sessionmaker() as session:
            async with session.begin():
                user = await get_or_create_admin_user(session)
                action = await self._get_pending_action_for_update(
                    session,
                    action_id=action_id,
                    thread_id=thread_id,
                    user_id=user.id,
                )

                try:
                    result = await self._execute_confirmed_action(session, action)
                except TransactionNotFoundError as error:
                    raise PendingActionTargetNotFoundError(
                        "Transaction for pending action was not found."
                    ) from error

                action.status = PendingActionStatus.confirmed
                await record_confirmed_action(session, action)

            return ConfirmedAction(action=action, result=result)

    async def cancel(
        self,
        *,
        action_id: int,
        thread_id: str,
    ) -> PendingAction:
        async with self._sessionmaker() as session:
            async with session.begin():
                user = await get_or_create_admin_user(session)
                action = await self._get_pending_action_for_update(
                    session,
                    action_id=action_id,
                    thread_id=thread_id,
                    user_id=user.id,
                )
                action.status = PendingActionStatus.canceled

            await session.refresh(action)
        return action

    async def _execute_confirmed_action(
        self,
        session: AsyncSession,
        action: PendingAction,
    ) -> dict[str, Any]:
        action_type = action.action_type

        if action_type in {
            PendingActionType.create_transaction.value,
            PendingActionType.create_finance_transaction.value,
        }:
            payload = validate_create_transaction_payload(action.payload)
            transaction, category, user = await create_admin_transaction(session, payload)
            return {"transaction": transaction_to_json(transaction, category, user)}

        if action_type == PendingActionType.update_transaction_category.value:
            payload = _validate_payload(TransactionCategoryUpdate, action.payload)
            transaction, category, user = await update_admin_transaction_category(session, payload)
            return {"transaction": transaction_to_json(transaction, category, user)}

        if action_type == PendingActionType.update_transaction_sum.value:
            payload = _validate_payload(TransactionAmountUpdate, action.payload)
            transaction, category, user = await update_admin_transaction_sum(session, payload)
            return {"transaction": transaction_to_json(transaction, category, user)}

        if action_type == PendingActionType.delete_transaction.value:
            payload = _validate_payload(TransactionDelete, action.payload)
            transaction_id = await delete_admin_transaction(session, payload)
            return {"deleted_transaction_id": transaction_id}

        raise UnsupportedPendingActionError("Unsupported pending action type.")

    async def _get_pending_action_for_update(
        self,
        session: AsyncSession,
        *,
        action_id: int,
        thread_id: str,
        user_id: int,
    ) -> PendingAction:
        action = await session.scalar(
            select(PendingAction)
            .where(
                PendingAction.action_id == action_id,
                PendingAction.thread_id == thread_id,
                PendingAction.user_id == user_id,
            )
            .with_for_update()
        )
        if action is None:
            raise PendingActionNotFoundError("Pending action not found.")
        if action.status != PendingActionStatus.pending:
            raise PendingActionStatusError("Pending action is no longer pending.")
        return action
