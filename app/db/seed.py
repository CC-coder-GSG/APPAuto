from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.config import settings
from app.core.security import hash_password
from app.models import SoftwareProduct, User, UserRole, Version, VersionType

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"
DEFAULT_SOFTWARE_NAME = "Survey Master"


def ensure_default_admin(db: Session) -> None:
    admin_user = db.query(User).filter(User.username == DEFAULT_ADMIN_USERNAME).first()
    if admin_user or not settings.allow_default_admin_seed:
        return

    db.add(
        User(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
            role=UserRole.ADMIN,
        )
    )
    db.commit()


def ensure_software_schema_compat(db: Session) -> None:
    # 兼容历史 SQLite：为 versions 增加 software_id 列，避免要求手工迁移
    rows = db.execute(text("PRAGMA table_info(versions)")).fetchall()
    cols = {r[1] for r in rows}
    if "software_id" not in cols:
        db.execute(text("ALTER TABLE versions ADD COLUMN software_id INTEGER"))
        db.commit()


def ensure_user_schema_compat(db: Session) -> None:
    rows = db.execute(text("PRAGMA table_info(users)")).fetchall()
    cols = {r[1] for r in rows}
    if "display_name" not in cols:
        db.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(80)"))
        db.commit()
    # 历史用户默认显示名回填为账号名
    db.execute(text("UPDATE users SET display_name = username WHERE display_name IS NULL OR TRIM(display_name) = ''"))
    db.commit()


def ensure_default_software_and_backfill(db: Session) -> None:
    software = db.query(SoftwareProduct).filter(SoftwareProduct.name == DEFAULT_SOFTWARE_NAME).first()
    if not software:
        software = SoftwareProduct(name=DEFAULT_SOFTWARE_NAME)
        db.add(software)
        db.commit()
        db.refresh(software)

    # 历史大版本默认归档到 Survey Master
    db.execute(
        text(
            "UPDATE versions SET software_id = :sid "
            "WHERE version_type = :major AND (software_id IS NULL OR software_id = 0)"
        ),
        {"sid": software.id, "major": VersionType.MAJOR.value},
    )
    # 子版本继承父大版本的软件归属
    db.execute(
        text(
            "UPDATE versions SET software_id = ("
            "  SELECT p.software_id FROM versions p WHERE p.id = versions.parent_id"
            ") "
            "WHERE version_type = :minor AND (software_id IS NULL OR software_id = 0)"
        ),
        {"minor": VersionType.MINOR.value},
    )
    db.commit()
