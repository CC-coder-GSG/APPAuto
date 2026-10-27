from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path

from sqlalchemy import update

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.database_compare import DatabaseCompareTask
from app.utils.time_utils import local_now

from .engine import compare_databases
from .schema import fingerprint
from .service import owned_path, storage_root
from .values import dumps

logger = logging.getLogger(__name__)


@contextmanager
def worker_lock():
    """One executor across web workers and manually launched worker processes."""
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True)
    stream = (root / "worker.lock").open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                pass
        yield locked
    finally:
        # Closing releases the OS lock, including after process termination.
        stream.close()


def recover_interrupted(session_factory=SessionLocal):
    with session_factory() as db:
        db.execute(update(DatabaseCompareTask).where(DatabaseCompareTask.status == "running").values(
            status="interrupted", error="工作进程中断，已有结果仅供参考；请创建新任务重新执行", finished_at=local_now()))
        db.commit()


def run_next(session_factory=SessionLocal, stopping=lambda: False) -> bool:
    with session_factory() as db:
        task = db.query(DatabaseCompareTask).filter_by(status="queued").order_by(DatabaseCompareTask.created_at).first()
        if task is None:
            return False
        identity = task.id
        claimed = db.execute(update(DatabaseCompareTask).where(
            DatabaseCompareTask.id == identity, DatabaseCompareTask.status == "queued",
            DatabaseCompareTask.cancel_requested.is_(False)).values(status="running", started_at=local_now()))
        db.commit()
        if not claimed.rowcount:
            return True
        db.refresh(task)
        pair_config = json.loads(task.pairs_json)
        rules = json.loads(task.rules_json)
        input_ids = (task.old_input_id, task.new_input_id)
        old_label, new_label = task.old_label, task.new_label
    directory = owned_path("tasks", identity)
    started = time.monotonic()
    timed_out = False

    def cancelled():
        nonlocal timed_out
        timed_out = time.monotonic() - started > settings.database_compare_timeout_seconds
        return stopping() or timed_out or (directory / "cancel").exists()

    def progress(data):
        with session_factory() as db:
            db.execute(update(DatabaseCompareTask).where(DatabaseCompareTask.id == identity).values(progress_json=dumps(data)))
            db.commit()

    try:
        from app.models.database_compare import DatabaseCompareInput
        with session_factory() as db:
            for input_id in set(input_ids):
                source = db.get(DatabaseCompareInput, input_id)
                if source is None:
                    raise ValueError("输入组已不存在")
                for entry in json.loads(source.manifest_json):
                    expected = entry["file"]
                    if fingerprint(owned_path("inputs", input_id) / expected["name"]) != expected:
                        raise ValueError("已上传的输入文件发生变化，请重新上传")
        pairs = [{"name": p["name"], **{
            side: owned_path("inputs", input_ids[i]) / p[side] if p.get(side) else None
            for i, side in enumerate(("old", "new"))}} for p in pair_config]
        summary = compare_databases(pairs, directory / "result.sqlite", rules, progress, cancelled)
        if timed_out:
            summary.update(status="failed", conclusion="inconclusive")
            summary["errors"].append({"error": f"超过执行时限 {settings.database_compare_timeout_seconds} 秒"})
        elif stopping() and summary["status"] == "cancelled":
            summary.update(status="interrupted", conclusion="inconclusive")
        summary.update(task_id=identity, old_label=old_label, new_label=new_label)
        # The connection context commits but does not close SQLite handles. Close
        # before publishing the terminal state so Windows can delete the result.
        with closing(sqlite3.connect(directory / "result.sqlite")) as result_db, result_db:
            result_db.execute("INSERT OR REPLACE INTO metadata VALUES('summary',?)", (dumps(summary),))
        with session_factory() as db:
            db.execute(update(DatabaseCompareTask).where(DatabaseCompareTask.id == identity).values(
                status=summary["status"], summary_json=dumps(summary),
                progress_json=dumps({key: summary[key] for key in ("status", "tables_completed", "tables_total")}),
                error="; ".join(e["error"] for e in summary["errors"]) or None, finished_at=local_now()))
            db.commit()
    except Exception as exc:
        logger.exception("database comparison failed: %s", identity)
        with session_factory() as db:
            db.execute(update(DatabaseCompareTask).where(DatabaseCompareTask.id == identity).values(
                status="failed", error=str(exc), finished_at=local_now()))
            db.commit()
    return True


def start_managed_worker():
    if not settings.database_compare_worker_enabled:
        return None
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True)
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    # Python worker has no visible window; logs go to a separate, inspectable file.
    with (root / "worker.log").open("ab") as log:
        return subprocess.Popen([sys.executable, "-m", "scripts.database_compare_worker"],
                                cwd=str(Path(__file__).resolve().parents[3]),
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
