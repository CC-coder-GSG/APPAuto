from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.database_compare import DatabaseCompareInput, DatabaseCompareTask
from app.services.permission_service import ensure_tab_access
from app.utils.time_utils import local_now

from .engine import validate_rules
from .schema import inspect_file, open_readonly, quote
from .values import dumps

TERMINAL = {"succeeded", "partial", "failed", "cancelled", "interrupted"}


def storage_root() -> Path:
    root = Path(settings.database_compare_root)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[3] / root
    return root.resolve()


def owned_path(kind: str, identity: str) -> Path:
    if kind not in ("inputs", "tasks") or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise HTTPException(400, "标识无效")
    root = storage_root()
    target = root / kind / identity
    path = target.resolve()
    if not path.is_relative_to(root) or path != target:
        raise HTTPException(400, "存储路径无效")
    return path


def authorize(user):
    ensure_tab_access(user, "database-compare")


def is_admin(user):
    return getattr(user.role, "value", user.role) == "admin"


def get_input(db: Session, identity: str, user) -> DatabaseCompareInput:
    authorize(user)
    owned_path("inputs", identity)
    item = db.get(DatabaseCompareInput, identity)
    if not item or (item.owner_id != user.id and not is_admin(user)):
        raise HTTPException(404, "输入组不存在或无权查看")
    return item


def get_task(db: Session, identity: str, user) -> DatabaseCompareTask:
    authorize(user)
    owned_path("tasks", identity)
    task = db.get(DatabaseCompareTask, identity)
    if not task or (task.owner_id != user.id and not is_admin(user)):
        raise HTTPException(404, "比较任务不存在或无权查看")
    return task


def serialize_task(task) -> dict:
    return {"id": task.id, "owner_id": task.owner_id, "old_label": task.old_label, "new_label": task.new_label,
            "old_input_id": task.old_input_id, "new_input_id": task.new_input_id,
            "pairs": json.loads(task.pairs_json), "rules": json.loads(task.rules_json),
            "status": task.status, "cancel_requested": task.cancel_requested,
            "progress": json.loads(task.progress_json), "summary": json.loads(task.summary_json),
            "error": task.error, "created_at": task.created_at, "started_at": task.started_at,
            "finished_at": task.finished_at}


def save_inputs(db: Session, files, user) -> dict:
    authorize(user)
    if not 1 <= len(files) <= 16:
        raise HTTPException(400, "每组需选择 1～16 个 SQLite 文件")
    identity = uuid.uuid4().hex
    directory = owned_path("inputs", identity)
    directory.mkdir(parents=True)
    total, names, manifest = 0, set(), []
    try:
        for upload in files:
            name = upload.filename or ""
            if (not name or len(name) > 180 or name != name.strip() or name.endswith(".") or
                    re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or
                    re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I) or
                    Path(name).suffix.lower() not in (".db", ".sqlite", ".sqlite3")):
                raise ValueError("请选择名称有效的 .db、.sqlite 或 .sqlite3 文件")
            if name.casefold() in names:
                raise ValueError("同一输入组中的文件名不能重复（不区分大小写）")
            names.add(name.casefold())
            path, size = directory / name, 0
            with path.open("xb") as target:
                for chunk in iter(lambda: upload.file.read(1024 * 1024), b""):
                    size += len(chunk)
                    total += len(chunk)
                    if size > settings.database_compare_max_file_mb * 1024 * 1024:
                        raise ValueError(f"单文件不能超过 {settings.database_compare_max_file_mb} MiB")
                    if total > settings.database_compare_max_group_mb * 1024 * 1024:
                        raise ValueError(f"每组文件不能超过 {settings.database_compare_max_group_mb} MiB")
                    target.write(chunk)
            manifest.append(inspect_file(path))
        item = DatabaseCompareInput(id=identity, owner_id=user.id, manifest_json=dumps(manifest))
        db.add(item)
        db.commit()
        return {"id": identity, "files": manifest}
    except Exception as exc:
        db.rollback()
        # directory is an exclusively-created UUID child validated against the configured root.
        shutil.rmtree(directory)
        if isinstance(exc, (ValueError, OSError)) or exc.__class__.__module__ == "sqlite3":
            raise HTTPException(400, str(exc)) from exc
        raise


