from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.database_compare import DatabaseCompareTask
from app.services.database_compare import service
from app.services.database_compare.reports import export_report
from app.services.database_compare.result import read_result, read_tables


def access(user=Depends(get_current_user)):
    service.authorize(user)
    return user


router = APIRouter(prefix="/api/database-compare", tags=["database-compare"])


class PreviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_input_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    new_input_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    pairs: list[dict] = Field(default_factory=list, max_length=32)
    rules: dict = Field(default_factory=dict)


class TaskPayload(PreviewPayload):
    old_label: str = Field(default="旧版本", min_length=1, max_length=120)
    new_label: str = Field(default="新版本", min_length=1, max_length=120)


class ExportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: str = Field(default="excel", pattern="^(excel|json)$")


class CleanupPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)


def result_path(task):
    path = service.owned_path("tasks", task.id) / "result.sqlite"
    if task.status not in service.TERMINAL:
        raise HTTPException(409, "比较尚未结束，请等待任务完成")
    if not path.is_file():
        raise HTTPException(404, "任务未产生结果文件，请查看错误信息")
    return path


@router.post("/input-sets")
def upload(files: list[UploadFile] = File(...), user=Depends(access), db: Session = Depends(get_db)):
    return service.save_inputs(db, files, user)


@router.post("/previews")
def preview(payload: PreviewPayload, user=Depends(access), db: Session = Depends(get_db)):
    try:
        return service.preview(db, payload.old_input_id, payload.new_input_id, user, payload.pairs, payload.rules)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/tasks", status_code=201)
def create(payload: TaskPayload, user=Depends(access), db: Session = Depends(get_db)):
    try:
        return service.create_task(db, payload, user)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/tasks")
def list_tasks(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               user=Depends(access), db: Session = Depends(get_db)):
    query = db.query(DatabaseCompareTask)
    if not service.is_admin(user):
        query = query.filter_by(owner_id=user.id)
    count = query.count()
    items = query.order_by(DatabaseCompareTask.created_at.desc(), DatabaseCompareTask.id).offset((page - 1) * page_size).limit(page_size)
    return {"total": count, "finished_total": query.filter(DatabaseCompareTask.status.in_(service.TERMINAL)).count(),
            "page": page, "page_size": page_size, "items": [service.serialize_task(t) for t in items]}


@router.post("/tasks/cleanup")
def cleanup(payload: CleanupPayload, user=Depends(access), db: Session = Depends(get_db)):
    return service.cleanup_tasks(db, user, payload.task_ids)


@router.delete("/tasks/{identity}")
def delete_task(identity: str, user=Depends(access), db: Session = Depends(get_db)):
    return service.cleanup_tasks(db, user, [identity])


@router.get("/tasks/{identity}")
def task(identity: str, user=Depends(access), db: Session = Depends(get_db)):
    return service.serialize_task(service.get_task(db, identity, user))


@router.post("/tasks/{identity}/cancel")
def cancel(identity: str, user=Depends(access), db: Session = Depends(get_db)):
    return service.cancel_task(db, service.get_task(db, identity, user))


@router.get("/tasks/{identity}/tables")
def tables(identity: str, user=Depends(access), db: Session = Depends(get_db)):
    return {"items": read_tables(result_path(service.get_task(db, identity, user)))}


@router.get("/tasks/{identity}/differences")
def differences(identity: str, database: str = "", table: str = "", scope: str = "", kind: str = "",
                preview: bool = False,
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                user=Depends(access), db: Session = Depends(get_db)):
    path = result_path(service.get_task(db, identity, user))
    return read_result(path, database=database, table=table, scope=scope, kind=kind, page=page, page_size=page_size, preview=preview)


@router.post("/tasks/{identity}/exports")
def export(identity: str, payload: ExportPayload, user=Depends(access), db: Session = Depends(get_db)):
    item = service.get_task(db, identity, user)
    data = service.serialize_task(item)
    summary = data["summary"] or {"status": item.status, "error": item.error, "conclusion": "inconclusive"}
    summary = {**summary, "task_id": identity, "old_label": item.old_label, "new_label": item.new_label,
               "status": item.status, "rules": data["rules"], "pairs": data["pairs"]}
    target = export_report(result_path(item), service.owned_path("tasks", identity) / "exports", summary, payload.format)
    return {"export_id": target.name, "download_url": f"/api/database-compare/tasks/{identity}/exports/{target.name}"}


@router.get("/tasks/{identity}/exports/{export_id}")
def download(identity: str, export_id: str, user=Depends(access), db: Session = Depends(get_db)):
    service.get_task(db, identity, user)
    if not re.fullmatch(r"[0-9a-f]{32}\.(json|zip)", export_id):
        raise HTTPException(400, "导出标识无效")
    target = service.owned_path("tasks", identity) / "exports" / export_id
    if not target.is_file():
        raise HTTPException(404, "导出文件不存在")
    return FileResponse(target, filename="数据库对比报告" + target.suffix,
                        media_type="application/zip" if target.suffix == ".zip" else "application/json")
