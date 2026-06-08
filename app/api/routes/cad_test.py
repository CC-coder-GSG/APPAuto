from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.auth import normalize_client_type, session_token_for
from app.core.config import settings
from app.models import User
from app.schemas.cad_test import (
    CadBoardPayload,
    CadColumnPayload,
    CadItemPayload,
    CadRecordPayload,
    CadVersionPayload,
)
from app.services.cad_test_service import CadTestService
from app.services.permission_service import ensure_tab_access, has_tab_access

router = APIRouter(prefix="/api/cad", tags=["cad-test"])

TAB_KEY = "cad-test"


def _service(current_user: User, db: Session) -> CadTestService:
    ensure_tab_access(current_user, TAB_KEY)
    return CadTestService(db)


def _user_from_raw_token(raw_token: str | None, db: Session) -> User | None:
    """从原始 JWT 串解析并校验用户（供 <video>/<img> 用 query token 流式访问）。"""
    if not raw_token:
        return None
    try:
        payload = jwt.decode(raw_token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
    username = payload.get("sub")
    session_token = payload.get("session")
    client_type = normalize_client_type(payload.get("client"))
    if not username:
        return None
    user = db.query(User).filter(User.username == username).first()
    if not user or session_token_for(user, client_type) != session_token:
        return None
    return user


def _stream_file(path: Path, request: Request, media_type: str) -> StreamingResponse | FileResponse:
    """支持 HTTP Range 的文件流式响应，使视频可边播边拖动（206 Partial Content）。"""
    file_size = path.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")
    start, end, status = 0, file_size - 1, 200
    headers = {"accept-ranges": "bytes"}
    if range_header:
        m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
        if m:
            g1, g2 = m.group(1), m.group(2)
            if g1 == "" and g2:  # bytes=-N → 末尾 N 字节
                start = max(0, file_size - int(g2))
            else:
                start = int(g1) if g1 else 0
                end = int(g2) if g2 else file_size - 1
            start = max(0, start)
            end = min(end, file_size - 1)
            if start > end:
                start, end = 0, file_size - 1
            status = 206
            headers["content-range"] = f"bytes {start}-{end}/{file_size}"
    length = end - start + 1
    headers["content-length"] = str(length)

    def iterator(chunk: int = 512 * 1024):
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                data = f.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(iterator(), status_code=status, headers=headers, media_type=media_type)


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
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(path), filename=att.original_name, media_type=att.file_type or "application/octet-stream")


@router.get("/attachments/{attachment_id}/stream")
def stream_attachment(
    attachment_id: int,
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """支持 Range 的流式访问（用于视频在线预览、图片内联）。
    鉴权可走 query ?token=（<video src> 无法设置请求头）或 Authorization 头。"""
    raw = token or ((authorization or "").removeprefix("Bearer ").strip() or None)
    user = _user_from_raw_token(raw, db)
    if not user:
        raise HTTPException(status_code=401, detail="未授权")
    if not has_tab_access(user, TAB_KEY):
        raise HTTPException(status_code=403, detail="无权限")
    att = CadTestService(db).get_attachment(attachment_id)
    path = CadTestService(db).attachment_abs_path(att)
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return _stream_file(path, request, att.file_type or "application/octet-stream")


@router.delete("/attachments/{attachment_id}")
def delete_attachment(attachment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _service(current_user, db).delete_attachment(attachment_id, current_user)
