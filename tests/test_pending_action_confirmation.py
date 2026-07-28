from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from app.models import PendingAction, PendingActionStatus, User
from app.pending_actions import PendingActionService


class FakeTransactionContext:
    async def __aenter__(self) -> "FakeTransactionContext":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    def begin(self) -> FakeTransactionContext:
        return FakeTransactionContext()


class PendingActionConfirmationTests(IsolatedAsyncioTestCase):
    async def test_confirm_records_audit_after_action_is_confirmed(self) -> None:
        action = PendingAction(
            action_id=42,
            user_id=1,
            thread_id="00000000-0000-4000-8000-000000000001",
            action_type="update_transaction_sum",
            payload={"transaction_id": 7, "amount": "140.00"},
            status=PendingActionStatus.pending.value,
        )
        session = FakeSession()
        service = PendingActionService(lambda: session)
        service._get_pending_action_for_update = AsyncMock(return_value=action)
        service._execute_confirmed_action = AsyncMock(
            return_value={"transaction": {"id": 7, "amount": "140.00"}}
        )
        user = User(
            id=1,
            telegram_id=0,
            username="admin_dashboard",
            first_name="Admin",
            last_name="Dashboard",
        )

        async def assert_audit_record(
            received_session: FakeSession,
            received_action: PendingAction,
        ) -> None:
            self.assertIs(received_session, session)
            self.assertIs(received_action, action)
            self.assertEqual(action.status, PendingActionStatus.confirmed.value)

        with (
            patch(
                "app.pending_actions.get_or_create_admin_user",
                new=AsyncMock(return_value=user),
            ),
            patch(
                "app.pending_actions.record_confirmed_action",
                new=AsyncMock(side_effect=assert_audit_record),
            ) as record_audit,
        ):
            confirmed = await service.confirm(
                action_id=42,
                thread_id=action.thread_id,
            )

        self.assertEqual(confirmed.action, action)
        self.assertEqual(confirmed.result["transaction"]["amount"], "140.00")
        record_audit.assert_awaited_once_with(session, action)