def paired_files(old_files: list, new_files: list) -> list[dict]:
    a, b = ({x["file"]["name"].casefold(): x["file"]["name"] for x in group} for group in (old_files, new_files))
    if len(a) == len(b) == 1:
        return [{"name": next(iter(b.values())), "old": next(iter(a.values())), "new": next(iter(b.values()))}]
    return [{"name": b.get(k) or a[k], "old": a.get(k), "new": b.get(k)} for k in sorted(set(a) | set(b))]


def validate_pairs(pairs: list[dict], old_files: list, new_files: list) -> list[dict]:
    if not pairs:
        pairs = paired_files(old_files, new_files)
    if len(pairs) > 32:
        raise HTTPException(400, "数据库对应关系过多")
    available = [{f["file"]["name"] for f in group} for group in (old_files, new_files)]
    used, pair_names = [set(), set()], set()
    for pair in pairs:
        if set(pair) - {"name", "old", "new"} or any(pair.get(s) is not None and not isinstance(pair.get(s), str) for s in ("old", "new")):
            raise HTTPException(400, "对应关系只支持 name、old、new，文件名必须为文本或空")
        name = pair.get("name", "")
        if not isinstance(name, str) or not name or len(name) > 180 or name.casefold() in pair_names:
            raise HTTPException(400, "每组对应关系需要不重复的名称")
        pair_names.add(name.casefold())
        if not pair.get("old") and not pair.get("new"):
            raise HTTPException(400, "对应关系不能两侧均为空")
        for i, side in enumerate(("old", "new")):
            value = pair.get(side)
            if value:
                if value not in available[i] or value in used[i]:
                    raise HTTPException(400, "文件不存在或被重复使用")
                used[i].add(value)
    if used != available:
        raise HTTPException(400, "对应关系必须覆盖两侧全部文件；单侧文件请保留为新增或删除")
    return pairs


def validate_key(connection, table, key):
    missing = set(key) - {c["name"] for c in table["columns"] if c["hidden"] != 1}
    if missing:
        raise ValueError("匹配键字段不存在: " + ", ".join(sorted(missing)))
    name = quote(table["name"])
    if connection.execute(f"SELECT 1 FROM {name} WHERE " + " OR ".join(quote(k) + " IS NULL" for k in key) + " LIMIT 1").fetchone():
        raise ValueError("匹配键包含 NULL")
    grouping = ",".join(f"typeof({quote(k)}),{quote(k)} COLLATE BINARY" for k in key)
    if connection.execute(f"SELECT 1 FROM {name} GROUP BY {grouping} HAVING COUNT(*)>1 LIMIT 1").fetchone():
        raise ValueError("匹配键存在重复值")


def preview(db, old_id, new_id, user, pairs=None, rules=None):
    a, b = (json.loads(get_input(db, x, user).manifest_json) for x in (old_id, new_id))
    pairs = validate_pairs(pairs or [], a, b)
    rules = validate_rules(rules)
    files = [{x["file"]["name"]: x for x in group} for group in (a, b)]
    tables = []
    known = set()
    for pair in pairs:
        sa, sb = [files[i].get(pair.get(side), {}).get("tables", {}) for i, side in enumerate(("old", "new"))]
        for name in sorted(set(sa) | set(sb)):
            table_a, table_b = sa.get(name), sb.get(name)
            full_name = f"{pair['name']}.{name}"
            known.add(full_name)
            configured = rules["tables"].get(full_name, {})
            pks = [[c["name"] for c in sorted(t["columns"], key=lambda c: c["pk_order"]) if c["pk_order"]]
                   if t else [] for t in (table_a, table_b)]
            key = configured.get("key", []) or (pks[0] if pks[0] == pks[1] else [])
            if key and table_a and table_b:
                declarations = [{c["name"]: c["type"] for c in t["columns"]} for t in (table_a, table_b)]
                if not configured.get("key") and any(declarations[0].get(k) != declarations[1].get(k) for k in key):
                    key = []
            if configured.get("match_mode") == "row_multiset":
                key = []
            validation = "整行与次数比较，不推断修改关系"
            if key:
                try:
                    for i, side in enumerate(("old", "new")):
                        table = (table_a, table_b)[i]
                        if table is None:
                            continue
                        conn = open_readonly(owned_path("inputs", (old_id, new_id)[i]) / pair[side])
                        try:
                            validate_key(conn, {**table, "name": name}, key)
                        finally:
                            conn.close()
                    validation = "当前数据唯一且非空；执行时再次验证。请确认该键跨版本含义稳定"
                except ValueError as exc:
                    if configured.get("key"):
                        raise ValueError(f"{full_name}: {exc}") from exc
                    key = []
                    validation = f"声明主键不可靠（{exc}），使用整行及次数比较"
            tables.append({"database": pair["name"], "table": name, "internal": (table_a or table_b)["internal"],
                           "old_rows": table_a.get("rows") if table_a else None,
                           "new_rows": table_b.get("rows") if table_b else None,
                           "match_mode": "key" if key else "row_multiset", "key": key,
                           "validation": validation})
    if set(rules["tables"]) - known:
        raise ValueError("规则引用不存在的表")
    return {"pairs": pairs, "tables": tables, "rules": rules,
            "notice": "请上传停止写入后导出的完整快照；单独上传 .db 无法检测尚未提交到文件的 WAL 数据。"}


