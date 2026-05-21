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
    FIELD_TEST = "field_test"


class FeedbackStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TestResultStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    PARTIAL = "partial"
    UNTESTED = "untested"


class TaskBoardStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    DEFERRED = "deferred"


class TaskBoardPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskBoardTargetType(str, Enum):
    REQUIREMENT = "requirement"
    BUG = "bug"
    FEEDBACK = "feedback"
    FIELD_TEST = "field_test"
    BUILD_RECORD = "build_record"
    MANUAL = "manual"


__all__ = [
    "UserRole",
    "VersionType",
    "RequirementStatus",
    "BugSourceType",
    "FeedbackStatus",
    "TestResultStatus",
    "TaskBoardStatus",
    "TaskBoardPriority",
    "TaskBoardTargetType",
]
