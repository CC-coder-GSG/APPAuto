from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import settings


@dataclass(frozen=True)
class ColumnFix:
    table: str
    column: str


SAFE_SHIFT_COLUMNS: tuple[ColumnFix, ...] = (
    ColumnFix("software_products", "created_at"),
    ColumnFix("versions", "created_at"),
    ColumnFix("users", "created_at"),
    ColumnFix("requirements", "created_at"),
    ColumnFix("requirements", "updated_at"),
    ColumnFix("requirements", "retested_at"),
    ColumnFix("requirements", "test_notes_updated_at"),
    ColumnFix("requirement_status_history", "changed_at"),
    ColumnFix("test_cases", "created_at"),
    ColumnFix("test_executions", "executed_at"),
    ColumnFix("bug_tracking", "created_at"),
    ColumnFix("bug_tracking", "updated_at"),
    ColumnFix("bug_tracking", "last_zentao_synced_at"),
    ColumnFix("bug_tracking", "last_zentao_checked_at"),
    ColumnFix("bug_stage5_records", "updated_at"),
    ColumnFix("audit_logs", "created_at"),
    ColumnFix("build_records", "created_at"),
    ColumnFix("build_records", "updated_at"),
    ColumnFix("feedback_records", "created_at"),
    ColumnFix("feedback_records", "updated_at"),
    ColumnFix("feedback_records", "handled_at"),
    ColumnFix("feedback_attachments", "created_at"),
    ColumnFix("feedback_bug_links", "created_at"),
    ColumnFix("field_test_records", "created_at"),
    ColumnFix("field_test_records", "updated_at"),
    ColumnFix("field_test_bug_links", "created_at"),
    ColumnFix("browser_sync_events", "received_at"),
    ColumnFix("browser_sync_events", "created_at"),
    ColumnFix("browser_sync_events", "updated_at"),
    ColumnFix("user_zentao_bindings", "created_at"),
    ColumnFix("user_zentao_bindings", "updated_at"),
)

RE_SYNC_COLUMNS: tuple[ColumnFix, ...] = (
    ColumnFix("bug_tracking", "zentao_close_date"),
    ColumnFix("bug_tracking", "zentao_remote_updated_at"),
)

EXCLUDED_COLUMNS: tuple[ColumnFix, ...] = (
    ColumnFix("field_test_records", "start_time"),
    ColumnFix("field_test_records", "end_time"),
    ColumnFix("user_zentao_bindings", "token_expires_at"),
    ColumnFix("user_zentao_bindings", "last_refresh_at"),
)


def resolve_db_path() -> Path:
    db_url = (settings.database_url or "").strip()
    if db_url.startswith("sqlite:///"):
        return Path(db_url.replace("sqlite:///", "", 1)).resolve()
    if db_url.startswith("sqlite://"):
        return Path(db_url.replace("sqlite://", "", 1)).resolve()
    raise SystemExit(f"Unsupported database url: {db_url}")


def list_table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    return {str(row[1]) for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def count_fixable_rows(cur: sqlite3.Cursor, fix: ColumnFix, cutoff_text: str) -> int:
    row = cur.execute(
        f"""
        SELECT COUNT(1)
        FROM {fix.table}
        WHERE {fix.column} IS NOT NULL
          AND datetime({fix.column}) IS NOT NULL
          AND datetime({fix.column}) <= datetime(?)
        """,
        (cutoff_text,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def apply_fix(cur: sqlite3.Cursor, fix: ColumnFix, cutoff_text: str) -> int:
    cur.execute(
        f"""
        UPDATE {fix.table}
        SET {fix.column} = datetime({fix.column}, '+8 hours')
        WHERE {fix.column} IS NOT NULL
          AND datetime({fix.column}) IS NOT NULL
          AND datetime({fix.column}) <= datetime(?)
        """,
        (cutoff_text,),
    )
    return int(cur.rowcount or 0)


def clear_token_cache(cur: sqlite3.Cursor) -> int:
    cur.execute(
        """
        UPDATE user_zentao_bindings
        SET token_value = NULL,
            token_expires_at = NULL
        WHERE token_value IS NOT NULL
           OR token_expires_at IS NOT NULL
        """
    )
    return int(cur.rowcount or 0)


def make_log_file(explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
    else:
        logs_dir = Path(__file__).resolve().parents[1] / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = logs_dir / f"fix_417_time_data_{stamp}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("fix_417_time_data")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix historical +8h time drift for safe local-generated datetime columns.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply the update. Default is dry-run.")
    parser.add_argument(
        "--before",
        default=None,
        help="Only fix rows with datetime <= this cutoff. Format: YYYY-MM-DD HH:MM:SS. Default is current time.",
    )
    parser.add_argument("--log-file", default=None, help="Write execution log to this path.")
    parser.add_argument(
        "--clear-zentao-token-cache",
        action="store_true",
        help="Also clear token_value/token_expires_at after the time fix.",
    )
    args = parser.parse_args()

    cutoff_text = args.before or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_path = resolve_db_path()
    log_file = make_log_file(args.log_file)
    logger = configure_logging(log_file)

    logger.info("mode=%s", "apply" if args.apply else "dry-run")
    logger.info("db=%s", db_path)
    logger.info("cutoff=%s", cutoff_text)
    logger.info("log_file=%s", log_file)

    if not db_path.exists():
        raise SystemExit(f"Database file does not exist: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        existing_tables = {
            str(row[0])
            for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        planned: list[tuple[ColumnFix, int]] = []
        skipped_missing: list[ColumnFix] = []
        total_rows = 0

        for fix in SAFE_SHIFT_COLUMNS:
            if fix.table not in existing_tables:
                skipped_missing.append(fix)
                continue
            table_columns = list_table_columns(cur, fix.table)
            if fix.column not in table_columns:
                skipped_missing.append(fix)
                continue
            count = count_fixable_rows(cur, fix, cutoff_text)
            planned.append((fix, count))
            total_rows += count

        logger.info("safe_shift_total_rows=%s", total_rows)
        for fix, count in planned:
            logger.info("plan shift %s.%s rows=%s", fix.table, fix.column, count)

        if skipped_missing:
            for fix in skipped_missing:
                logger.info("skip missing %s.%s", fix.table, fix.column)

        logger.info("re-sync columns (do not shift directly):")
        for fix in RE_SYNC_COLUMNS:
            logger.info("  %s.%s", fix.table, fix.column)

        logger.info("excluded columns (manual/internal):")
        for fix in EXCLUDED_COLUMNS:
            logger.info("  %s.%s", fix.table, fix.column)

        if not args.apply:
            logger.info("dry-run complete")
            return

        conn.execute("BEGIN")
        applied_total = 0
        for fix, _ in planned:
            changed = apply_fix(cur, fix, cutoff_text)
            applied_total += changed
            logger.info("applied shift %s.%s rows=%s", fix.table, fix.column, changed)

        if args.clear_zentao_token_cache and "user_zentao_bindings" in existing_tables:
            cleared = clear_token_cache(cur)
            logger.info("cleared zentao token cache rows=%s", cleared)

        conn.commit()
        logger.info("apply complete total_rows=%s", applied_total)
    except Exception:
        conn.rollback()
        logger.exception("fix failed, transaction rolled back")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
