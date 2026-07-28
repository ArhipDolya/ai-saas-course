from __future__ import annotations

from unittest import TestCase

from sqlalchemy import Enum as SqlEnum

from app.models import PendingAction, PendingActionStatus
from app.pending_actions import (
    CREATE_TRANSACTION_TOOL,
    DELETE_TRANSACTION_TOOL,
    PendingActionPayloadError,
    UPDATE_TRANSACTION_CATEGORY_TOOL,
    UPDATE_TRANSACTION_SUM_TOOL,
    WRITE_ACTION_TOOL_NAMES,
    validate_create_transaction_payload,
    validate_pending_action_payload,
)


class PendingActionPayloadTests(TestCase):
    def test_pending_action_status_uses_the_postgres_enum(self) -> None:
        status_column = PendingAction.__table__.c.status

        self.assertIsInstance(status_column.type, SqlEnum)
        self.assertEqual(status_column.type.name, "pending_action_status")
        self.assertIn(PendingActionStatus.pending.value, status_column.type.enums)
        self.assertIn(PendingActionStatus.confirmed.value, status_column.type.enums)

    def test_create_transaction_payload_is_validated_before_confirmation(self) -> None:
        payload = validate_create_transaction_payload(
            {
                "type": "expense",
                "amount": "450.50",
                "category": " Транспорт ",
                "description": " Таксі ",
                "date": "2026-07-28",
            }
        )

        self.assertEqual(str(payload.amount), "450.50")
        self.assertEqual(payload.category, "Транспорт")
        self.assertEqual(payload.description, "Таксі")

    def test_invalid_pending_payload_is_rejected(self) -> None:
        with self.assertRaises(PendingActionPayloadError):
            validate_create_transaction_payload(
                {
                    "type": "expense",
                    "amount": "9999999999",
                    "category": "123",
                }
            )

    def test_write_tools_have_typed_normalized_payloads(self) -> None:
        category_update = validate_pending_action_payload(
            UPDATE_TRANSACTION_CATEGORY_TOOL,
            {"transaction_id": 15, "category": " Продукти "},
        )
        sum_update = validate_pending_action_payload(
            UPDATE_TRANSACTION_SUM_TOOL,
            {"transaction_id": 15, "amount": "99.90"},
        )
        deletion = validate_pending_action_payload(
            DELETE_TRANSACTION_TOOL,
            {"transaction_id": 15},
        )

        self.assertEqual(category_update.category, "Продукти")
        self.assertEqual(str(sum_update.amount), "99.90")
        self.assertEqual(deletion.transaction_id, 15)
        self.assertEqual(
            WRITE_ACTION_TOOL_NAMES,
            {
                CREATE_TRANSACTION_TOOL,
                UPDATE_TRANSACTION_CATEGORY_TOOL,
                UPDATE_TRANSACTION_SUM_TOOL,
                DELETE_TRANSACTION_TOOL,
            },
        )

    def test_invalid_transaction_id_is_rejected(self) -> None:
        with self.assertRaises(PendingActionPayloadError):
            validate_pending_action_payload(
                DELETE_TRANSACTION_TOOL,
                {"transaction_id": 0},
            )
