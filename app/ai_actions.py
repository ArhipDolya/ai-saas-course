import uuid
from typing import Any

# In-memory сховище для pending дій
# Структура: {action_id: {"telegram_id": int, "type": str, "payload": dict, "status": str}}
PENDING_ACTIONS: dict[str, dict[str, Any]] = {}

def create_pending_action(telegram_id: int, action_type: str, payload: dict) -> str:
    """Створює pending action і повертає його action_id."""
    action_id = str(uuid.uuid4())
    PENDING_ACTIONS[action_id] = {
        "telegram_id": telegram_id,
        "type": action_type,
        "payload": payload,
        "status": "pending",
    }
    return action_id

def get_pending_action(action_id: str) -> dict[str, Any] | None:
    return PENDING_ACTIONS.get(action_id)

def cancel_pending_action(action_id: str) -> None:
    if action_id in PENDING_ACTIONS:
        PENDING_ACTIONS[action_id]["status"] = "cancelled"

def confirm_pending_action(action_id: str) -> None:
    if action_id in PENDING_ACTIONS:
        PENDING_ACTIONS[action_id]["status"] = "completed"
