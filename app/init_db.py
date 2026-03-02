from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.models import User, UserRole


DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


def init_admin(db: Session) -> None:
    admin_user = db.query(User).filter(User.username == DEFAULT_ADMIN_USERNAME).first()
    if admin_user:
        return

    admin = User(
        username=DEFAULT_ADMIN_USERNAME,
        password_hash=User.hash_password(DEFAULT_ADMIN_PASSWORD),
        role=UserRole.ADMIN,
    )
    db.add(admin)
    db.commit()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        init_admin(db)
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized. Default admin account ensured: admin/admin")
