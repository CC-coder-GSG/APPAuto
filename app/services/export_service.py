from __future__ import annotations

import tempfile
from csv import DictWriter
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy.orm import Session, joinedload

from app.models import Requirement, TestExecution


class ExportService:
    def __init__(self, db: Session):
        self.db = db

    def build_rows(self, major_version_id: Optional[int], minor_version_id: Optional[int]) -> list[dict[str, str]]:
        query = self.db.query(Requirement).options(
            joinedload(Requirement.major_version),
            joinedload(Requirement.owner),
            joinedload(Requirement.test_cases),
            joinedload(Requirement.test_executions).joinedload(TestExecution.minor_version),
        )
        if major_version_id is not None:
            query = query.filter(Requirement.major_version_id == major_version_id)

        rows: list[dict[str, str]] = []
        for req in query.order_by(Requirement.id.asc()).all():
            executions = req.test_executions
            if minor_version_id is not None:
                executions = [e for e in executions if e.minor_version_id == minor_version_id]
            if not executions:
                executions = [None]
            for exe in executions:
                rows.append(
                    {
                        "requirement_id": str(req.id),
                        "major_version": req.major_version.version_no,
                        "zentao_req_id": req.zentao_req_id,
                        "title": req.title,
                        "owner": req.owner.shown_name if req.owner else "",
                        "case_ids": ", ".join(c.zentao_case_id for c in req.test_cases),
                        "case_completed": str(req.case_completed),
                        "test_completed": str(req.test_completed),
                        "status": req.status.value,
                        "minor_version": exe.minor_version.version_no if exe else "",
                        "result_status": exe.result_status if exe else "",
                        "bug_id": exe.bug_id if exe else "",
                        "source_case_id": exe.source_case_id if exe else "",
                        "executed_at": exe.executed_at.isoformat() if exe else "",
                    }
                )
        return rows

    def export(self, format: str, major_version_id: Optional[int], minor_version_id: Optional[int]) -> tuple[Path, str]:
        rows = self.build_rows(major_version_id, minor_version_id)
        if not rows:
            raise HTTPException(status_code=404, detail="No data for export")

        fields = ["requirement_id", "major_version", "zentao_req_id", "title", "owner", "case_ids", "case_completed", "test_completed", "status", "minor_version", "result_status", "bug_id", "source_case_id", "executed_at"]
        tmp_dir = Path(tempfile.gettempdir())
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        if format == "csv":
            out_path = tmp_dir / f"appauto_export_{stamp}.csv"
            with out_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            media_type = "text/csv"
        else:
            out_path = tmp_dir / f"appauto_export_{stamp}.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "export"
            ws.append(fields)
            for row in rows:
                ws.append([row.get(k, "") for k in fields])
            wb.save(out_path)
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return out_path, media_type
