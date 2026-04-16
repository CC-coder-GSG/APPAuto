"""
Zentao version sync service.

Pulls executions and builds from Zentao and upserts them into the
local Version table as MAJOR / MINOR versions.

Naming rules
------------
- Pure sXXXX (only digits after 's', no suffix) → V4.0.3.x  (human-readable)
- sXXXX with ANY suffix, or names with no sXXXX prefix → original name as-is

Upsert strategy (avoids duplication)
-------------------------------------
For MAJOR versions:
  1. Look up by zentao_execution_id (precise, survives renames)
  2. Fall back to version_no + software_id (links manually-created versions)

For MINOR versions:
  1. Look up by zentao_build_id
  2. Fall back to version_no + parent_id
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Version, VersionType
from app.models.user_zentao_binding import UserZentaoBinding
from app.services.zentao_auth_service import get_valid_token
from app.services.zentao_client_service import ZentaoClient, ZentaoAPIError
from app.services.zentao_utils import normalize_version_name

logger = logging.getLogger(__name__)

_MAX_CONCURRENCY = 5
_PURE_S_PATTERN = re.compile(r'^s(\d{4,})$', re.IGNORECASE)


def _execution_to_version_no(name: str) -> str:
    """
    Convert a Zentao execution name to a local major version_no.

    Only pure sXXXX (exactly 's' + 4+ digits, no suffix) is converted
    to V4.0.3.x format.  All other names are returned as-is.

    Examples:
        s4031     → V4.0.3.1
        s40318    → V4.0.3.18
        s40311    → V4.0.3.11
        s4030(V4.0.3.0) → s4030(V4.0.3.0)  (has suffix → original)
        s4030东南亚      → s4030东南亚        (has suffix → original)
        SDK              → SDK               (no sXXXX → original)
        外业测试          → 外业测试           (no sXXXX → original)
    """
    text = (name or '').strip()
    m = _PURE_S_PATTERN.match(text)
    if m:
        digits = m.group(1)
        a, b, c = digits[0], digits[1], digits[2]
        rest = digits[3:]
        if rest:
            return f'V{a}.{b}.{c}.{rest}'
        return f'V{a}.{b}.{c}'
    return text


@dataclass
class VersionSyncResult:
    created_major: list[str] = field(default_factory=list)
    updated_major: list[str] = field(default_factory=list)
    created_minor: list[str] = field(default_factory=list)
    updated_minor: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class ZentaoVersionSyncService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_accessible_projects(self, user_id: int) -> list[dict]:
        """Return list of Zentao projects the user can access."""
        client = self._get_client(user_id)
        if not client:
            return []
        try:
            data = client.get('projects', params={'limit': 100}) or {}
            return [
                {
                    'id': p['id'],
                    'name': p.get('name', ''),
                    'code': p.get('code', ''),
                    'status': p.get('status', ''),
                }
                for p in data.get('projects', [])
                if p.get('id') and p.get('name')
            ]
        except Exception as exc:
            logger.warning('get_accessible_projects: %s', exc)
            return []

    async def sync_versions(
        self,
        user_id: int,
        software_id: int,
        zentao_project_id: int,
        sync_minor: bool = True,
    ) -> VersionSyncResult:
        """
        Sync MAJOR versions (executions) and optionally MINOR versions (builds).
        Returns a VersionSyncResult with counts of created / updated / skipped.
        """
        result = VersionSyncResult()
        client = self._get_client(user_id)
        if not client:
            logger.warning('sync_versions: no client for user_id=%s', user_id)
            return result

        # ── Step 1: fetch executions ──────────────────────────────────────
        try:
            data = await asyncio.to_thread(
                client.get,
                f'projects/{zentao_project_id}/executions',
                {'limit': 100},
            )
        except ZentaoAPIError as exc:
            logger.warning('sync_versions: fetch executions failed: %s', exc)
            return result

        executions = (data or {}).get('executions', [])

        # ── Step 2: upsert major versions ─────────────────────────────────
        exec_id_to_major: dict[int, Version] = {}
        for exc_row in executions:
            exec_id = exc_row.get('id')
            exec_name = (exc_row.get('name') or '').strip()
            if not exec_id or not exec_name:
                continue

            version_no = _execution_to_version_no(exec_name)
            if not version_no:
                result.skipped.append(exec_name)
                continue

            major = self._upsert_major(
                version_no=version_no,
                software_id=software_id,
                zentao_execution_id=exec_id,
                zentao_execution_name_cache=exec_name,
                result=result,
            )
            if major:
                exec_id_to_major[exec_id] = major

        self.db.flush()  # populate Version.id for newly created majors

        if not sync_minor or not exec_id_to_major:
            self.db.commit()
            return result

        # ── Step 3: fetch builds concurrently and upsert minor versions ───
        sem = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def _sync_one_execution(exec_id: int, major: Version) -> None:
            async with sem:
                try:
                    bdata = await asyncio.to_thread(
                        client.get,
                        f'executions/{exec_id}/builds',
                        {'limit': 100},
                    )
                except Exception as exc:
                    logger.warning('sync_versions: builds exec_id=%s err=%s', exec_id, exc)
                    return

                for b in (bdata or {}).get('builds', []):
                    build_id = b.get('id')
                    build_name = (b.get('name') or '').strip()
                    if not build_id or not build_name:
                        continue
                    version_no = normalize_version_name(build_name) or build_name
                    # DB mutations happen in the event loop (not in the thread)
                    self._upsert_minor(
                        version_no=version_no,
                        parent_id=major.id,
                        software_id=software_id,
                        zentao_build_id=build_id,
                        zentao_build_name_cache=build_name,
                        result=result,
                    )

        await asyncio.gather(*[
            _sync_one_execution(exec_id, major)
            for exec_id, major in exec_id_to_major.items()
        ])

        self.db.commit()
        return result

    # ------------------------------------------------------------------
    # Upsert helpers
    # ------------------------------------------------------------------

    def _upsert_major(
        self,
        version_no: str,
        software_id: int,
        zentao_execution_id: int,
        zentao_execution_name_cache: str,
        result: VersionSyncResult,
    ) -> Version | None:
        # 1. Precise match by Zentao execution ID
        v = (
            self.db.query(Version)
            .filter(
                Version.zentao_execution_id == zentao_execution_id,
                Version.version_type == VersionType.MAJOR,
            )
            .first()
        )

        # 2. Fallback: match by version_no + software_id (links manual entries)
        if v is None:
            v = (
                self.db.query(Version)
                .filter(
                    Version.version_no == version_no,
                    Version.software_id == software_id,
                    Version.version_type == VersionType.MAJOR,
                )
                .first()
            )

        # 3. Global fallback: match by version_no + version_type only, to avoid
        #    IntegrityError when the same name exists under a different software_id.
        if v is None:
            v = (
                self.db.query(Version)
                .filter(
                    Version.version_no == version_no,
                    Version.version_type == VersionType.MAJOR,
                )
                .first()
            )

        if v is not None:
            v.zentao_execution_id = zentao_execution_id
            v.zentao_execution_name_cache = zentao_execution_name_cache
            result.updated_major.append(version_no)
            return v

        v = Version(
            version_no=version_no,
            version_type=VersionType.MAJOR,
            software_id=software_id,
            zentao_execution_id=zentao_execution_id,
            zentao_execution_name_cache=zentao_execution_name_cache,
        )
        self.db.add(v)
        result.created_major.append(version_no)
        return v

    def _upsert_minor(
        self,
        version_no: str,
        parent_id: int,
        software_id: int,
        zentao_build_id: int,
        zentao_build_name_cache: str,
        result: VersionSyncResult,
    ) -> Version | None:
        # 1. Precise match by Zentao build ID
        v = (
            self.db.query(Version)
            .filter(
                Version.zentao_build_id == zentao_build_id,
                Version.version_type == VersionType.MINOR,
            )
            .first()
        )

        # 2. Fallback: match by version_no + parent_id
        if v is None:
            v = (
                self.db.query(Version)
                .filter(
                    Version.version_no == version_no,
                    Version.parent_id == parent_id,
                    Version.version_type == VersionType.MINOR,
                )
                .first()
            )

        # 3. Global fallback: match by version_no + version_type only.
        #    The UniqueConstraint is on (version_no, version_type), so if a minor
        #    version with the same name already exists under a different parent,
        #    inserting a duplicate would raise IntegrityError.  Find it and link
        #    the zentao_build_id instead of creating a conflicting row.
        if v is None:
            v = (
                self.db.query(Version)
                .filter(
                    Version.version_no == version_no,
                    Version.version_type == VersionType.MINOR,
                )
                .first()
            )

        if v is not None:
            v.zentao_build_id = zentao_build_id
            v.zentao_build_name_cache = zentao_build_name_cache
            result.updated_minor.append(version_no)
            return v

        v = Version(
            version_no=version_no,
            version_type=VersionType.MINOR,
            parent_id=parent_id,
            software_id=software_id,
            zentao_build_id=zentao_build_id,
            zentao_build_name_cache=zentao_build_name_cache,
        )
        self.db.add(v)
        result.created_minor.append(version_no)
        return v

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_client(self, user_id: int) -> ZentaoClient | None:
        binding = (
            self.db.query(UserZentaoBinding)
            .filter(UserZentaoBinding.user_id == user_id)
            .first()
        )
        if not binding or not binding.base_url:
            return None
        token = get_valid_token(user_id, self.db)
        if not token:
            return None
        return ZentaoClient(base_url=binding.base_url, token=token)


__all__ = ['ZentaoVersionSyncService', 'VersionSyncResult']
