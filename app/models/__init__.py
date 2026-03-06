from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_legacy_models():
    legacy_path = Path(__file__).resolve().parent.parent / "models.py"
    spec = importlib.util.spec_from_file_location("app._legacy_models", legacy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load legacy models from {legacy_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_legacy = _load_legacy_models()

UserRole = _legacy.UserRole
VersionType = _legacy.VersionType
RequirementStatus = _legacy.RequirementStatus
BugSourceType = _legacy.BugSourceType

User = _legacy.User
Version = _legacy.Version
Requirement = _legacy.Requirement
TestCase = _legacy.TestCase
TestExecution = _legacy.TestExecution
BugTracking = _legacy.BugTracking
BugStage5Record = _legacy.BugStage5Record

from app.models.audit import AuditLog

__all__ = [
    "AuditLog",
    "BugSourceType",
    "BugStage5Record",
    "BugTracking",
    "Requirement",
    "RequirementStatus",
    "TestCase",
    "TestExecution",
    "User",
    "UserRole",
    "Version",
    "VersionType",
]
