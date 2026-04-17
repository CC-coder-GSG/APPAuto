from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import settings


def resolve_db_path() -> Path:
    db_url = (settings.database_url or "").strip()
    if db_url.startswith("sqlite:///"):
        return Path(db_url.replace("sqlite:///", "", 1)).resolve()
    if db_url.startswith("sqlite://"):
        return Path(db_url.replace("sqlite://", "", 1)).resolve()
    raise SystemExit(f"Unsupported database url: {db_url}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check which Stage5 bugs actually have 'other users closed-loop records'.",
    )
    parser.add_argument("--major-version-id", type=int, default=None, help="Only inspect one major version")
    args = parser.parse_args()

    db_path = resolve_db_path()
    print(f"[check_stage5_other_records] db={db_path}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    try:
        params: list[object] = []
        where_sql = ""
        if args.major_version_id is not None:
            where_sql = "WHERE b.major_version_id = ?"
            params.append(args.major_version_id)

        rows = cur.execute(
            f"""
            SELECT
              b.major_version_id,
              b.id,
              b.bug_id,
              b.zentao_bug_id,
              COUNT(DISTINCT r.user_id) AS user_count
            FROM bug_tracking b
            JOIN bug_stage5_records r ON r.bug_tracking_id = b.id
            {where_sql}
            GROUP BY b.major_version_id, b.id, b.bug_id, b.zentao_bug_id
            HAVING COUNT(DISTINCT r.user_id) >= 2
            ORDER BY b.major_version_id, b.id
            """,
            params,
        ).fetchall()

        if not rows:
            print("No bugs with multi-user Stage5 records were found.")
            return

        current_major = None
        for major_version_id, bug_track_id, bug_id, zentao_bug_id, user_count in rows:
            if major_version_id != current_major:
                current_major = major_version_id
                print(f"\n[major_version_id={major_version_id}]")
            print(
                f"  bug_tracking_id={bug_track_id} bug_id={bug_id} "
                f"zentao_bug_id={zentao_bug_id or '-'} user_count={user_count}"
            )
            detail_rows = cur.execute(
                """
                SELECT
                  r.user_id,
                  COALESCE(NULLIF(TRIM(u.display_name), ''), u.username) AS shown_name,
                  r.test_done,
                  r.resolution,
                  COALESCE(v.version_no, '未知') AS minor_version_no,
                  COALESCE(r.source, 'manual') AS source
                FROM bug_stage5_records r
                LEFT JOIN users u ON u.id = r.user_id
                LEFT JOIN versions v ON v.id = r.minor_version_id
                WHERE r.bug_tracking_id = ?
                ORDER BY r.user_id
                """,
                (bug_track_id,),
            ).fetchall()
            for user_id, shown_name, test_done, resolution, minor_version_no, source in detail_rows:
                print(
                    f"    - user_id={user_id} shown_name={shown_name or '-'} "
                    f"test_done={bool(test_done)} resolution={resolution or '-'} "
                    f"minor_version={minor_version_no} source={source}"
                )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
