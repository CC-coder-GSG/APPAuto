from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.seed import (
    ensure_requirement_foreign_key_compat,
    ensure_version_schema_compat,
    repair_software_product_mappings_from_bug_history,
)
from app.models import BugTracking, SoftwareProduct, Version, VersionType
from app.models.enums import BugSourceType


def test_legacy_version_unique_constraint_is_migrated_per_software(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE versions ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "version_no VARCHAR(30) NOT NULL, "
                "version_type VARCHAR(5) NOT NULL, "
                "parent_id INTEGER, "
                "created_at DATETIME NOT NULL, "
                "software_id INTEGER, "
                "final_test_enabled BOOLEAN NOT NULL DEFAULT 0, "
                "final_test_started_at DATETIME, "
                "CONSTRAINT uq_version_no_type UNIQUE (version_no, version_type), "
                "FOREIGN KEY(parent_id) REFERENCES versions(id) ON DELETE CASCADE"
                ")"
            )
        )
        conn.execute(text("CREATE INDEX ix_versions_version_no ON versions (version_no)"))
        conn.execute(
            text(
                "INSERT INTO versions "
                "(id, version_no, version_type, created_at, software_id) "
                "VALUES (1, 'V0.0.0-unclassified', 'MAJOR', CURRENT_TIMESTAMP, 1)"
            )
        )

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        ensure_version_schema_compat(session)
        session.execute(
            text(
                "INSERT INTO versions "
                "(id, version_no, version_type, created_at, software_id) "
                "VALUES (2, 'V0.0.0-unclassified', 'MAJOR', CURRENT_TIMESTAMP, 2)"
            )
        )
        session.commit()
        sql = session.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='versions'")
        ).scalar_one()
        assert "uq_version_software_no_type" in sql
        assert session.execute(text("SELECT COUNT(*) FROM versions")).scalar_one() == 2
    finally:
        session.close()
        engine.dispose()


def test_repair_software_product_mapping_from_single_historical_product(db_session):
    software = SoftwareProduct(
        name="Infinity Studio",
        zentao_product_id=1233,
        zentao_product_name_cache="Infinity Studio",
    )
    db_session.add(software)
    db_session.flush()
    major = Version(
        version_no="V0.0.4",
        version_type=VersionType.MAJOR,
        software_id=software.id,
    )
    db_session.add(major)
    db_session.flush()
    bug = BugTracking(
        major_version_id=major.id,
        source_type=BugSourceType.MANUAL,
        bug_id="b#32171",
        resolution="unresolved",
        zentao_bug_id="32171",
        zentao_product_id="311",
        zentao_product_name="Infinity Studio",
    )
    db_session.add(bug)
    db_session.commit()

    assert repair_software_product_mappings_from_bug_history(db_session) == 1
    db_session.refresh(software)
    assert software.zentao_product_id == 311
    assert software.zentao_product_name_cache == "Infinity Studio"


def test_dangling_requirements_old_foreign_key_is_repaired(tmp_path):
    db_path = tmp_path / "dangling-fk.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE requirements (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO requirements (id) VALUES (1)"))
        conn.execute(
            text(
                "CREATE TABLE child_rows ("
                "id INTEGER PRIMARY KEY, requirement_id INTEGER, "
                "FOREIGN KEY(requirement_id) REFERENCES requirements_old(id)"
                ")"
            )
        )
        conn.execute(text("CREATE INDEX ix_child_req ON child_rows(requirement_id)"))

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        assert ensure_requirement_foreign_key_compat(session) == 1
        session.execute(text("PRAGMA foreign_keys=ON"))
        session.execute(
            text("INSERT INTO child_rows (id, requirement_id) VALUES (1, 1)")
        )
        session.commit()
        fk_target = session.execute(
            text("PRAGMA foreign_key_list(child_rows)")
        ).fetchone()[2]
        assert fk_target == "requirements"
    finally:
        session.close()
        engine.dispose()
