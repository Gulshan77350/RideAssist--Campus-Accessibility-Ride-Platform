import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from rideassist_common.models import AuditLog


def record_audit(
    session: AsyncSession,
    action: str,
    *,
    college_id: uuid.UUID | None,
    actor_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: object | None = None,
    ip: str | None = None,
    **details,
) -> None:
    """Adds an audit row to the caller's transaction (committed together with the change itself)."""
    session.add(
        AuditLog(
            college_id=college_id,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=details,
            ip=ip,
        )
    )
