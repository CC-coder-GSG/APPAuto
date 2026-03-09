from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Version, VersionType


class VersionService:
    @staticmethod
    def list_versions(db: Session, software_id: int | None = None) -> list[dict]:
        def _fetch_rows() -> list[Version]:
            q = db.query(Version)
            if software_id:
                q = q.filter(Version.software_id == software_id)
            return q.order_by(Version.created_at.desc()).all()

        rows = _fetch_rows()

        # 兼容历史数据：如果按软件筛选为空，且存在未归属软件的版本，则自动回填一次
        if software_id and not rows:
            legacy_rows = db.query(Version).filter(Version.software_id.is_(None)).all()
            if legacy_rows:
                for v in legacy_rows:
                    if v.version_type == VersionType.MAJOR:
                        v.software_id = software_id
                db.flush()
                major_soft_map = {
                    m.id: m.software_id
                    for m in db.query(Version).filter(Version.version_type == VersionType.MAJOR).all()
                }
                for v in legacy_rows:
                    if v.version_type == VersionType.MINOR:
                        v.software_id = major_soft_map.get(v.parent_id) or software_id
                db.commit()
                rows = _fetch_rows()

        return [
            {
                "id": row.id,
                "version_no": row.version_no,
                "version_type": row.version_type.value,
                "parent_id": row.parent_id,
                "software_id": row.software_id,
            }
            for row in rows
        ]
