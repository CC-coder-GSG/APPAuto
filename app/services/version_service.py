from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Version


class VersionService:
    @staticmethod
    def list_versions(db: Session) -> list[dict]:
        rows = db.query(Version).order_by(Version.created_at.desc()).all()
        return [
            {
                "id": row.id,
                "version_no": row.version_no,
                "version_type": row.version_type.value,
                "parent_id": row.parent_id,
            }
            for row in rows
        ]
