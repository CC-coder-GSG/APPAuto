"""Approved repair of the five orphan references in the 2026-11-04 backup.

Always creates a new copy. Refuses a different set of broken references.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from app.db.migrate import snapshot, quote, preflight

APPROVED = {
    ('requirement_status_history', 71, 'requirement_id'): 'archive',
    ('requirement_status_history', 976, 'requirement_id'): 'archive',
    ('requirement_status_history', 979, 'requirement_id'): 'archive',
    ('bug_tracking', 5188, 'fixed_minor_version_id'): 'clear_reference',
    ('bug_stage5_records', 1845, 'minor_version_id'): 'clear_reference',
}


def repair_copy(source, destination, archive):
    archive = Path(archive)
    if archive.exists():
        raise ValueError('Archive must be a new file')
    provenance = snapshot(source, destination)
    with closing(sqlite3.connect(destination)) as db:
        db.row_factory = sqlite3.Row
        broken = set()
        for row in db.execute('PRAGMA foreign_key_check'):
            fk = next(f for f in db.execute(f'PRAGMA foreign_key_list({quote(row[0])})') if f['id'] == row[3])
            broken.add((row[0], row[1], fk['from']))
        if broken != set(APPROVED):
            raise ValueError('Orphan references differ from the approved five; copy was not repaired')
        entries = []
        for (table, row_id, column), action in APPROVED.items():
            row = db.execute(f'SELECT * FROM {quote(table)} WHERE rowid=?', (row_id,)).fetchone()
            entry = {'table': table, 'id': row_id, 'column': column, 'action': action,
                     'original_reference': row[column]}
            if action == 'archive':
                entry['archived_row'] = dict(row)
            entries.append(entry)
        archive.parent.mkdir(parents=True, exist_ok=True)
        # Persist the original values before changing the disposable copy.
        with archive.open('x', encoding='utf-8') as file:
            json.dump({'provenance': provenance, 'entries': entries}, file, ensure_ascii=False, indent=2)
        with db:
            for entry in entries:
                table, row_id = entry['table'], entry['id']
                if entry['action'] == 'archive':
                    db.execute(f'DELETE FROM {quote(table)} WHERE rowid=?', (row_id,))
                else:
                    db.execute(f'UPDATE {quote(table)} SET {quote(entry["column"])}=NULL WHERE rowid=?', (row_id,))
            if list(db.execute('PRAGMA foreign_key_check')):
                raise ValueError('Repair did not resolve all foreign key issues')
    result = preflight(destination)
    return {**result, 'archived_rows': 3, 'cleared_references': 2, 'archive': str(archive)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('source', 'destination', 'archive', 'report'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args()
    result = repair_copy(args.source, args.destination, args.archive)
    Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Repair copy ready; preflight:', 'ok' if result['ok'] else 'blocked')
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
