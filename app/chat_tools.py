from __future__ import annotations

from typing import Any

from app.ai_chat_graph import ChatToolContext
from app.finance_tools import FinanceTools
from app.pending_actions import (
    PENDING_ACTION_TOOL_RESULT_KEY,
    PendingActionPayloadError,
    PendingActionService,
    PendingActionTargetNotFoundError,
    UnsupportedPendingActionError,
    WRITE_ACTION_TOOL_NAMES,
    pending_action_to_response,
)


class FinanceChatToolExecutor:
    """Routes AI tools without exposing a database session to the model."""

    def __init__(self, read_tools: FinanceTools, pending_actions: PendingActionService) -> None:
        self._read_tools = read_tools
        self._pending_actions = pending_actions

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: ChatToolContext,
    ) -> dict[str, Any]:
        if name not in WRITE_ACTION_TOOL_NAMES:
            return await self._read_tools.execute(name, arguments, context=context)

        try:
            action = await self._pending_actions.create_proposal(
                thread_id=context.thread_id,
                action_type=name,
                payload=arguments,
            )
        except PendingActionTargetNotFoundError:
            return {"error": "Операцію для цієї дії не знайдено."}
        except (PendingActionPayloadError, UnsupportedPendingActionError):
            return {
                "error": (
                    "Не вдалося підготувати дію: перевірте ідентифікатор операції, "
                    "суму, категорію та дату."
                )
            }

        return {
            "status": "pending_confirmation",
            "message": (
                "Чернетку дії підготовлено. Інтерфейс покаже картку з кнопками "
                "підтвердження або відхилення; не проси текстове підтвердження."
            ),
            PENDING_ACTION_TOOL_RESULT_KEY: pending_action_to_response(action).model_dump(
                mode="json"
            ),
        }
