from __future__ import annotations

from copy import deepcopy

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, PendingAction


async def record_confirmed_action(
    session: AsyncSession,
    action: PendingAction,
) -> AuditLog:
    """Persist an audit record in the same transaction as the confirmed action."""

    audit_record = AuditLog(
        action_id=action.action_id,
        user_id=action.user_id,
        thread_id=action.thread_id,
        action_type=action.action_type,
        payload=deepcopy(action.payload),
    )
    session.add(audit_record)
    await session.flush()
    return audit_record
