from __future__ import annotations

from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"


class VersionType(str, Enum):
    MAJOR = "major"
    MINOR = "minor"


class RequirementStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    CASE_DONE = "case_done"
    TESTING = "testing"
    TEST_DONE = "test_done"
    RETEST_PENDING = "retest_pending"
    RETEST_DONE = "retest_done"


class BugSourceType(str, Enum):
    REQUIREMENT = "requirement"
    CASE = "case"
    LEGACY_BUG = "legacy_bug"
    MANUAL = "manual"
    RETEST = "retest"


class FeedbackStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RESOLVED = "resolved"
    CLOSED = "closed"


__all__ = ["UserRole", "VersionType", "RequirementStatus", "BugSourceType", "FeedbackStatus"]
