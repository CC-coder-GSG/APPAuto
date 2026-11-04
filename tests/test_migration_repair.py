from contextlib import closing
import json
import sqlite3

import pytest

from app.db.migration_repair import repair_copy


def make_source(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''
            CREATE TABLE requirements(id INTEGER PRIMARY KEY);
            CREATE TABLE versions(id INTEGER PRIMARY KEY);
            CREATE TABLE requirement_status_history(id INTEGER PRIMARY KEY, requirement_id INTEGER NOT NULL REFERENCES requirements(id), notes TEXT);
            CREATE TABLE bug_tracking(id INTEGER PRIMARY KEY, fixed_minor_version_id INTEGER REFERENCES versions(id), title TEXT);
            CREATE TABLE bug_stage5_records(id INTEGER PRIMARY KEY, minor_version_id INTEGER REFERENCES versions(id));
            INSERT INTO requirement_status_history VALUES(71,999,'first'),(976,999,'second'),(979,999,'third');
            INSERT INTO bug_tracking VALUES(5188,888,'keep bug');
            INSERT INTO bug_stage5_records VALUES(1845,777);
        ''')


def test_approved_repair_archives_rows_and_keeps_bug_records(tmp_path, monkeypatch):
    source = tmp_path / 'source.db'
    make_source(source)
    original = source.read_bytes()
    monkeypatch.setattr('app.db.migration_repair.preflight', lambda path: {'ok': True})
    destination, archive = tmp_path / 'copy.db', tmp_path / 'archive.json'
    result = repair_copy(source, destination, archive)
    assert result['archived_rows'] == 3 and result['cleared_references'] == 2
    assert source.read_bytes() == original
    entries = json.loads(archive.read_text(encoding='utf-8'))['entries']
    assert len([e for e in entries if e.get('archived_row')]) == 3
    assert entries[-1]['original_reference'] == 777
    with closing(sqlite3.connect(destination)) as db:
        assert db.execute('SELECT count(*) FROM requirement_status_history').fetchone() == (0,)
        assert db.execute('SELECT title,fixed_minor_version_id FROM bug_tracking').fetchone() == ('keep bug', None)
        assert db.execute('SELECT id,minor_version_id FROM bug_stage5_records').fetchone() == (1845, None)
        assert list(db.execute('PRAGMA foreign_key_check')) == []


def test_repair_refuses_unapproved_orphans(tmp_path):
    source = tmp_path / 'source.db'
    make_source(source)
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("INSERT INTO requirement_status_history VALUES(999,998,'unapproved')")
    with pytest.raises(ValueError, match='differ from the approved'):
        repair_copy(source, tmp_path / 'copy.db', tmp_path / 'archive.json')
    assert not (tmp_path / 'archive.json').exists()
