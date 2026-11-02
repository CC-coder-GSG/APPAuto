"""Preview or apply historical retest aggregate repair without calling live APIs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.services.retest_aggregate_service import RetestAggregateService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Persist repairs; otherwise preview on an in-memory copy')
    args = parser.parse_args()
    source_path = args.database.resolve(strict=True)
    if args.apply:
        engine = create_engine('sqlite:///' + source_path.as_posix())
    else:
        source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
        copy = sqlite3.connect(':memory:', check_same_thread=False)
        try:
            source.backup(copy)
        finally:
            source.close()
        engine = create_engine('sqlite://', creator=lambda: copy, poolclass=StaticPool)
    try:
        with Session(engine, autoflush=False) as db:
            result = RetestAggregateService(db).reconcile_all(commit_batches=True)
        print(json.dumps({'mode': 'apply' if args.apply else 'preview', **result}, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
