from __future__ import annotations

from app.models.audit import AuditLog
from app.models.browser_sync_event import BrowserSyncEvent
from app.models.build_record import BuildRecord
from app.models.bug import BugTracking
from app.models.cad_test import CadAttachment, CadBoard, CadCustomColumn, CadItem, CadItemFile, CadRecord, CadVersion
from app.models.enums import (
    BugSourceType,
    FeedbackStatus,
    RequirementStatus,
    TaskBoardPriority,
    TaskBoardStatus,
    TaskBoardTargetType,
    TestResultStatus,
    UserRole,
    VersionType,
)
from app.models.execution import TestExecution
from app.models.feedback import FeedbackAttachment, FeedbackBugLink, FeedbackRecord
from app.models.field_test import FieldTestBugLink, FieldTestPurposeType, FieldTestRecord, FieldTestResultStatus
from app.models.requirement import Requirement
from app.models.requirement_retest_record import RequirementRetestRecord
from app.models.requirement_status_history import RequirementStatusHistory
from app.models.software import SoftwareProduct
from app.models.stage5 import BugStage5Record
from app.models.story_ai_result import StoryAIResult
from app.models.sync_lock import SyncLock
from app.models.task_board import TaskBoardTask, TaskBoardUpdate
from app.models.testcase import TestCase
from app.models.user import User
from app.models.user_ai_provider_config import UserAIProviderConfig
from app.models.user_jenkins_binding import UserJenkinsBinding
from app.models.user_zentao_binding import UserZentaoBinding
from app.models.version import Version
from app.models.zentao_testcase_mirror import ZentaoTestCaseMirror

__all__ = [
    "AuditLog",
    "BrowserSyncEvent",
    "BuildRecord",
    "BugSourceType",
    "BugStage5Record",
    "BugTracking",
    "CadAttachment",
    "CadBoard",
    "CadCustomColumn",
    "CadItem",
    "CadItemFile",
    "CadRecord",
    "CadVersion",
    "FeedbackStatus",
    "FeedbackAttachment",
    "FeedbackBugLink",
    "FeedbackRecord",
    "FieldTestBugLink",
    "FieldTestPurposeType",
    "FieldTestRecord",
    "FieldTestResultStatus",
    "Requirement",
    "RequirementRetestRecord",
    "RequirementStatusHistory",
    "RequirementStatus",
    "SoftwareProduct",
    "StoryAIResult",
    "SyncLock",
    "TaskBoardPriority",
    "TaskBoardStatus",
    "TaskBoardTargetType",
    "TaskBoardTask",
    "TaskBoardUpdate",
    "TestCase",
    "TestExecution",
    "TestResultStatus",
    "User",
    "UserAIProviderConfig",
    "UserJenkinsBinding",
    "UserRole",
    "UserZentaoBinding",
    "Version",
    "VersionType",
    "ZentaoTestCaseMirror",
]
