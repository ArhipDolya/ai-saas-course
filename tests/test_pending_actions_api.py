from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from uuid import UUID

from fastapi import HTTPException

from app.api import (
    PendingActionDecisionPayload,
    app,
    cancel_ai_action,
    confirm_ai_action,
)
from app.models import PendingAction, PendingActionStatus
from app.pending_actions import (
    ConfirmedAction,
    PendingActionNotFoundError,
    PendingActionPayloadError,
    PendingActionStatusError,
)


THREAD_ID = UUID("00000000-0000-4000-8000-000000000001")


class FakePendingActionService:
    def __init__(self, action: PendingAction) -> None:
        self.action = action
        self.confirm_calls: list[tuple[int, str]] = []
        self.cancel_calls: list[tuple[int, str]] = []

    async def confirm(self, *, action_id: int, thread_id: str) -> ConfirmedAction:
        self.confirm_calls.append((action_id, thread_id))
        if action_id != self.action.action_id:
            raise PendingActionNotFoundError

        self.action.status = PendingActionStatus.confirmed.value
        return ConfirmedAction(
            action=self.action,
            result={
                "transaction": {
                    "id": 3,
                    "category_name": "Кава",
                    "amount": "120.00",
                }
            },
        )

    async def cancel(self, *, action_id: int, thread_id: str) -> PendingAction:
        self.cancel_calls.append((action_id, thread_id))
        if action_id != self.action.action_id:
            raise PendingActionNotFoundError

        self.action.status = PendingActionStatus.canceled.value
        return self.action


class FailingPendingActionService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def confirm(self, *, action_id: int, thread_id: str) -> ConfirmedAction:
        raise self.error

    async def cancel(self, *, action_id: int, thread_id: str) -> PendingAction:
        raise self.error


class PendingActionApiTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.action = PendingAction(
            action_id=42,
            user_id=1,
            thread_id=str(THREAD_ID),
            action_type="create_finance_transaction",
            payload={
                "type": "expense",
                "amount": "120.00",
                "category": "Кава",
            },
            status=PendingActionStatus.pending.value,
        )
        app.state.admin_password = "test-password"  # pragma: allowlist secret
        app.state.pending_actions = FakePendingActionService(self.action)
        self.request = type("Request", (), {"app": app})()
        self.payload = PendingActionDecisionPayload(thread_id=THREAD_ID)

    async def test_confirm_returns_action_status_and_transaction_result(self) -> None:
        response = await confirm_ai_action(
            payload=self.payload,
            request=self.request,
            action_id=42,
            admin_password="test-password",  # pragma: allowlist secret
        )

        self.assertEqual(response.action_id, 42)
        self.assertEqual(response.status, PendingActionStatus.confirmed)
        self.assertEqual(response.result["transaction"]["id"], 3)
        self.assertEqual(response.result["transaction"]["category_name"], "Кава")

    async def test_cancel_returns_only_action_id_and_status(self) -> None:
        response = await cancel_ai_action(
            payload=self.payload,
            request=self.request,
            action_id=42,
            admin_password="test-password",  # pragma: allowlist secret
        )

        self.assertEqual(response.model_dump(), {"action_id": 42, "status": "canceled"})

    async def test_missing_action_is_returned_as_not_found(self) -> None:
        with self.assertRaises(HTTPException) as context:
            await confirm_ai_action(
                payload=self.payload,
                request=self.request,
                action_id=999,
                admin_password="test-password",  # pragma: allowlist secret
            )

        self.assertEqual(context.exception.status_code, 404)

    async def test_non_pending_action_is_returned_as_conflict(self) -> None:
        app.state.pending_actions = FailingPendingActionService(PendingActionStatusError())

        with self.assertRaises(HTTPException) as context:
            await cancel_ai_action(
                payload=self.payload,
                request=self.request,
                action_id=42,
                admin_password="test-password",  # pragma: allowlist secret
            )

        self.assertEqual(context.exception.status_code, 409)

    async def test_invalid_payload_is_returned_as_unprocessable(self) -> None:
        app.state.pending_actions = FailingPendingActionService(PendingActionPayloadError())

        with self.assertRaises(HTTPException) as context:
            await confirm_ai_action(
                payload=self.payload,
                request=self.request,
                action_id=42,
                admin_password="test-password",  # pragma: allowlist secret
            )

        self.assertEqual(context.exception.status_code, 422)
