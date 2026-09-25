"""In-memory storage for pending AI actions (Human-in-the-Loop).

Each pending action holds the data needed to perform a write operation
(create / update / delete transaction). The user must explicitly confirm
or cancel the action via the API.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo


class ActionType(str, Enum):
    CREATE_TRANSACTION = "create_transaction"
    UPDATE_TRANSACTION = "update_transaction"
    DELETE_TRANSACTION = "delete_transaction"


class ActionStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


@dataclass
class PendingAction:
    action_id: str
    action_type: ActionType
    telegram_id: int
    payload: dict[str, Any]
    status: ActionStatus = ActionStatus.PENDING
    created_at: datetime = field(
        default_factory=lambda: datetime.now(ZoneInfo("Europe/Kyiv"))
    )


# Simple in-memory store. Acceptable for a course project; production would
# use Redis or a DB table.
_pending_actions: dict[str, PendingAction] = {}


def create_pending_action(
    action_type: ActionType,
    telegram_id: int,
    payload: dict[str, Any],
) -> PendingAction:
    """Create and store a new pending action. Returns it."""
    action_id = uuid.uuid4().hex[:12]
    action = PendingAction(
        action_id=action_id,
        action_type=action_type,
        telegram_id=telegram_id,
        payload=payload,
    )
    _pending_actions[action_id] = action
    return action


def get_pending_action(action_id: str) -> PendingAction | None:
    return _pending_actions.get(action_id)


def confirm_pending_action(action_id: str) -> PendingAction | None:
    """Mark the action as confirmed and return it, or None if not found / not pending."""
    action = _pending_actions.get(action_id)
    if action is None or action.status != ActionStatus.PENDING:
        return None
    action.status = ActionStatus.CONFIRMED
    return action


def cancel_pending_action(action_id: str) -> PendingAction | None:
    """Mark the action as cancelled and return it, or None if not found / not pending."""
    action = _pending_actions.get(action_id)
    if action is None or action.status != ActionStatus.PENDING:
        return None
    action.status = ActionStatus.CANCELLED
    return action
