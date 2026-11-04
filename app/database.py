from sqlalchemy import event
import logging
import time
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings
from app.core.retest_aggregate_events import AppSession
from app.db.engine import build_engine

DATABASE_URL = settings.database_url
logger = logging.getLogger(__name__)

engine = build_engine(
    DATABASE_URL,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout,
)


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
