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


class TerminalDeviceStatus(str, Enum):
    """终端（安卓真机）当前状态。由设备状态机统一维护。"""

    OFFLINE = "offline"      # adb 不可见 / 掉线
    IDLE = "idle"            # 在线且无人占用，可申请操作
    AUTOMATION = "automation"  # Appium/Jenkins 自动化占用，只读观看
    MANUAL = "manual"        # 某用户持有手动操作权


class TerminalLockType(str, Enum):
    AUTOMATION = "automation"  # Jenkins 自动化测试占用
    MANUAL = "manual"          # 人工远程操作占用


class TerminalLockHolderKind(str, Enum):
    JENKINS = "jenkins"
    USER = "user"


class TerminalLockReleaseReason(str, Enum):
    NORMAL = "normal"        # 正常释放（用户释放 / Jenkins unlock）
    TIMEOUT = "timeout"      # 心跳/TTL 超时自动释放
    PREEMPTED = "preempted"  # 被人工抢占
    FORCED = "forced"        # 管理员强制释放


class TerminalControlMode(str, Enum):
    VIEW = "view"        # 只读观看
    CONTROL = "control"  # 可操作


class QuestionType(str, Enum):
    """学习考核题型。"""

    SINGLE = "single"   # 单选
    MULTI = "multi"     # 多选
    JUDGE = "judge"     # 判断
    BLANK = "blank"     # 填空
    SHORT = "short"     # 简答（人工批改）


class AssessmentStatus(str, Enum):
    """考核状态机：出题中 → 答题中 → 批改中 → 公示。"""

    DRAFTING = "drafting"     # 出题人编辑题目
    ANSWERING = "answering"   # 已发布，其余成员答题
    GRADING = "grading"       # 有简答待出题人批改
    PUBLISHED = "published"   # 已公示成绩（本轮结束）


class SubmissionStatus(str, Enum):
    """单份作答状态。"""

    SUBMITTED = "submitted"   # 已交，客观题已自动判，简答待批改
    GRADED = "graded"         # 全部判分完成


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
    "TerminalDeviceStatus",
    "TerminalLockType",
    "TerminalLockHolderKind",
    "TerminalLockReleaseReason",
    "TerminalControlMode",
    "QuestionType",
    "AssessmentStatus",
    "SubmissionStatus",
]
