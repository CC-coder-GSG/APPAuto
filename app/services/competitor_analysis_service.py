from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import CompetitorAnalysisReport, User
from app.utils.time_utils import local_now


REPORT_ID = "survey-master"
INITIAL_REPORT_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "competitor-analysis"
    / "report.initial.json"
)
MAX_REPORT_BYTES = 5 * 1024 * 1024


class CompetitorAnalysisService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _serialize(data: dict[str, Any]) -> str:
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > MAX_REPORT_BYTES:
            raise HTTPException(status_code=413, detail="竞品分析报告过大（最大 5MB）")
        return raw

    @staticmethod
    def _initial_data() -> dict[str, Any]:
        try:
            data = json.loads(INITIAL_REPORT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail="竞品分析初始数据不可用") from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=500, detail="竞品分析初始数据格式错误")
        return data

    def _get_or_create(self, user: User) -> CompetitorAnalysisReport:
        report = self.db.get(CompetitorAnalysisReport, REPORT_ID)
        if report:
            return report
        report = CompetitorAnalysisReport(
            report_id=REPORT_ID,
            data_json=self._serialize(self._initial_data()),
            version=1,
            updated_by_id=user.id,
            updated_by_name=user.shown_name,
            updated_at=local_now(),
        )
        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)
        return report

    @staticmethod
    def _response(report: CompetitorAnalysisReport) -> dict[str, Any]:
        return {
            "data": json.loads(report.data_json),
            "version": report.version,
            "updatedAt": report.updated_at.isoformat(),
            "updatedBy": report.updated_by_name,
        }

    def get_report(self, user: User) -> dict[str, Any]:
        return self._response(self._get_or_create(user))

    def save_report(
        self,
        data: dict[str, Any],
        base_version: int,
        user: User,
    ) -> dict[str, Any]:
        self._get_or_create(user)
        now = local_now()
        result = self.db.execute(
            update(CompetitorAnalysisReport)
            .where(
                CompetitorAnalysisReport.report_id == REPORT_ID,
                CompetitorAnalysisReport.version == base_version,
            )
            .values(
                data_json=self._serialize(data),
                version=base_version + 1,
                updated_by_id=user.id,
                updated_by_name=user.shown_name,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise HTTPException(status_code=409, detail="报告已被其他用户更新，请刷新后重试")
        self.db.commit()
        report = self.db.get(CompetitorAnalysisReport, REPORT_ID)
        return self._response(report)
