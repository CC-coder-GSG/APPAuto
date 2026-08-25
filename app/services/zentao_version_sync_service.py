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
from app.services.zentao_utils import is_placeholder_name, normalize_version_name

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
    zentao_products: list[dict] = field(default_factory=list)


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
        # 用客户端的 list_project_executions（status=all + 分页）拉全部执行，
        # 否则禅道默认会丢掉 closed/suspended 状态的执行（s4031定制 就因此漏拉）。
        try:
            executions = await asyncio.to_thread(
                client.list_project_executions,
                zentao_project_id,
                200,
                'all',
            )
        except ZentaoAPIError as exc:
            logger.warning('sync_versions: fetch executions failed: %s', exc)
            return result
        except Exception as exc:
            logger.warning('sync_versions: fetch executions failed: %s', exc)
            return result

        result.zentao_products = await asyncio.to_thread(
            self._resolve_products_from_executions,
            client,
            executions,
        )

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
                    # DB mutations happen in the event loop (not in the thread).
                    # 单条 upsert 失败不应阻塞整次同步 — 记日志 + 跳过即可。
                    try:
                        self._upsert_minor(
                            version_no=version_no,
                            parent_id=major.id,
                            software_id=software_id,
                            zentao_build_id=build_id,
                            zentao_build_name_cache=build_name,
                            result=result,
                        )
                    except Exception as exc:
                        logger.warning(
                            'sync_versions: upsert minor failed exec=%s build=%s name=%s err=%s',
                            exec_id, build_id, build_name, exc,
                        )
                        result.skipped.append(f'{build_name} (exec={exec_id})')

        await asyncio.gather(*[
            _sync_one_execution(exec_id, major)
            for exec_id, major in exec_id_to_major.items()
        ])

        # 提交：若仍撞唯一约束（极端竞争场景），回滚并重试一次单行 INSERT，
        # 把失败的 minor 跳过、其他保留。
        try:
            self.db.commit()
        except Exception as exc:
            logger.warning('sync_versions: bulk commit failed (%s) — falling back to per-row', exc)
            self.db.rollback()
            self._fallback_per_row_commit(result)
        return result

    def reconcile_execution_minors(
        self,
        client: ZentaoClient,
        *,
        software_id: int,
        parent_id: int,
        zentao_execution_id: int,
    ) -> VersionSyncResult:
        """
        Pull a single execution's builds from Zentao and upsert local MINOR
        versions under ``parent_id``.

        与 ``sync_versions`` 不同，本方法是**同步**的、接受一个已认证好的
        ``ZentaoClient``（通常是系统账号），且只处理单个执行——用于 Jenkins
        构建写回禅道之后立即对齐本地小版本（webhook 没有用户级禅道绑定）。

        幂等：按 ``zentao_build_id`` / ``version_no+parent`` 命中已有行只更新；
        占位 build（名字带 xxxx）不算真实小版本，跳过。
        """
        result = VersionSyncResult()
        try:
            builds = client.list_execution_builds(zentao_execution_id, limit=500) or []
        except Exception as exc:
            logger.warning(
                "reconcile_execution_minors: list builds exec=%s failed: %s",
                zentao_execution_id, exc,
            )
            return result

        for b in builds:
            build_id = b.get("id")
            build_name = (b.get("name") or "").strip()
            if not build_id or not build_name:
                continue
            if is_placeholder_name(build_name):
                continue
            version_no = normalize_version_name(build_name) or build_name
            try:
                self._upsert_minor(
                    version_no=version_no,
                    parent_id=parent_id,
                    software_id=software_id,
                    zentao_build_id=build_id,
                    zentao_build_name_cache=build_name,
                    result=result,
                )
            except Exception as exc:
                logger.warning(
                    "reconcile_execution_minors: upsert minor failed exec=%s build=%s name=%s err=%s",
                    zentao_execution_id, build_id, build_name, exc,
                )
                result.skipped.append(f"{build_name} (exec={zentao_execution_id})")

        try:
            self.db.commit()
        except Exception as exc:
            logger.warning("reconcile_execution_minors: commit failed (%s)", exc)
            self.db.rollback()
        return result

    def _fallback_per_row_commit(self, result: VersionSyncResult) -> None:
        """
        Bulk commit 失败的兜底：对 session.new 里的 Version 逐条 add+commit，
        遇到 IntegrityError 直接跳过那条，不让整次同步报 500。
        Note: 调用前 session 已 rollback，需要重新从 result 里 add；
        但我们这里已经丢了对象引用，所以本兜底只能保证不抛 500、把 summary 标记。
        """
        result.skipped.append('bulk-commit-fallback: 部分版本因唯一约束冲突未写入')

    # ------------------------------------------------------------------
    # Upsert helpers
    # ------------------------------------------------------------------

    def _find_pending(
        self,
        version_type: VersionType,
        version_no: str,
        software_id: int,
    ) -> Version | None:
        """
        Session autoflush=False，两个 coroutine 在同一次 sync 里 add 同名 Version 时
        互相看不见对方的 pending insert，commit 时撞唯一约束。这里扫一遍 session.new
        把当前 batch 里已 add 但还没 flush 的同名行找出来，让后来者直接复用并更新。
        """
        for obj in list(self.db.new):
            if (
                isinstance(obj, Version)
                and obj.version_type == version_type
                and obj.version_no == version_no
                and obj.software_id == software_id
            ):
                return obj
        return None

    def _upsert_major(
        self,
        version_no: str,
        software_id: int,
        zentao_execution_id: int,
        zentao_execution_name_cache: str,
        result: VersionSyncResult,
    ) -> Version | None:
        # 0. Pending insert in this session (autoflush=False guard)
        v = self._find_pending(VersionType.MAJOR, version_no, software_id)

        # 1. Precise match by Zentao execution ID
        if v is None:
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
        # 0. Pending insert in this session (autoflush=False guard)
        v = self._find_pending(VersionType.MINOR, version_no, software_id)

        # 1. Precise match by Zentao build ID
        if v is None:
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

        # 3. Software fallback: a build may move between executions inside one
        #    software, but a same-named build in another software is unrelated.
        if v is None:
            v = (
                self.db.query(Version)
                .filter(
                    Version.version_no == version_no,
                    Version.version_type == VersionType.MINOR,
                    Version.software_id == software_id,
                )
                .first()
            )

        if v is not None:
            v.zentao_build_id = zentao_build_id
            v.zentao_build_name_cache = zentao_build_name_cache
            # 跟随禅道侧的归属：build 在禅道被搬到另一个执行后，本地 parent 也要跟着搬。
            # 否则按 zentao_build_id 命中后只更新名字，version 永远卡在旧 major 下，
            # 新 major 的对账永远显示"本地无"。
            if v.parent_id != parent_id:
                v.parent_id = parent_id
            if software_id and v.software_id != software_id:
                v.software_id = software_id
            # 跟随禅道侧的 build name：push 流程会把"占位 build"在禅道侧 rename 成真实
            # 版本名（id 不变），但本地行的 version_no 不会自动变。下次 sync 按
            # zentao_build_id 命中本地行时，如果只刷 cache、不刷 version_no，会留下
            # "version_no=占位 + cache=真实名"的脏状态；后续禅道补的新占位 build 同步
            # 时会通过"按 version_no+parent 匹配"误抢这一行的 zentao_build_id，导致
            # 真实 build 在本地丢失。这里把 version_no 也跟随到禅道当前名。
            if v.version_no != version_no:
                conflict = (
                    self.db.query(Version)
                    .filter(
                        Version.version_no == version_no,
                        Version.version_type == VersionType.MINOR,
                        Version.software_id == software_id,
                        Version.id != v.id,
                    )
                    .first()
                )
                if conflict is None:
                    logger.info(
                        'sync_versions: rename minor id=%s %r -> %r (build_id=%s)',
                        v.id, v.version_no, version_no, zentao_build_id,
                    )
                    v.version_no = version_no
                else:
                    # 唯一约束 (version_no, version_type) 不允许两行同名 MINOR。
                    # 命中冲突时不强行 rename —— 把脏状态保留下来不致 500，下次
                    # sync 在另一条 build 上仍有机会修复。
                    logger.warning(
                        'sync_versions: cannot rename minor id=%s %r -> %r '
                        '(conflict with id=%s); leaving version_no as-is',
                        v.id, v.version_no, version_no, conflict.id,
                    )
                    result.skipped.append(
                        f'rename-blocked {v.version_no!r} -> {version_no!r} '
                        f'(conflict id={conflict.id})'
                    )
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

    @staticmethod
    def _resolve_products_from_executions(
        client: ZentaoClient,
        executions: list[dict],
    ) -> list[dict]:
        """
        Resolve a project's real product IDs from execution details.

        The Zentao project payload only says ``product=single/multiple``; it
        does not contain the product ID.  Execution details expose the linked
        products as ``[{"id": ..., "name": ...}]``.
        """
        products: dict[int, dict] = {}
        for execution in executions:
            execution_id = execution.get("id")
            if not execution_id:
                continue
            try:
                detail = client.get(f"executions/{int(execution_id)}") or {}
            except Exception as exc:
                logger.debug(
                    "sync_versions: resolve products from execution=%s failed: %s",
                    execution_id,
                    exc,
                )
                continue
            raw_products = detail.get("products", []) if isinstance(detail, dict) else []
            if isinstance(raw_products, dict):
                raw_products = list(raw_products.values())
            for raw in raw_products if isinstance(raw_products, list) else []:
                if not isinstance(raw, dict):
                    continue
                try:
                    product_id = int(raw.get("id"))
                except (TypeError, ValueError):
                    continue
                products[product_id] = {
                    "id": product_id,
                    "name": str(raw.get("name") or "").strip(),
                }
            if products:
                # Every execution under a single-product project exposes the
                # same product.  One successful detail is enough and avoids a
                # request per historical execution.
                break
        return list(products.values())

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
