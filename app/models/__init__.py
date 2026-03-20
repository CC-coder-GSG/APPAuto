from __future__ import annotations

from app.models.audit import AuditLog
from app.models.browser_sync_event import BrowserSyncEvent
from app.models.build_record import BuildRecord
from app.models.bug import BugTracking
from app.models.enums import BugSourceType, FeedbackStatus, RequirementStatus, TestResultStatus, UserRole, VersionType
from app.models.execution import TestExecution
from app.models.feedback import FeedbackAttachment, FeedbackBugLink, FeedbackRecord
from app.models.field_test import FieldTestBugLink, FieldTestPurposeType, FieldTestRecord, FieldTestResultStatus
from app.models.requirement import Requirement
from app.models.requirement_status_history import RequirementStatusHistory
from app.models.software import SoftwareProduct
from app.models.stage5 import BugStage5Record
from app.models.testcase import TestCase
from app.models.user import User
from app.models.version import Version

__all__ = [
    "AuditLog",
    "BrowserSyncEvent",
    "BuildRecord",
    "BugSourceType",
    "BugStage5Record",
    "BugTracking",
    "FeedbackStatus",
    "FeedbackAttachment",
    "FeedbackBugLink",
    "FeedbackRecord",
    "FieldTestBugLink",
    "FieldTestPurposeType",
    "FieldTestRecord",
    "FieldTestResultStatus",
    "Requirement",
    "RequirementStatusHistory",
    "RequirementStatus",
    "SoftwareProduct",
    "TestCase",
    "TestExecution",
    "TestResultStatus",
    "User",
    "UserRole",
    "Version",
    "VersionType",
]
