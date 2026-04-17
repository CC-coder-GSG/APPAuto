from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Version


class VersionService:
    @staticmethod
    def list_versions(db: Session, software_id: int | None = None) -> list[dict]:
        q = db.query(Version)
        if software_id:
            q = q.filter(Version.software_id == software_id)
        rows = q.order_by(Version.created_at.desc()).all()

        return [
            {
                "id": row.id,
                "version_no": row.version_no,
                "version_type": row.version_type.value,
                "parent_id": row.parent_id,
                "software_id": row.software_id,
                "zentao_execution_id": row.zentao_execution_id,
            }
            for row in rows
        ]
