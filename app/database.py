from sqlalchemy import create_engine, event
import logging
import time
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings
from app.core.retest_aggregate_events import AppSession

DATABASE_URL = settings.database_url
logger = logging.getLogger(__name__)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def set_sqlite_pragmas(dbapi_conn, _):
    """开启 WAL 模式：读写互不阻塞，并发性能更好；synchronous=NORMAL 在性能和安全间取得平衡。"""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
SessionLocal = sessionmaker(class_=AppSession, autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@event.listens_for(engine, "checkout")
def track_checkout(dbapi_connection, connection_record, connection_proxy):
    connection_record.info['checkout_started'] = time.monotonic()


@event.listens_for(engine, "checkin")
def track_checkin(dbapi_connection, connection_record):
    started = connection_record.info.pop('checkout_started', None)
    if started is not None and time.monotonic() - started >= 5:
        logger.warning('db connection held %.2fs; pool=%s', time.monotonic() - started, engine.pool.status())
