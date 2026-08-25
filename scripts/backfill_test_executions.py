from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

# 兼容从任意目录执行脚本，确保可以导入 app 包
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import SessionLocal
from app.models import BugTracking, Requirement, TestExecution, TestResultStatus, Version, VersionType

BACKFILL_NOTE = "历史数据回填：原需求已勾选测试完成，但缺少执行记录，系统自动补录"


def _find_existing_minor(db: Session, requirement: Requirement) -> int | None:
    bug = (
        db.query(BugTracking)
        .filter(BugTracking.requirement_id == requirement.id, BugTracking.found_minor_version_id.isnot(None))
        .order_by(BugTracking.created_at.desc(), BugTracking.id.desc())
        .first()
    )
    if not bug or not bug.found_minor_version_id:
        return None
    minor = db.query(Version).filter(Version.id == bug.found_minor_version_id).first()
    if not minor or minor.version_type != VersionType.MINOR:
        return None
    if minor.parent_id != requirement.major_version_id:
        return None
    return minor.id


def _find_or_create_backfill_minor(db: Session, requirement: Requirement, apply: bool) -> tuple[int | None, str]:
    major = db.query(Version).filter(Version.id == requirement.major_version_id, Version.version_type == VersionType.MAJOR).first()
    if not major:
        return None, "需求关联的大版本不存在"

    backfill_no = f"历史补录-{major.version_no}"
    exists = (
        db.query(Version)
        .filter(Version.version_type == VersionType.MINOR, Version.parent_id == major.id, Version.version_no == backfill_no)
        .first()
    )
    if exists:
        return exists.id, "已使用现有历史补录小版本"

    if not apply:
        return None, f"将自动创建小版本：{backfill_no}"

    created = Version(
        version_no=backfill_no,
        version_type=VersionType.MINOR,
        parent_id=major.id,
        software_id=major.software_id,
    )
    db.add(created)
    db.flush()
    return created.id, f"已自动创建小版本：{backfill_no}"


def backfill_test_executions(db: Session, *, apply: bool = False, major_id: int | None = None, verbose: bool = False) -> dict[str, Any]:
    q = db.query(Requirement).filter(Requirement.test_completed.is_(True))
    if major_id:
        q = q.filter(Requirement.major_version_id == major_id)
    all_reqs = q.order_by(Requirement.id.asc()).all()

    scanned = len(all_reqs)
    repaired = 0
    skipped = 0
    failed = 0
    details: list[dict[str, Any]] = []

    for req in all_reqs:
        has_execution = db.query(TestExecution.id).filter(TestExecution.requirement_id == req.id).first() is not None
        if has_execution:
            skipped += 1
            if verbose:
                details.append({"requirement_id": req.id, "zentao_req_id": req.zentao_req_id, "status": "跳过", "reason": "已有执行记录"})
            continue

        minor_id = _find_existing_minor(db, req)
        reason = "使用关联Bug推断的小版本" if minor_id else ""

        if not minor_id:
            minor_id, reason = _find_or_create_backfill_minor(db, req, apply)
            if not minor_id and not apply:
                # dry-run 下还未创建版本，继续按“计划补录”统计
                details.append({
                    "requirement_id": req.id,
                    "zentao_req_id": req.zentao_req_id,
                    "status": "计划补录",
                    "minor": "待创建历史补录小版本",
                    "reason": reason,
                })
                continue

        if not minor_id:
            failed += 1
            details.append({"requirement_id": req.id, "zentao_req_id": req.zentao_req_id, "status": "失败", "reason": reason or "无法确定小版本"})
            continue

        if not apply:
            details.append({
                "requirement_id": req.id,
                "zentao_req_id": req.zentao_req_id,
                "status": "计划补录",
                "minor_version_id": minor_id,
                "reason": reason,
            })
            continue

        executed_at = req.updated_at or req.created_at or datetime.utcnow()
        execution = TestExecution(
            requirement_id=req.id,
            minor_version_id=minor_id,
            result_status=TestResultStatus.PARTIAL,
            notes=BACKFILL_NOTE,
            executed_by_id=req.owner_id,
            executed_at=executed_at,
        )
        db.add(execution)
        repaired += 1
        details.append({
            "requirement_id": req.id,
            "zentao_req_id": req.zentao_req_id,
            "status": "已补录",
            "minor_version_id": minor_id,
            "reason": reason,
        })

    if apply:
        db.commit()

    pending = len([d for d in details if d.get("status") == "计划补录"])
    if not apply:
        repaired = pending

    return {
        "apply": apply,
        "scanned": scanned,
        "repaired": repaired,
        "skipped": skipped,
        "failed": failed,
        "details": details,
    }


def _print_summary(result: dict[str, Any]) -> None:
    mode = "正式执行" if result.get("apply") else "仅预演（dry-run）"
    print(f"\n=== 历史测试执行回填：{mode} ===")
    print(f"总扫描数：{result.get('scanned', 0)}")
    print(f"待/已回填数：{result.get('repaired', 0)}")
    print(f"已跳过数：{result.get('skipped', 0)}")
    print(f"失败数：{result.get('failed', 0)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="回填 requirements.test_completed=true 但缺少 test_executions 的历史数据")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写数据库")
    parser.add_argument("--apply", action="store_true", help="执行回填，写入数据库")
    parser.add_argument("--major-id", type=int, default=None, help="仅处理指定大版本ID")
    parser.add_argument("--verbose", action="store_true", help="输出每条处理明细")
    args = parser.parse_args()

    # 默认 dry-run，只有显式 --apply 才真正写库
    apply = bool(args.apply and not args.dry_run)

    db = SessionLocal()
    try:
        result = backfill_test_executions(db, apply=apply, major_id=args.major_id, verbose=args.verbose)
        _print_summary(result)
        if args.verbose:
            print("\n--- 明细 ---")
            for item in result.get("details", []):
                print(item)
    finally:
        db.close()


if __name__ == "__main__":
    main()
