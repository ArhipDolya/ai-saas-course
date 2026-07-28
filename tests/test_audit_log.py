from __future__ import annotations

from unittest import IsolatedAsyncioTestCase

from app.audit_log import record_confirmed_action
from app.models import PendingAction, PendingActionStatus


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_calls = 0

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flush_calls += 1


class AuditLogTests(IsolatedAsyncioTestCase):
    async def test_confirmed_action_is_recorded_with_its_payload(self) -> None:
        payload = {
            "transaction_id": 7,
            "amount": "140.00",
        }
        action = PendingAction(
            action_id=12,
            user_id=1,
            thread_id="00000000-0000-4000-8000-000000000001",
            action_type="update_transaction_sum",
            payload=payload,
            status=PendingActionStatus.confirmed.value,
        )
        session = FakeSession()

        audit_record = await record_confirmed_action(session, action)

        self.assertEqual(session.added, [audit_record])
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(audit_record.action_id, 12)
        self.assertEqual(audit_record.action_type, "update_transaction_sum")
        self.assertEqual(audit_record.payload, payload)
        self.assertIsNot(audit_record.payload, payload)
