from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.schemas.cad_test import (
    CadBoardPayload,
    CadColumnPayload,
    CadItemPayload,
    CadRecordPayload,
    CadVersionPayload,
)
from app.services.cad_test_service import CadTestService
from app.services.permission_service import ensure_tab_access

router = APIRouter(prefix="/api/cad", tags=["cad-test"])

TAB_KEY = "cad-test"


def _service(current_user: User, db: Session) -> CadTestService:
    ensure_tab_access(current_user, TAB_KEY)
    return CadTestService(db)


# ---------------------------------------------------------------------- boards
@router.get("/boards")
def list_boards(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).list_boards()


@router.post("/boards", status_code=201)
def create_board(payload: CadBoardPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).create_board(payload.name, payload.description, current_user)


@router.get("/boards/{board_id}")
def get_board(board_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).get_board_detail(board_id)


@router.put("/boards/{board_id}")
def update_board(board_id: int, payload: CadBoardPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).update_board(board_id, payload.name, payload.description, current_user)


@router.delete("/boards/{board_id}")
def delete_board(board_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).delete_board(board_id, current_user)


# -------------------------------------------------------------------- versions
@router.post("/boards/{board_id}/versions", status_code=201)
def create_version(board_id: int, payload: CadVersionPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).create_version(board_id, payload.name, current_user)


@router.put("/versions/{version_id}")
def update_version(version_id: int, payload: CadVersionPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).update_version(version_id, payload.name, current_user)


@router.delete("/versions/{version_id}")
def delete_version(version_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).delete_version(version_id, current_user)


# --------------------------------------------------------------------- columns
@router.post("/boards/{board_id}/columns", status_code=201)
def create_column(board_id: int, payload: CadColumnPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).create_column(board_id, payload.name, current_user)


@router.put("/columns/{column_id}")
def update_column(column_id: int, payload: CadColumnPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).update_column(column_id, payload.name, current_user)


@router.delete("/columns/{column_id}")
def delete_column(column_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).delete_column(column_id, current_user)


# ----------------------------------------------------------------------- items
@router.post("/boards/{board_id}/items", status_code=201)
def create_item(board_id: int, payload: CadItemPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).create_item(board_id, payload.seq, payload.title, payload.zentao_bug_id, current_user)


@router.put("/items/{item_id}")
def update_item(item_id: int, payload: CadItemPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).update_item(item_id, payload.seq, payload.title, payload.zentao_bug_id, current_user)


@router.delete("/items/{item_id}")
def delete_item(item_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).delete_item(item_id, current_user)


# --------------------------------------------------------------------- records
@router.post("/records")
def upsert_record(payload: CadRecordPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).upsert_record(payload, current_user)


@router.post("/records/attachment")
def upload_attachment(
    item_id: int = Query(...),
    version_id: int = Query(...),
    kind: str = Query(default="screenshot"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _service(current_user, db).save_attachment(item_id, version_id, kind, file, current_user)


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = _service(current_user, db)
    att = service.get_attachment(attachment_id)
    path = service.attachment_abs_path(att)
    if not path.exists():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(path), filename=att.original_name, media_type=att.file_type or "application/octet-stream")


@router.delete("/attachments/{attachment_id}")
def delete_attachment(attachment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).delete_attachment(attachment_id, current_user)
