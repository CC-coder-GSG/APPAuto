from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.base import Base


class BugStage5Record(Base):
    __tablename__ = "bug_stage5_records"

    id = Column(Integer, primary_key=True, index=True)
    bug_tracking_id = Column(Integer, ForeignKey("bug_tracking.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    minor_version_id = Column(Integer, ForeignKey("versions.id"))
    test_done = Column(Boolean, default=False)
    newly_found_bug_id = Column(String, nullable=True)
    resolution = Column(String, default="fixed")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    bug = relationship("BugTracking", back_populates="stage5_records")
    user = relationship("User")
    minor_version = relationship("Version")


__all__ = ["BugStage5Record"]
