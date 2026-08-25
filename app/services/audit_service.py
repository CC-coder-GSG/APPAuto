from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.services.sse_service import sse_publish


def audit(
    db: Session,
    action: str,
    target_type: str,
    actor_id: int | None = None,
    target_id: str | None = None,
    detail: str | None = None,
) -> None:
    row = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
    )
    db.add(row)
    db.commit()
    try:
        sse_publish(
            "activity_created",
            {
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "detail": detail,
                "actor_id": actor_id,
            },
            channels=["global"],
        )
        if target_type in {"requirement", "bug", "test_case", "feedback", "field_test"}:
            sse_publish(
                "report_data_changed",
                {"target_type": target_type, "action": action, "target_id": target_id},
                channels=["global"],
            )
    except Exception:
        # SSE publish failure should not block main business transaction.
        pass