def create_task(db, payload, user):
    checked = preview(db, payload.old_input_id, payload.new_input_id, user, payload.pairs, payload.rules)
    task = DatabaseCompareTask(id=uuid.uuid4().hex, owner_id=user.id, old_input_id=payload.old_input_id,
                               new_input_id=payload.new_input_id, old_label=payload.old_label, new_label=payload.new_label,
                               pairs_json=dumps(checked["pairs"]), rules_json=dumps(checked["rules"]),
                               status="queued", cancel_requested=False, progress_json="{}", summary_json="{}")
    directory = owned_path("tasks", task.id)
    directory.mkdir(parents=True)
    db.add(task)
    db.commit()
    db.refresh(task)
    return serialize_task(task)


def cancel_task(db, task):
    if task.status not in TERMINAL:
        task.cancel_requested = True
        owned_path("tasks", task.id).joinpath("cancel").touch()
        if task.status == "queued":
            task.status = "cancelled"
            task.finished_at = local_now()
        db.commit()
        db.refresh(task)
    return serialize_task(task)


def cleanup_tasks(db, user, task_ids=None):
    """Remove finished tasks and release snapshots only after their last reference."""
    authorize(user)
    query = db.query(DatabaseCompareTask)
    if not is_admin(user):
        query = query.filter_by(owner_id=user.id)
    if task_ids is None:
        tasks = query.filter(DatabaseCompareTask.status.in_(TERMINAL)).all()
    else:
        # Validate the entire selection before touching any files.
        tasks = [get_task(db, identity, user) for identity in dict.fromkeys(task_ids)]
        if any(task.status not in TERMINAL for task in tasks):
            raise HTTPException(409, "排队中或比较中的任务不能删除，请先取消并等待任务结束")

    identities = [task.id for task in tasks]
    input_ids = {identity for task in tasks for identity in (task.old_input_id, task.new_input_id)}
    remaining = db.query(DatabaseCompareTask).filter(DatabaseCompareTask.id.notin_(identities))
    unused_inputs = [identity for identity in sorted(input_ids) if not remaining.filter(or_(
        DatabaseCompareTask.old_input_id == identity, DatabaseCompareTask.new_input_id == identity)).first()]
    directories = [owned_path("tasks", identity) for identity in identities]
    directories += [owned_path("inputs", identity) for identity in unused_inputs]
    # owned_path checks every resolved absolute target before recursive removal,
    # including redirects to another UUID directory inside storage.
    try:
        for directory in directories:
            if directory.exists():
                shutil.rmtree(directory)
        for task in tasks:
            db.delete(task)
        for identity in unused_inputs:
            item = db.get(DatabaseCompareInput, identity)
            if item is not None:
                db.delete(item)
        db.commit()
    except OSError as exc:
        db.rollback()
        raise HTTPException(409, "文件清理失败，请确认报告未被占用后重试") from exc
    except Exception:
        db.rollback()
        raise
    return {"deleted_count": len(identities), "deleted_task_ids": identities, "deleted_input_ids": unused_inputs}
