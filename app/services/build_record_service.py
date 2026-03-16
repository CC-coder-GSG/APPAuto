from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import BuildRecord


class BuildRecordService:
    def __init__(self, db: Session):
        self.db = db

    def upsert_report(
        self,
        *,
        job_name: str,
        build_number: str | int,
        build_status: str,
        version_name: str | None = None,
        branch: str | None = None,
        build_url: str | None = None,
        change_log: str | None = None,
    ) -> tuple[BuildRecord, str]:
        build_number_text = str(build_number).strip()
        record = (
            self.db.query(BuildRecord)
            .filter(BuildRecord.job_name == job_name, BuildRecord.build_number == build_number_text)
            .first()
        )
        action = "updated" if record else "created"
        if not record:
            record = BuildRecord(job_name=job_name, build_number=build_number_text, build_status=build_status)
            self.db.add(record)

        record.build_status = build_status
        record.version_name = version_name
        record.branch = branch
        record.build_url = build_url
        record.change_log = change_log

        self.db.commit()
        self.db.refresh(record)
        return record, action

    def list_records(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        job_name: Optional[str] = None,
        build_status: Optional[str] = None,
    ) -> dict:
        query = self.db.query(BuildRecord)
        if job_name:
            query = query.filter(BuildRecord.job_name == job_name.strip())
        if build_status:
            query = query.filter(BuildRecord.build_status == build_status.strip().upper())

        total = query.count()
        rows = (
            query.order_by(BuildRecord.created_at.desc(), BuildRecord.id.desc())
            .offset(max(0, offset))
            .limit(max(1, min(limit, 200)))
            .all()
        )
        return {
            "items": [self.serialize(row) for row in rows],
            "total": total,
            "limit": max(1, min(limit, 200)),
            "offset": max(0, offset),
        }

    def get_record(self, record_id: int) -> dict | None:
        row = self.db.query(BuildRecord).filter(BuildRecord.id == record_id).first()
        return self.serialize(row) if row else None

    @staticmethod
    def serialize(row: BuildRecord | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row.id,
            "job_name": row.job_name,
            "build_number": row.build_number,
            "build_status": row.build_status,
            "version_name": row.version_name,
            "branch": row.branch,
            "build_url": row.build_url,
            "change_log": row.change_log,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


__all__ = ["BuildRecordService"]
