from typing import Any

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import AuditLog


def audit(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    *,
    store_id: int | None = None,
    actor: str = "owner",
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        at=utcnow(), actor=actor, action=action, entity_type=entity_type,
        entity_id=entity_id, store_id=store_id, details=details or {},
    )
    db.add(entry)
    return entry
