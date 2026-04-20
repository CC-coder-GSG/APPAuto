"""
Backward-compatibility shim.

The real implementation lives in `app.services.overall_test_service`.
This module only re-exports the legacy `Stage5Service` symbol (and the
`_sync_cache` internal used by tests) so existing imports keep working.
New code should import from `overall_test_service`.
"""
from __future__ import annotations

from app.services.overall_test_service import (
    OverallTestService,
    OverallTestService as Stage5Service,
    _sync_cache,
)

__all__ = ["Stage5Service", "OverallTestService", "_sync_cache"]
