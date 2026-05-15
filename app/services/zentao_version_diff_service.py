"""
本地小版本 ↔ 禅道 build 对账 & 切换归属。

提供：
  - compare_local_vs_zentao(db, major_version_id) — 拉本地 minor + 禅道 builds，给前端做 diff 展示
  - apply_version_diff(db, major_version_id, actions) — 执行 diff 修复（删本地 / 从禅道补到本地）
  - build_record_reassign_major(db, build_record_id, target_major_id)
        — 把一条构建记录指向的小版本搬到另一个大版本下；禅道侧也跟着搬
        （禅道 IPD 4.3 不允许 API 删 build，DELETE → 403。所以走 PUT
          /v1/builds/{id} {execution:new_exec} 直接把原 build 移到目标执行下；
          只有原本没有 zentao_build_id 时才退回到"在目标执行下新建"。）

注意：所有禅道侧改动都走 get_system_zentao_client（优先 chenwenbo）。
任何禅道调用失败都返回 partial 结果 + 错误描述，不抛异常给路由。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models import BuildRecord, Version
from app.models.enums import VersionType
from app.services.zentao_client_service import ZentaoClient
from app.services.zentao_system_client import get_system_zentao_client
from app.services.zentao_utils import is_placeholder_name, normalize_version_name

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Q3: compare
# ---------------------------------------------------------------------------

def compare_local_vs_zentao(db: Session, major_version_id: int) -> dict:
    """
    返回一个 dict，前端可以直接渲染：
    {
      "major": {...},
      "execution": {"id": ..., "name": ...} | None,
      "local": [{id, version_no, zentao_build_id, ...}],
      "remote": [{id, name, normalized_name, is_placeholder}],
      "diff": {
        "only_local": [...local rows...],     # 本地有禅道无
        "only_remote": [...remote rows...],   # 禅道有本地无（前端可点"补到本地"）
        "matched": [...{local, remote}...],   # 已对齐
        "remote_placeholders": [...],         # 占位 build（不计入差异，单独展示）
      },
      "errors": [...]
    }
    """
    out: dict = {
        "major": None,
        "execution": None,
        "local": [],
        "remote": [],
        "diff": {"only_local": [], "only_remote": [], "matched": [], "remote_placeholders": []},
        "errors": [],
    }

    major = db.query(Version).filter(Version.id == major_version_id, Version.version_type == VersionType.MAJOR).first()
    if not major:
        out["errors"].append("大版本不存在")
        return out
    out["major"] = {
        "id": major.id,
        "version_no": major.version_no,
        "software_id": major.software_id,
        "zentao_execution_id": major.zentao_execution_id,
        "zentao_execution_name": major.zentao_execution_name_cache,
    }
    if major.zentao_execution_id:
        out["execution"] = {
            "id": major.zentao_execution_id,
            "name": major.zentao_execution_name_cache or "",
        }

    local_minors = (
        db.query(Version)
        .filter(Version.parent_id == major.id, Version.version_type == VersionType.MINOR)
        .order_by(Version.created_at.desc(), Version.id.desc())
        .all()
    )
    out["local"] = [
        {
            "id": m.id,
            "version_no": m.version_no,
            "zentao_build_id": m.zentao_build_id,
            "zentao_build_name_cache": m.zentao_build_name_cache,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in local_minors
    ]

    if not major.zentao_execution_id:
        out["errors"].append("该大版本未绑定禅道执行，无法对账")
        out["diff"]["only_local"] = list(out["local"])
        return out

    client = get_system_zentao_client(db)
    if not client:
        out["errors"].append("找不到可用的禅道账号绑定")
        return out

    try:
        remote_rows = client.list_execution_builds(int(major.zentao_execution_id), limit=500) or []
    except Exception as exc:
        logger.warning("list_execution_builds(%s) failed: %s", major.zentao_execution_id, exc)
        out["errors"].append(f"拉取禅道 build 列表失败：{exc}")
        return out

    remote_norm: list[dict] = []
    for b in remote_rows:
        bid = _coerce_int(b.get("id"))
        name = str(b.get("name") or "").strip()
        if not bid or not name:
            continue
        remote_norm.append({
            "id": bid,
            "name": name,
            "normalized_name": normalize_version_name(name) or name,
            "is_placeholder": is_placeholder_name(name),
        })
    out["remote"] = remote_norm

    remote_by_id = {r["id"]: r for r in remote_norm if not r["is_placeholder"]}
    remote_by_name = {r["normalized_name"]: r for r in remote_norm if not r["is_placeholder"]}
    out["diff"]["remote_placeholders"] = [r for r in remote_norm if r["is_placeholder"]]

    matched_remote_ids: set[int] = set()

    for m in local_minors:
        local_row = {
            "id": m.id,
            "version_no": m.version_no,
            "zentao_build_id": m.zentao_build_id,
        }
        match: Optional[dict] = None
        if m.zentao_build_id and m.zentao_build_id in remote_by_id:
            match = remote_by_id[m.zentao_build_id]
        else:
            match = remote_by_name.get(m.version_no)
        if match is not None:
            matched_remote_ids.add(match["id"])
            out["diff"]["matched"].append({
                "local": local_row,
                "remote": match,
                "name_mismatch": match["normalized_name"] != m.version_no,
            })
        else:
            out["diff"]["only_local"].append(local_row)

    for r in remote_norm:
        if r["is_placeholder"]:
            continue
        if r["id"] in matched_remote_ids:
            continue
        out["diff"]["only_remote"].append(r)

    return out


# ---------------------------------------------------------------------------
# Q3: apply
# ---------------------------------------------------------------------------

@dataclass
class DiffApplyResult:
    deleted_local: list[int]
    imported_local: list[int]
    errors: list[str]

    def as_dict(self) -> dict:
        return {
            "deleted_local": self.deleted_local,
            "imported_local": self.imported_local,
            "errors": self.errors,
        }


def apply_version_diff(db: Session, major_version_id: int, actions: list[dict]) -> dict:
    """
    actions 每条 = {"action": "delete_local", "version_id": int}
                 或 {"action": "import_from_zentao", "zentao_build_id": int}
    """
    res = DiffApplyResult(deleted_local=[], imported_local=[], errors=[])

    major = db.query(Version).filter(Version.id == major_version_id, Version.version_type == VersionType.MAJOR).first()
    if not major:
        res.errors.append("大版本不存在")
        return res.as_dict()

    # import_from_zentao 需要禅道 build 列表
    needs_remote = any(a.get("action") == "import_from_zentao" for a in actions)
    remote_map: dict[int, dict] = {}
    if needs_remote:
        if not major.zentao_execution_id:
            res.errors.append("该大版本未绑定禅道执行")
            return res.as_dict()
        client = get_system_zentao_client(db)
        if not client:
            res.errors.append("找不到可用的禅道账号绑定")
            return res.as_dict()
        try:
            rows = client.list_execution_builds(int(major.zentao_execution_id), limit=500) or []
            for b in rows:
                bid = _coerce_int(b.get("id"))
                if bid:
                    remote_map[bid] = b
        except Exception as exc:
            res.errors.append(f"拉取禅道 build 失败：{exc}")
            return res.as_dict()

    for action in actions:
        kind = (action or {}).get("action")
        if kind == "delete_local":
            vid = _coerce_int(action.get("version_id"))
            if not vid:
                res.errors.append("delete_local 缺少 version_id")
                continue
            row = (
                db.query(Version)
                .filter(Version.id == vid, Version.parent_id == major.id, Version.version_type == VersionType.MINOR)
                .first()
            )
            if not row:
                res.errors.append(f"小版本 {vid} 不在该大版本下，跳过")
                continue
            db.delete(row)
            res.deleted_local.append(vid)
        elif kind == "import_from_zentao":
            bid = _coerce_int(action.get("zentao_build_id"))
            if not bid or bid not in remote_map:
                res.errors.append(f"禅道 build {bid} 不存在，跳过")
                continue
            b = remote_map[bid]
            name = str(b.get("name") or "").strip()
            if not name:
                res.errors.append(f"禅道 build {bid} 名字为空，跳过")
                continue
            version_no = normalize_version_name(name) or name

            # 全局去重（uq_version_no_type）：同名 minor 已存在就把它搬到当前 major 下。
            # 用户从某个 major 的对账页点了"补到本地"，意图就是要这条 build 在这个 major 下，
            # 必须真正改 parent_id，否则下次刷新对账还是显示"本地无"。
            existing = (
                db.query(Version)
                .filter(Version.version_no == version_no, Version.version_type == VersionType.MINOR)
                .first()
            )
            if existing:
                existing.zentao_build_id = bid
                existing.zentao_build_name_cache = name
                if existing.parent_id != major.id:
                    old_parent_id = existing.parent_id
                    existing.parent_id = major.id
                    existing.software_id = major.software_id
                    res.errors.append(
                        f"小版本 {version_no} 已从大版本 id={old_parent_id} 搬到 id={major.id}"
                    )
                res.imported_local.append(existing.id)
                continue

            new_minor = Version(
                version_no=version_no,
                version_type=VersionType.MINOR,
                parent_id=major.id,
                software_id=major.software_id,
                zentao_build_id=bid,
                zentao_build_name_cache=name,
            )
            db.add(new_minor)
            db.flush()
            res.imported_local.append(new_minor.id)
        else:
            res.errors.append(f"未知 action：{kind}")

    db.commit()
    return res.as_dict()


# ---------------------------------------------------------------------------
# Q2e: reassign major
# ---------------------------------------------------------------------------

def build_record_reassign_major(
    db: Session,
    build_record_id: int,
    target_major_id: int,
) -> dict:
    """
    把一条 BuildRecord 对应的本地小版本（auto_archive_minor_version_id）切到 target_major_id 下，
    禅道侧同步搬迁。

    禅道侧策略（基于 IPD 4.3 实测）：
      - 原 minor 已经有 zentao_build_id ⇒ PUT /v1/builds/{old_bid} {execution:new_exec}
        把同一个 build 移到目标执行下（不删除、不新建、保留 stories/bugs/history）。
      - 原 minor 没绑过禅道 build ⇒ 在目标执行下新建一条同名 build。
      - API 不支持 DELETE build（403 Access not allowed），所以不再提供"保留原 build"
        这个开关；切归属永远是"移动"，不可能出现两个 build。

    返回 {ok, message, local_minor_id, moved_zentao_build_id, new_zentao_build_id, errors}
    """
    out: dict = {
        "ok": False,
        "message": "",
        "local_minor_id": None,
        "moved_zentao_build_id": None,
        "new_zentao_build_id": None,
        "errors": [],
    }

    record = db.query(BuildRecord).filter(BuildRecord.id == build_record_id).first()
    if not record:
        out["errors"].append("构建记录不存在")
        return out

    minor_id = record.auto_archive_minor_version_id
    if not minor_id:
        out["errors"].append("该构建记录尚未归档到本地小版本，无法切换归属")
        return out
    minor = db.query(Version).filter(Version.id == minor_id, Version.version_type == VersionType.MINOR).first()
    if not minor:
        out["errors"].append("本地小版本不存在")
        return out

    target_major = (
        db.query(Version)
        .filter(Version.id == target_major_id, Version.version_type == VersionType.MAJOR)
        .first()
    )
    if not target_major:
        out["errors"].append("目标大版本不存在")
        return out
    if target_major.id == minor.parent_id:
        out["errors"].append("目标大版本就是当前大版本，无需切换")
        return out

    old_major = db.query(Version).filter(Version.id == minor.parent_id).first()
    old_zentao_build_id = minor.zentao_build_id
    version_name = minor.version_no
    out["local_minor_id"] = minor.id

    # ── 禅道侧操作 ──
    client = get_system_zentao_client(db)
    if not client:
        out["errors"].append("找不到可用的禅道账号绑定，仅修改本地归属")
    elif not target_major.zentao_execution_id:
        out["errors"].append("目标大版本未绑定禅道执行，仅修改本地归属")
    else:
        target_exec_id = int(target_major.zentao_execution_id)
        if old_zentao_build_id:
            # 已经有 build —— 走 PUT 移动，最干净（无重复、stories/bugs 跟着走）
            try:
                client.update_build(int(old_zentao_build_id), execution_id=target_exec_id)
                minor.zentao_build_id = int(old_zentao_build_id)
                minor.zentao_build_name_cache = version_name
                out["moved_zentao_build_id"] = int(old_zentao_build_id)
            except Exception as exc:
                logger.warning(
                    "move build %s to exec %s failed: %s",
                    old_zentao_build_id, target_exec_id, exc,
                )
                out["errors"].append(f"移动原 build 到目标执行失败：{exc}")
        else:
            # 没绑过禅道 build —— 在目标执行下新建一条
            try:
                project_id, product_id = _resolve_target_exec_project_product(client, target_exec_id)
                if not project_id:
                    out["errors"].append(
                        f"无法解析目标执行 {target_exec_id} 的 project_id，跳过在目标执行新建 build"
                    )
                else:
                    created = client.create_execution_build(
                        target_exec_id,
                        version_name,
                        project_id=project_id,
                        product_id=product_id,
                    ) or {}
                    new_bid = _coerce_int(created.get("id"))
                    if new_bid:
                        minor.zentao_build_id = new_bid
                        minor.zentao_build_name_cache = version_name
                        out["new_zentao_build_id"] = new_bid
            except Exception as exc:
                logger.warning("create build in target execution failed: %s", exc)
                out["errors"].append(f"在目标执行创建 build 失败：{exc}")

    # ── 本地切归属 ──
    minor.parent_id = target_major.id
    minor.software_id = target_major.software_id
    db.commit()
    db.refresh(minor)

    out["ok"] = True
    parts = [f"已切到大版本 {target_major.version_no}"]
    if old_major:
        parts.insert(0, f"原大版本 {old_major.version_no}")
    if out["moved_zentao_build_id"]:
        parts.append(f"禅道 build id={out['moved_zentao_build_id']} 已移到目标执行")
    elif out["new_zentao_build_id"]:
        parts.append(f"禅道新建 build id={out['new_zentao_build_id']}")
    out["message"] = "；".join(parts)
    return out


def _coerce_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_target_exec_project_product(client, exec_id: int) -> tuple[Optional[int], Optional[int]]:
    """读一次执行 detail，取 (project_id, product_id)。两者都拿不到时返回 (None, None)。

    禅道 IPD 4.3 GET /v1/executions/{id} 用的字段是 `project`(标量) + `products`(列表)。
    """
    try:
        detail = client.get(f"executions/{exec_id}")
    except Exception as exc:
        logger.warning("resolve target execution(%s) detail failed: %s", exec_id, exc)
        return None, None
    if not isinstance(detail, dict):
        return None, None
    exec_obj = detail.get("execution") if isinstance(detail.get("execution"), dict) else detail
    if not isinstance(exec_obj, dict):
        return None, None
    project_id = _coerce_int(exec_obj.get("project"))
    product_id = None
    products = exec_obj.get("products")
    if isinstance(products, list):
        for item in products:
            if isinstance(item, dict):
                product_id = _coerce_int(item.get("id"))
                if product_id:
                    break
    if product_id is None:
        product_id = _coerce_int(exec_obj.get("product"))
    return project_id, product_id


__all__ = [
    "compare_local_vs_zentao",
    "apply_version_diff",
    "build_record_reassign_major",
]
