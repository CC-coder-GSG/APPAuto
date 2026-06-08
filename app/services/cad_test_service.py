from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session, joinedload

from app.models import (
    BugTracking,
    CadAttachment,
    CadBoard,
    CadCustomColumn,
    CadItem,
    CadItemFile,
    CadRecord,
    CadVersion,
    User,
)
from app.services.audit_service import audit
from app.utils.time_utils import local_now

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
VIDEO_EXTS = {".mp4", ".webm", ".ogg", ".ogv", ".mov", ".m4v", ".avi", ".mkv"}
CAD_EXTS = {".dwg", ".dxf", ".dwf", ".dgn", ".dwt"}
ALLOWED_KINDS = {"cad", "screenshot", "video"}
UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "uploads" / "cad"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CadTestService:
    def __init__(self, db: Session):
        self.db = db

    # ----------------------------------------------------------------- boards
    def list_boards(self) -> list[dict]:
        rows = self.db.query(CadBoard).order_by(CadBoard.sort_order.asc(), CadBoard.id.asc()).all()
        return [
            {
                "id": b.id,
                "name": b.name,
                "description": b.description,
                "version_count": len(b.versions),
                "item_count": len(b.items),
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in rows
        ]

    def create_board(self, name: str, description: str | None, actor: User) -> dict:
        max_order = self.db.query(CadBoard).count()
        board = CadBoard(name=name.strip(), description=(description or "").strip() or None, sort_order=max_order, created_by_id=actor.id)
        self.db.add(board)
        self.db.commit()
        self.db.refresh(board)
        audit(self.db, action="cad.board.create", target_type="cad_board", actor_id=actor.id, target_id=str(board.id), detail=board.name)
        return {"id": board.id, "name": board.name}

    def update_board(self, board_id: int, name: str, description: str | None, actor: User) -> dict:
        board = self._get_board(board_id)
        board.name = name.strip()
        board.description = (description or "").strip() or None
        self.db.commit()
        audit(self.db, action="cad.board.update", target_type="cad_board", actor_id=actor.id, target_id=str(board.id))
        return {"message": "已更新"}

    def delete_board(self, board_id: int, actor: User) -> dict:
        board = self._get_board(board_id)
        self.db.delete(board)
        self.db.commit()
        audit(self.db, action="cad.board.delete", target_type="cad_board", actor_id=actor.id, target_id=str(board_id))
        return {"message": "已删除"}

    def get_board_detail(self, board_id: int) -> dict:
        board = (
            self.db.query(CadBoard)
            .options(
                joinedload(CadBoard.versions),
                joinedload(CadBoard.columns),
                joinedload(CadBoard.items).joinedload(CadItem.cad_files),
            )
            .filter(CadBoard.id == board_id)
            .first()
        )
        if not board:
            raise HTTPException(status_code=404, detail="统计表不存在")

        items = sorted(board.items, key=lambda x: (x.sort_order, x.id))
        item_ids = [i.id for i in items]

        # 关联禅道 Bug 信息（用于预览/跳转）。
        bug_ids = [i.zentao_bug_id for i in items if i.zentao_bug_id]
        bug_map: dict[int, BugTracking] = {}
        if bug_ids:
            for bug in self.db.query(BugTracking).filter(BugTracking.zentao_bug_id.in_(bug_ids)).all():
                if bug.zentao_bug_id is not None:
                    bug_map.setdefault(int(bug.zentao_bug_id), bug)

        records = (
            self.db.query(CadRecord)
            .options(joinedload(CadRecord.attachments))
            .filter(CadRecord.item_id.in_(item_ids))
            .all()
            if item_ids
            else []
        )
        record_map: dict[str, dict] = {}
        for r in records:
            record_map[f"{r.item_id}:{r.version_id}"] = self._serialize_record(r)

        return {
            "id": board.id,
            "name": board.name,
            "description": board.description,
            "versions": [{"id": v.id, "name": v.name, "sort_order": v.sort_order} for v in sorted(board.versions, key=lambda x: (x.sort_order, x.id))],
            "columns": [{"id": c.id, "name": c.name, "sort_order": c.sort_order} for c in sorted(board.columns, key=lambda x: (x.sort_order, x.id))],
            "items": [self._serialize_item(i, bug_map) for i in items],
            "records": record_map,
        }

    def _serialize_item(self, item: CadItem, bug_map: dict[int, BugTracking]) -> dict:
        bug = bug_map.get(int(item.zentao_bug_id)) if item.zentao_bug_id else None
        return {
            "id": item.id,
            "seq": item.seq,
            "title": item.title,
            "zentao_bug_id": item.zentao_bug_id,
            "bug": (
                {
                    "id": bug.id,
                    "bug_id": bug.bug_id,
                    "zentao_bug_url": bug.zentao_bug_url,
                    "zentao_bug_title": bug.zentao_bug_title,
                    "closed": bool(getattr(bug, "closed", False)),
                }
                if bug
                else None
            ),
            "sort_order": item.sort_order,
            # 条目级共享 CAD 文件（所有版本通用）。
            "cad_files": [
                {
                    "id": f.id,
                    "original_name": f.original_name,
                    "file_ext": f.file_ext,
                    "file_size": f.file_size,
                    "download_url": f"/api/cad/cad-files/{f.id}/download",
                }
                for f in sorted(item.cad_files, key=lambda x: x.id)
            ],
        }

    def _serialize_record(self, r: CadRecord) -> dict:
        try:
            custom_values = json.loads(r.custom_values) if r.custom_values else {}
            if not isinstance(custom_values, dict):
                custom_values = {}
        except Exception:
            custom_values = {}
        return {
            "id": r.id,
            "item_id": r.item_id,
            "version_id": r.version_id,
            "normal_count": r.normal_count,
            "abnormal_count": r.abnormal_count,
            "description": r.description,
            "custom_values": custom_values,
            "attachments": [
                {
                    "id": a.id,
                    "kind": a.kind,
                    "original_name": a.original_name,
                    "file_ext": a.file_ext,
                    "file_size": a.file_size,
                    "is_image": a.is_image,
                    "is_video": a.kind == "video" or (a.file_type or "").startswith("video/"),
                    "download_url": f"/api/cad/attachments/{a.id}/download",
                    "stream_url": f"/api/cad/attachments/{a.id}/stream",
                }
                for a in sorted(r.attachments, key=lambda x: x.id)
            ],
        }

    # --------------------------------------------------------------- versions
    def create_version(self, board_id: int, name: str, actor: User) -> dict:
        self._get_board(board_id)
        order = self.db.query(CadVersion).filter(CadVersion.board_id == board_id).count()
        v = CadVersion(board_id=board_id, name=name.strip(), sort_order=order)
        self.db.add(v)
        self.db.commit()
        self.db.refresh(v)
        audit(self.db, action="cad.version.create", target_type="cad_board", actor_id=actor.id, target_id=str(board_id), detail=v.name)
        return {"id": v.id, "name": v.name}

    def update_version(self, version_id: int, name: str, actor: User) -> dict:
        v = self._get_version(version_id)
        v.name = name.strip()
        self.db.commit()
        return {"message": "已更新"}

    def delete_version(self, version_id: int, actor: User) -> dict:
        v = self._get_version(version_id)
        self.db.delete(v)
        self.db.commit()
        return {"message": "已删除"}

    # ---------------------------------------------------------------- columns
    def create_column(self, board_id: int, name: str, actor: User) -> dict:
        self._get_board(board_id)
        order = self.db.query(CadCustomColumn).filter(CadCustomColumn.board_id == board_id).count()
        c = CadCustomColumn(board_id=board_id, name=name.strip(), sort_order=order)
        self.db.add(c)
        self.db.commit()
        self.db.refresh(c)
        audit(self.db, action="cad.column.create", target_type="cad_board", actor_id=actor.id, target_id=str(board_id), detail=c.name)
        return {"id": c.id, "name": c.name}

    def update_column(self, column_id: int, name: str, actor: User) -> dict:
        c = self.db.query(CadCustomColumn).filter(CadCustomColumn.id == column_id).first()
        if not c:
            raise HTTPException(status_code=404, detail="自定义列不存在")
        c.name = name.strip()
        self.db.commit()
        return {"message": "已更新"}

    def delete_column(self, column_id: int, actor: User) -> dict:
        c = self.db.query(CadCustomColumn).filter(CadCustomColumn.id == column_id).first()
        if not c:
            raise HTTPException(status_code=404, detail="自定义列不存在")
        self.db.delete(c)
        self.db.commit()
        return {"message": "已删除"}

    # ------------------------------------------------------------------ items
    def create_item(self, board_id: int, seq: int | None, title: str | None, zentao_bug_id: int | None, actor: User) -> dict:
        self._get_board(board_id)
        order = self.db.query(CadItem).filter(CadItem.board_id == board_id).count()
        if seq is None:
            seq = order + 1
        item = CadItem(board_id=board_id, seq=seq, title=(title or "").strip() or None, zentao_bug_id=zentao_bug_id, sort_order=order)
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        audit(self.db, action="cad.item.create", target_type="cad_board", actor_id=actor.id, target_id=str(board_id), detail=str(item.id))
        return {"id": item.id}

    def update_item(self, item_id: int, seq: int | None, title: str | None, zentao_bug_id: int | None, actor: User) -> dict:
        item = self._get_item(item_id)
        item.seq = seq
        item.title = (title or "").strip() or None
        item.zentao_bug_id = zentao_bug_id
        self.db.commit()
        return {"message": "已更新"}

    def delete_item(self, item_id: int, actor: User) -> dict:
        item = self._get_item(item_id)
        # 删除条目时一并清理其记录附件与共享 CAD 文件的物理文件。
        for rec in item.records:
            self._delete_record_files(rec)
        for cf in item.cad_files:
            self._safe_unlink(self.item_cad_file_abs_path(cf))
        self.db.delete(item)
        self.db.commit()
        return {"message": "已删除"}

    # ---------------------------------------------------------------- records
    def upsert_record(self, payload, actor: User) -> dict:
        item = self._get_item(payload.item_id)
        version = self._get_version(payload.version_id)
        if version.board_id != item.board_id:
            raise HTTPException(status_code=400, detail="版本与条目不属于同一统计表")

        rec = (
            self.db.query(CadRecord)
            .filter(CadRecord.item_id == payload.item_id, CadRecord.version_id == payload.version_id)
            .first()
        )
        if not rec:
            rec = CadRecord(item_id=payload.item_id, version_id=payload.version_id)
            self.db.add(rec)

        rec.normal_count = max(0, int(payload.normal_count or 0))
        rec.abnormal_count = max(0, int(payload.abnormal_count or 0))
        rec.description = (payload.description or "").strip() or None
        rec.custom_values = json.dumps(payload.custom_values, ensure_ascii=False) if payload.custom_values else None
        self.db.commit()
        self.db.refresh(rec)
        return {"id": rec.id, "message": "已保存"}

    def _get_or_create_record(self, item_id: int, version_id: int) -> CadRecord:
        rec = (
            self.db.query(CadRecord)
            .filter(CadRecord.item_id == item_id, CadRecord.version_id == version_id)
            .first()
        )
        if not rec:
            item = self._get_item(item_id)
            version = self._get_version(version_id)
            if version.board_id != item.board_id:
                raise HTTPException(status_code=400, detail="版本与条目不属于同一统计表")
            rec = CadRecord(item_id=item_id, version_id=version_id)
            self.db.add(rec)
            self.db.commit()
            self.db.refresh(rec)
        return rec

    # -------------------------------------------------------------- attachments
    def save_attachment(self, item_id: int, version_id: int, kind: str, upload_file: UploadFile, actor: User) -> dict:
        rec = self._get_or_create_record(item_id, version_id)
        suffix = Path(upload_file.filename or "").suffix.lower()
        if kind not in ALLOWED_KINDS:
            kind = "video" if suffix in VIDEO_EXTS else ("cad" if suffix in CAD_EXTS else "screenshot")

        now = local_now()
        folder = UPLOAD_ROOT / now.strftime("%Y") / now.strftime("%m")
        folder.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        full_path = folder / stored_name
        content = upload_file.file.read()
        with open(full_path, "wb") as f:
            f.write(content)

        guessed = upload_file.content_type or mimetypes.guess_type(upload_file.filename or "")[0] or "application/octet-stream"
        is_image = suffix in IMAGE_EXTS or guessed.startswith("image/")
        rel_path = str(full_path.relative_to(PROJECT_ROOT)).replace("\\", "/")

        att = CadAttachment(
            record_id=rec.id,
            kind=kind,
            original_name=upload_file.filename or stored_name,
            stored_name=stored_name,
            file_path=rel_path,
            file_type=guessed,
            file_ext=suffix.lstrip("."),
            file_size=len(content),
            is_image=is_image,
            uploaded_by_id=actor.id,
        )
        self.db.add(att)
        self.db.commit()
        self.db.refresh(att)
        audit(self.db, action="cad.attachment.upload", target_type="cad_record", actor_id=actor.id, target_id=str(rec.id), detail=att.original_name)
        return {
            "id": att.id,
            "kind": att.kind,
            "original_name": att.original_name,
            "file_ext": att.file_ext,
            "file_size": att.file_size,
            "is_image": att.is_image,
            "is_video": att.kind == "video" or (att.file_type or "").startswith("video/"),
            "download_url": f"/api/cad/attachments/{att.id}/download",
            "stream_url": f"/api/cad/attachments/{att.id}/stream",
        }

    def get_attachment(self, attachment_id: int) -> CadAttachment:
        att = self.db.query(CadAttachment).filter(CadAttachment.id == attachment_id).first()
        if not att:
            raise HTTPException(status_code=404, detail="附件不存在")
        return att

    def attachment_abs_path(self, att: CadAttachment) -> Path:
        return PROJECT_ROOT / att.file_path

    def delete_attachment(self, attachment_id: int, actor: User) -> dict:
        att = self.get_attachment(attachment_id)
        self._safe_unlink(self.attachment_abs_path(att))
        self.db.delete(att)
        self.db.commit()
        audit(self.db, action="cad.attachment.delete", target_type="cad_attachment", actor_id=actor.id, target_id=str(attachment_id))
        return {"message": "已删除"}

    # ------------------------------------------------------- item-level CAD files
    def save_item_cad_file(self, item_id: int, upload_file: UploadFile, actor: User) -> dict:
        item = self._get_item(item_id)
        suffix = Path(upload_file.filename or "").suffix.lower()
        now = local_now()
        folder = UPLOAD_ROOT / "cad_files" / now.strftime("%Y") / now.strftime("%m")
        folder.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        full_path = folder / stored_name
        content = upload_file.file.read()
        with open(full_path, "wb") as f:
            f.write(content)
        guessed = upload_file.content_type or mimetypes.guess_type(upload_file.filename or "")[0] or "application/octet-stream"
        rel_path = str(full_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        cf = CadItemFile(
            item_id=item.id,
            original_name=upload_file.filename or stored_name,
            stored_name=stored_name,
            file_path=rel_path,
            file_type=guessed,
            file_ext=suffix.lstrip("."),
            file_size=len(content),
            uploaded_by_id=actor.id,
        )
        self.db.add(cf)
        self.db.commit()
        self.db.refresh(cf)
        audit(self.db, action="cad.item_cad.upload", target_type="cad_item", actor_id=actor.id, target_id=str(item.id), detail=cf.original_name)
        return {
            "id": cf.id,
            "original_name": cf.original_name,
            "file_ext": cf.file_ext,
            "file_size": cf.file_size,
            "download_url": f"/api/cad/cad-files/{cf.id}/download",
        }

    def get_item_cad_file(self, file_id: int) -> CadItemFile:
        cf = self.db.query(CadItemFile).filter(CadItemFile.id == file_id).first()
        if not cf:
            raise HTTPException(status_code=404, detail="CAD 文件不存在")
        return cf

    def item_cad_file_abs_path(self, cf: CadItemFile) -> Path:
        return PROJECT_ROOT / cf.file_path

    def delete_item_cad_file(self, file_id: int, actor: User) -> dict:
        cf = self.get_item_cad_file(file_id)
        self._safe_unlink(self.item_cad_file_abs_path(cf))
        self.db.delete(cf)
        self.db.commit()
        audit(self.db, action="cad.item_cad.delete", target_type="cad_item_file", actor_id=actor.id, target_id=str(file_id))
        return {"message": "已删除"}

    # ------------------------------------------------------------------ helpers
    def _delete_record_files(self, rec: CadRecord) -> None:
        for att in rec.attachments:
            self._safe_unlink(self.attachment_abs_path(att))

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    def _get_board(self, board_id: int) -> CadBoard:
        board = self.db.query(CadBoard).filter(CadBoard.id == board_id).first()
        if not board:
            raise HTTPException(status_code=404, detail="统计表不存在")
        return board

    def _get_version(self, version_id: int) -> CadVersion:
        v = self.db.query(CadVersion).filter(CadVersion.id == version_id).first()
        if not v:
            raise HTTPException(status_code=404, detail="版本不存在")
        return v

    def _get_item(self, item_id: int) -> CadItem:
        item = self.db.query(CadItem).filter(CadItem.id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="条目不存在")
        return item
