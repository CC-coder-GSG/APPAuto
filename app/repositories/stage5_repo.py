"""
Backward-compatibility shim for the historical Stage5 repository name.
"""
from __future__ import annotations

from app.repositories.overall_test_repo import OverallTestRepository

Stage5Repository = OverallTestRepository

__all__ = ["Stage5Repository", "OverallTestRepository"]
