"""Offline SQLite snapshot/preflight/import. Never starts application background jobs.

Run: python -m app.db.migrate --help
Reports contain table/column names and IDs, never credential or business field values.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum, Integer, JSON, LargeBinary, Numeric, String, select, text

from app.db.model_registry import metadata


def quote(name):
    return '"' + name.replace('"', '""') + '"'


@contextmanager
def readonly(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        yield connection


def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError('Source database does not exist')
    if destination.exists() or source == destination:
        raise ValueError('Snapshot destination must be new')
    # A backup directory must already be a stopped/consistent set of DB+WAL files.
    # Open only disposable copies, so SQLite never modifies the supplied SHM/WAL.
    hashes = {}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='omniqa-snapshot-') as directory:
        for suffix in ('', '-wal', '-shm'):
            original = Path(str(source) + suffix)
            if original.exists():
                hashes[original.name] = hashlib.sha256(original.read_bytes()).hexdigest()
                shutil.copy2(original, Path(directory) / original.name)
        with closing(sqlite3.connect(Path(directory) / source.name)) as src, closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
        for name, digest in hashes.items():
            if hashlib.sha256((source.parent / name).read_bytes()).hexdigest() != digest:
                raise ValueError('Source changed during snapshot; stop its writers and retry')
    return {'source_hashes': hashes, 'snapshot_sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}


def convert(column, value):
    if value is None:
        if not column.nullable:
            raise ValueError('null_in_required_column')
        return None
    kind = column.type
    if isinstance(kind, Boolean):
        if value not in (0, 1, False, True):
            raise ValueError('invalid_boolean')
        return bool(value)
    if isinstance(kind, Enum):
        value = value.name if hasattr(value, 'name') else value
        if value not in kind.enums:
            raise ValueError('invalid_enum')
        return value
    if isinstance(kind, DateTime):
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if result.tzinfo is not None and not kind.timezone:
            raise ValueError('unexpected_timezone')
        return result
    if isinstance(kind, Date):
        return value if isinstance(value, date) else date.fromisoformat(value)
    if isinstance(kind, Integer):
        bits = 63 if isinstance(kind, BigInteger) else 31
        if not isinstance(value, int) or not -(2**bits) <= value < 2**bits:
            raise ValueError('invalid_integer')
        return value
    if isinstance(kind, JSON):
        return json.loads(value) if isinstance(value, str) else value
    if isinstance(kind, String):
        if not isinstance(value, str) or '\x00' in value:
            raise ValueError('invalid_text')
        if kind.length and len(value) > kind.length:
            raise ValueError('text_exceeds_declared_length')
    if isinstance(kind, LargeBinary):
        return bytes(value)
    if isinstance(kind, Numeric):
        return Decimal(str(value))
    return value


def preflight(path):
    model = metadata()
    issues, counts = [], {}
    with readonly(path) as db:
        db.row_factory = sqlite3.Row
        integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
        if integrity != ['ok']:
            issues.append({'kind': 'integrity_check_failed'})
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        for name in sorted(tables - set(model.tables)):
            issues.append({'table': name, 'kind': 'unmapped_table'})
        for fk in db.execute('PRAGMA foreign_key_check'):
            issues.append({'kind': 'foreign_key', 'table': fk[0], 'rowid': fk[1], 'parent': fk[2], 'fk_index': fk[3]})
        for name, table in sorted(model.tables.items()):
            if name not in tables:
                issues.append({'table': name, 'kind': 'missing_table'})
                continue
            columns = {r['name'] for r in db.execute(f'PRAGMA table_info({quote(name)})')}
            for col in columns ^ set(table.c.keys()):
                issues.append({'table': name, 'column': col, 'kind': 'schema_column_mismatch'})
            counts[name] = 0
            for row in db.execute(f'SELECT * FROM {quote(name)}'):
                counts[name] += 1
                for col in table.c:
                    if col.name not in columns:
                        continue
                    try:
                        convert(col, row[col.name])
                    except (ValueError, TypeError, OverflowError):
                        issues.append({'table': name, 'id': row['id'] if 'id' in columns else None,
                                       'column': col.name, 'kind': 'invalid_target_value'})
    return {'tables': counts, 'row_count': sum(counts.values()), 'issues': issues, 'ok': not issues}


def normalized(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'name'):
        return value.name
    return value


def digest_rows(rows, columns):
    digest = hashlib.sha256()
    for row in rows:
        payload = [normalized(convert(col, row[col.name])) for col in columns]
        digest.update(json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()


def import_snapshot(path, url):
    from app.db.engine import build_engine
    from app.db.schema_version import require_schema
    from sqlalchemy.engine import make_url
    if make_url(url).get_backend_name() != 'postgresql':
        raise ValueError('Import target must be PostgreSQL')
    report = preflight(path)
    if not report['ok']:
        raise ValueError('Source preflight failed; resolve the reported issues on a separate copy first')
    engine = build_engine(url)
    model = metadata()
    try:
        require_schema(engine)
        with readonly(path) as src, engine.begin() as dst:
            src.row_factory = sqlite3.Row
            # This is an offline import. Lock all tables to prevent accidental writers.
            dst.execute(text('LOCK TABLE ' + ', '.join(quote(n) for n in sorted(model.tables)) + ' IN ACCESS EXCLUSIVE MODE'))
            for table in model.sorted_tables:
                if dst.execute(select(table).limit(1)).first() is not None:
                    raise ValueError('Target contains business rows; refusing to overwrite')
            # Generated baseline makes FK checks deferrable for import only.
            # Ordinary application transactions still check immediately.
            dst.execute(text('SET CONSTRAINTS ALL DEFERRED'))
            for table in model.sorted_tables:
                cursor = src.execute(f'SELECT * FROM {quote(table.name)} ORDER BY ' + ','.join(quote(c.name) for c in table.primary_key))
                while rows := cursor.fetchmany(500):
                    dst.execute(table.insert(), [{col.name: convert(col, row[col.name]) for col in table.c} for row in rows])
            dst.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            hashes = {}
            for table in model.sorted_tables:
                source_rows = src.execute(f'SELECT * FROM {quote(table.name)} ORDER BY ' + ','.join(quote(c.name) for c in table.primary_key))
                target_rows = dst.execute(select(table).order_by(*table.primary_key)).mappings()
                source_hash = digest_rows(source_rows, table.c)
                target_hash = digest_rows(target_rows, table.c)
                if source_hash != target_hash:
                    raise ValueError(f'Checksum mismatch: {table.name}')
                hashes[table.name] = source_hash
                for column in table.primary_key:
                    sequence = dst.execute(text('SELECT pg_get_serial_sequence(:table, :column)'), {'table': table.name, 'column': column.name}).scalar()
                    if sequence:
                        from sqlalchemy import func
                        maximum = dst.execute(select(func.max(column))).scalar()
                        dst.execute(text('SELECT setval(CAST(:seq AS regclass), :value, :called)'),
                                    {'seq': sequence, 'value': max(1, maximum or 1), 'called': maximum is not None})
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.execute(text('ANALYZE'))
        return {**report, 'table_checksums': hashes, 'imported': True}
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['snapshot', 'preflight', 'import'])
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination')
    parser.add_argument('--report', required=True)
    parser.add_argument('--confirm-empty-target', action='store_true')
    args = parser.parse_args()
    if args.command == 'snapshot':
        if not args.destination:
            parser.error('--destination is required')
        result = snapshot(args.source, args.destination)
    elif args.command == 'preflight':
        result = preflight(args.source)
    else:
        import os
        if not args.confirm_empty_target or not os.environ.get('APP_DATABASE_URL'):
            parser.error('Import requires APP_DATABASE_URL and --confirm-empty-target')
        result = import_snapshot(args.source, os.environ['APP_DATABASE_URL'])
    Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Report written; status:', 'blocked' if result.get('ok') is False else 'ok')
    return 2 if result.get('ok') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
