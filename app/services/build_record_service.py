from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models import BuildRecord, Version
from app.models.enums import VersionType
from app.services.sse_service import sse_publish
from app.services.zentao_build_push_service import (
    apply_push_result_to_record,
    push_build_to_zentao,
)
from app.services.zentao_utils import normalize_version_name, parse_job_name_to_major_version_no

logger = logging.getLogger(__name__)


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

        # Auto-archive as minor version if the build succeeded
        archive_status, archive_minor_id, archive_message = self._try_auto_archive(record)
        if archive_status:
            record.auto_archive_status = archive_status
            record.auto_archive_minor_version_id = archive_minor_id
            record.auto_archive_message = archive_message
            self.db.commit()
            self.db.refresh(record)

        # 自动把 build 写回禅道（占位重命名 + 新占位 / 多执行）
        try:
            push = push_build_to_zentao(self.db, record)
            apply_push_result_to_record(record, push)
            self.db.commit()
            self.db.refresh(record)
        except Exception as exc:
            # 极少数情况下 push 自身崩溃 — 不要让 Jenkins 接口 500
            logger.exception("push_build_to_zentao crashed | record_id=%s err=%s", record.id, exc)
            record.zentao_push_status = "error"
            record.zentao_push_message = f"推送禅道异常：{exc}"[:480]
            self.db.commit()

        # 写回禅道后，立即从禅道回拉该执行的 build 列表，把本地小版本与禅道对齐
        # （补 zentao_build_id / 跟随重命名），保证小版本始终和禅道一致。失败不影响主流程。
        try:
            self._try_sync_minors_from_zentao(record)
            self.db.refresh(record)
        except Exception as exc:
            logger.warning("sync minors from zentao crashed | record_id=%s err=%s", record.id, exc)

        sse_publish(
            "build_record_created" if action == "created" else "build_record_updated",
            {"item": self.serialize(record), "action": action},
            channels=["global"],
        )
        return record, action

    def _try_auto_archive(self, record: BuildRecord) -> tuple[str, int | None, str]:
        """
        Attempt to automatically create a local minor version from the build record.

        Returns (status, minor_version_id_or_None, human_readable_message).

        Status values:
            ok            — minor version created successfully
            skipped       — minor version already exists (idempotent)
            not_success   — build_status is not SUCCESS, skipped
            empty_version_name — version_name is absent or empty after normalisation
            no_parent     — no matching local major version found for job_name
            error         — unexpected exception
        """
        if (record.build_status or '').upper() != 'SUCCESS':
            return 'not_success', None, f'构建状态为 {record.build_status}，仅 SUCCESS 包自动归档'

        normalized = normalize_version_name(record.version_name or '')
        if not normalized:
            return 'empty_version_name', None, 'version_name 为空，跳过自动归档'

        major_version_no = parse_job_name_to_major_version_no(record.job_name or '')
        if not major_version_no:
            return 'no_parent', None, f'无法从 job_name "{record.job_name}" 解析大版本号'

        try:
            major = (
                self.db.query(Version)
                .filter(
                    Version.version_no == major_version_no,
                    Version.version_type == VersionType.MAJOR,
                )
                .first()
            )
            if not major:
                return (
                    'no_parent',
                    None,
                    f'本地未找到大版本 {major_version_no}，构建记录已保存但未归档小版本',
                )

            # Check for duplicate minor version under the same parent
            existing = (
                self.db.query(Version)
                .filter(
                    Version.version_no == normalized,
                    Version.version_type == VersionType.MINOR,
                    Version.parent_id == major.id,
                )
                .first()
            )
            if existing:
                return 'skipped', existing.id, f'小版本 {normalized} 已存在（id={existing.id}），跳过'

            minor = Version(
                version_no=normalized,
                version_type=VersionType.MINOR,
                parent_id=major.id,
                software_id=major.software_id,
            )
            self.db.add(minor)
            self.db.commit()
            self.db.refresh(minor)
            logger.info(
                "Auto-archived minor version | job=%s version_name=%s major=%s minor_id=%s",
                record.job_name,
                normalized,
                major_version_no,
                minor.id,
            )
            return 'ok', minor.id, f'自动归档成功：{normalized} -> {major_version_no}（小版本 id={minor.id}）'

        except Exception as e:
            logger.exception("Auto-archive minor version failed | job=%s version_name=%s: %s", record.job_name, normalized, e)
            return 'error', None, f'归档异常：{e}'

    def _try_sync_minors_from_zentao(self, record: BuildRecord) -> None:
        """
        收到构建并写回禅道后，从禅道回拉该执行下的 build 列表，把本地小版本与
        禅道对齐（补 zentao_build_id / 跟随禅道重命名）。

        - 仅处理 SUCCESS 包；
        - 用系统禅道账号（webhook 无用户级绑定）；
        - 幂等，单次失败仅记日志、不影响构建报告主流程。
        """
        from app.services.zentao_system_client import get_system_zentao_client
        from app.services.zentao_version_sync_service import ZentaoVersionSyncService

        if (record.build_status or "").upper() != "SUCCESS":
            return

        major_version_no = parse_job_name_to_major_version_no(record.job_name or "")
        if not major_version_no:
            return

        majors = (
            self.db.query(Version)
            .filter(
                Version.version_no == major_version_no,
                Version.version_type == VersionType.MAJOR,
            )
            .all()
        )
        bound_majors = [m for m in majors if m.zentao_execution_id]
        if not bound_majors:
            return

        client = get_system_zentao_client(self.db)
        if not client:
            logger.info("sync minors from zentao skipped: no system zentao client | job=%s", record.job_name)
            return

        sync_service = ZentaoVersionSyncService(self.db)
        for major in bound_majors:
            try:
                sync_service.reconcile_execution_minors(
                    client,
                    software_id=major.software_id,
                    parent_id=major.id,
                    zentao_execution_id=int(major.zentao_execution_id),
                )
            except Exception as exc:
                logger.warning(
                    "reconcile minors failed | major=%s exec=%s err=%s",
                    major.version_no, major.zentao_execution_id, exc,
                )

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

    def retry_zentao_push(self, record_id: int) -> tuple[dict, str]:
        """
        手动重试一条 BuildRecord 的禅道写回。返回 (serialized_record, push_status)。

        Raises ValueError when the record does not exist; raises RuntimeError
        when the push itself crashes (route layer 转 500，前端展示原因)。
        """
        record = self.db.query(BuildRecord).filter(BuildRecord.id == record_id).first()
        if not record:
            raise ValueError("构建记录不存在")

        try:
            push = push_build_to_zentao(self.db, record)
            apply_push_result_to_record(record, push)
            self.db.commit()
            self.db.refresh(record)
        except Exception as exc:
            logger.exception(
                "retry push_build_to_zentao crashed | record_id=%s err=%s",
                record.id,
                exc,
            )
            record.zentao_push_status = "error"
            record.zentao_push_message = f"重试推送禅道异常：{exc}"[:480]
            self.db.commit()
            self.db.refresh(record)

        serialized = self.serialize(record)
        sse_publish(
            "build_record_updated",
            {"item": serialized, "action": "updated"},
            channels=["global"],
        )
        return serialized, record.zentao_push_status or "unknown"

    def get_major_log(self, job_name: str) -> dict:
        job_name_text = (job_name or "").strip()
        rows = (
            self.db.query(BuildRecord)
            .filter(BuildRecord.job_name == job_name_text)
            .order_by(BuildRecord.created_at.asc(), BuildRecord.id.asc())
            .all()
        )

        latest = rows[-1] if rows else None
        sections: list[str] = []
        for row in rows:
            content = (row.change_log or "").strip()
            if not content:
                continue
            if row.version_name:
                title = row.version_name
            elif row.build_number:
                title = f"Build #{row.build_number}"
            else:
                title = "未知版本"
            sections.append(f"## {title}\n{content}")

        return {
            "job_name": job_name_text,
            "record_count": len(rows),
            "latest_version_name": latest.version_name if latest else None,
            "latest_build_number": latest.build_number if latest else None,
            "major_log": "\n\n".join(sections),
        }

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
            "auto_archive_status": row.auto_archive_status,
            "auto_archive_minor_version_id": row.auto_archive_minor_version_id,
            "auto_archive_message": row.auto_archive_message,
            "zentao_push_status": row.zentao_push_status,
            "zentao_push_message": row.zentao_push_message,
            "zentao_pushed_at": row.zentao_pushed_at.isoformat() if row.zentao_pushed_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


__all__ = ["BuildRecordService"]
