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
from app.services.zentao_utils import is_placeholder_name, make_placeholder_name, normalize_version_name

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
        "new_placeholder_build_id": None,
        "new_placeholder_name": None,
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

    # 旧版本会在 target_major.id == minor.parent_id 时 hard-fail "已经在该大版本"。
    # 但本地 parent_id 跟禅道 build 真实归属可能脱节（早期 reassign 失败留下的状态），
    # 这种情况用户点"切到 X"恰恰是想做一次"对齐到 X"的修复。所以这里不再 early-return，
    # 不管 local 怎么说都跑一遍禅道侧搬迁逻辑：update_build 把 build 拽到 target_exec，
    # 如果 build 本来就在那儿，update_build 实际上是 no-op，依然安全。
    old_major = db.query(Version).filter(Version.id == minor.parent_id).first()
    old_zentao_build_id = minor.zentao_build_id
    version_name = minor.version_no
    out["local_minor_id"] = minor.id

    # ── 禅道侧操作 ──
    client = get_system_zentao_client(db)
    target_exec_name: Optional[str] = None
    if not client:
        out["errors"].append("找不到可用的禅道账号绑定，仅修改本地归属")
    elif not target_major.zentao_execution_id:
        out["errors"].append("目标大版本未绑定禅道执行，仅修改本地归属")
    else:
        target_exec_id = int(target_major.zentao_execution_id)
        # 拿目标执行的 name / project / product / builder —— builder 是 create 必填
        target_exec_name, target_project_id, target_product_id, target_builder = _resolve_target_exec_meta(
            client, target_exec_id
        )
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

            # 收尾：扫一下项目里 normalize 后同名的 build（包括 (64-bit) 变体），
            # 把不在 target_exec 的全部 PUT 过去 —— 用户明确要"源不留 build"
            if target_project_id and out["moved_zentao_build_id"]:
                _move_orphans_to_target(
                    client,
                    project_id=target_project_id,
                    version_name=version_name,
                    target_exec_id=target_exec_id,
                    skip_ids={int(old_zentao_build_id)},
                    errors=out["errors"],
                )
        else:
            # 本地 minor 没记录 zentao_build_id —— 不一定真的没建过 build。常见原因：
            # push 时只更新了 BuildRecord，没回填 minor.zentao_build_id。所以这里先
            # 在项目维度按归一化 name 找一遍：
            #   - 找到 + 已经在 target_exec ⇒ adopt（写回本地，不动禅道）
            #   - 找到 + 在别的 exec ⇒ PUT 移到 target
            #   - 找到多条（既有 target 的也有源的孤儿）⇒ adopt target 的那条，
            #     把所有源孤儿都 PUT 到 target，让源不再留有 build
            #   - 一条都没找到 ⇒ 才真的去 create
            # 没这条分支会撞禅道 400 "名称编号已经有 xxx 这条记录了"。
            try:
                if not target_project_id:
                    out["errors"].append(
                        f"无法解析目标执行 {target_exec_id} 的 project_id，跳过在目标执行新建 build"
                    )
                else:
                    matches = _find_builds_by_name_in_project(client, target_project_id, version_name)
                    if matches:
                        # 拆出 target 内 vs 其他 exec 的孤儿
                        in_target = [m for m in matches if _coerce_int(m.get("execution")) == target_exec_id]
                        orphans = [m for m in matches if _coerce_int(m.get("execution")) != target_exec_id]
                        # adopt：优先用 target 内已有的；没有就把第一个孤儿 PUT 过去后 adopt
                        adopt_id: Optional[int] = None
                        if in_target:
                            adopt_id = _coerce_int(in_target[0].get("id"))
                            minor.zentao_build_id = adopt_id
                            minor.zentao_build_name_cache = version_name
                            out["moved_zentao_build_id"] = adopt_id
                        elif orphans:
                            first_orphan = orphans.pop(0)
                            orphan_id = _coerce_int(first_orphan.get("id"))
                            try:
                                client.update_build(orphan_id, execution_id=target_exec_id)
                                adopt_id = orphan_id
                                minor.zentao_build_id = adopt_id
                                minor.zentao_build_name_cache = version_name
                                out["moved_zentao_build_id"] = adopt_id
                            except Exception as exc:
                                logger.warning(
                                    "move existing build %s (was exec %s) to %s failed: %s",
                                    orphan_id, first_orphan.get("execution"), target_exec_id, exc,
                                )
                                out["errors"].append(
                                    f"找到同名 build #{orphan_id}（在执行 {first_orphan.get('execution')}），移到目标执行失败：{exc}"
                                )
                        # 把剩余孤儿也都 PUT 到 target —— 用户明确要"源执行不留 build"
                        for orphan in orphans:
                            orphan_id = _coerce_int(orphan.get("id"))
                            orphan_exec = orphan.get("execution")
                            try:
                                client.update_build(orphan_id, execution_id=target_exec_id)
                            except Exception as exc:
                                logger.warning(
                                    "move orphan build %s (was exec %s) to %s failed: %s",
                                    orphan_id, orphan_exec, target_exec_id, exc,
                                )
                                out["errors"].append(
                                    f"清理源执行 {orphan_exec} 的孤儿 build #{orphan_id} 失败：{exc}"
                                )
                    else:
                        created = client.create_execution_build(
                            target_exec_id,
                            version_name,
                            project_id=target_project_id,
                            product_id=target_product_id,
                            builder=target_builder,
                        ) or {}
                        new_bid = _coerce_int(created.get("id"))
                        if new_bid:
                            minor.zentao_build_id = new_bid
                            minor.zentao_build_name_cache = version_name
                            out["new_zentao_build_id"] = new_bid
            except Exception as exc:
                logger.warning("create build in target execution failed: %s", exc)
                out["errors"].append(f"在目标执行创建 build 失败：{exc}")

        # ── 保证 target_exec 下有占位 build，未来 push 才有东西可以 rename ──
        # 只在成功 reassign（move 或 create 或收编）后做。失败不致命，加到 errors 但不挡总流程。
        zentao_succeeded = bool(out["moved_zentao_build_id"] or out["new_zentao_build_id"])
        if zentao_succeeded and target_project_id:
            ph_id, ph_name, ph_err = _ensure_placeholder_in_target_exec(
                client,
                target_exec_id=target_exec_id,
                target_project_id=target_project_id,
                target_product_id=target_product_id,
                target_builder=target_builder,
                version_name=version_name,
            )
            if ph_id:
                out["new_placeholder_build_id"] = ph_id
                out["new_placeholder_name"] = ph_name
            elif ph_err:
                out["errors"].append(f"目标执行补占位失败：{ph_err}")

    # ── 本地切归属 ──
    minor.parent_id = target_major.id
    minor.software_id = target_major.software_id

    # ── 写回到 BuildRecord.zentao_push_*，让"禅道写回"卡片立即反映新位置 ──
    _refresh_record_zentao_push_after_reassign(
        record=record,
        version_name=version_name,
        target_major_no=target_major.version_no,
        target_exec_id=target_major.zentao_execution_id,
        target_exec_name=target_exec_name,
        moved_build_id=out["moved_zentao_build_id"],
        new_build_id=out["new_zentao_build_id"],
        placeholder_build_id=out["new_placeholder_build_id"],
        placeholder_name=out["new_placeholder_name"],
        errors=out["errors"],
    )

    db.commit()
    db.refresh(minor)
    db.refresh(record)

    # 通知 SSE 订阅者：让正打开页面的客户端立即看到"禅道写回"卡片的新位置，
    # 不用等用户手动刷新
    try:
        from app.services.build_record_service import BuildRecordService
        from app.services.sse_service import sse_publish
        sse_publish(
            "build_record_updated",
            {"item": BuildRecordService.serialize(record), "action": "updated"},
            channels=["global"],
        )
    except Exception as exc:
        logger.warning("sse_publish after reassign failed: %s", exc)

    out["ok"] = True
    parts = [f"已切到大版本 {target_major.version_no}"]
    if old_major:
        parts.insert(0, f"原大版本 {old_major.version_no}")
    if out["moved_zentao_build_id"]:
        head = f"{target_exec_name} #{target_major.zentao_execution_id}" if target_exec_name else f"执行 #{target_major.zentao_execution_id}"
        parts.append(f"禅道 build #{out['moved_zentao_build_id']} 已移到 {head}")
    elif out["new_zentao_build_id"]:
        head = f"{target_exec_name} #{target_major.zentao_execution_id}" if target_exec_name else f"执行 #{target_major.zentao_execution_id}"
        parts.append(f"在 {head} 新建 build #{out['new_zentao_build_id']}")
    out["message"] = "；".join(parts)
    return out


def _coerce_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ensure_placeholder_in_target_exec(
    client,
    *,
    target_exec_id: int,
    target_project_id: int,
    target_product_id: Optional[int],
    target_builder: Optional[str],
    version_name: str,
) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """切归属后保证 target_exec 下有一条占位 build，让以后的 push 有东西可以 rename。

    禅道 build name 是 PROJECT 维度唯一的（不是 per-exec），所以不能简单拷贝
    源 exec 的占位名。规则：
      1) target_exec 已经有任何含 'xxxx' 的 build ⇒ 不动
      2) 否则用 make_placeholder_name(version_name) 取规范占位名
      3) 在 project 维度查这个名字有没有用过：
         - 没用过 ⇒ 用规范名 create
         - 用过了 ⇒ 给规范名加个 _e<exec_id> 后缀让它在 project 内独一份

    返回 (placeholder_id, placeholder_name, error)。失败不致命，由调用方决定要不要
    把它写进 errors。
    """
    canonical = make_placeholder_name(version_name)
    if not canonical or canonical == version_name:
        return None, None, "无法从 version_name 生成占位名"

    # (1) target_exec 已经有占位？
    try:
        target_builds = client.list_execution_builds(target_exec_id, limit=500) or []
    except Exception as exc:
        logger.warning("list target exec(%s) builds failed: %s", target_exec_id, exc)
        return None, None, f"读取目标执行 build 列表失败：{exc}"
    for b in target_builds:
        if is_placeholder_name(str(b.get("name") or "")):
            return _coerce_int(b.get("id")), str(b.get("name") or ""), None

    # (2)/(3) 决定要 create 哪个 name
    try:
        project_builds = client.list_project_builds(target_project_id, limit=500) or []
    except Exception as exc:
        logger.warning("list project(%s) builds failed: %s", target_project_id, exc)
        project_builds = []
    used_names = {str(b.get("name") or "").strip() for b in project_builds}
    placeholder_name = canonical
    if canonical in used_names:
        placeholder_name = _disambiguate_placeholder(canonical, target_exec_id)
        # 极端情况：连带后缀也撞了 —— 再 append "_b" / "_c"
        suffix_idx = ord("b")
        while placeholder_name in used_names and suffix_idx < ord("z"):
            placeholder_name = f"{canonical}_e{target_exec_id}_{chr(suffix_idx)}"
            suffix_idx += 1
        if placeholder_name in used_names:
            return None, None, f"无法为占位名 '{canonical}' 找到不冲突的变体"

    # (4) create
    try:
        created = client.create_execution_build(
            target_exec_id,
            placeholder_name,
            project_id=target_project_id,
            product_id=target_product_id,
            builder=target_builder,
        ) or {}
        new_id = _coerce_int(created.get("id"))
        if not new_id:
            # 容忍 list 缓存延迟：再查一次
            try:
                refreshed = client.list_execution_builds(target_exec_id, limit=500) or []
                match = next((b for b in refreshed if str(b.get("name") or "").strip() == placeholder_name), None)
                if match:
                    new_id = _coerce_int(match.get("id"))
            except Exception:
                pass
        if new_id:
            return new_id, placeholder_name, None
        return None, placeholder_name, "禅道返回了 200 但 list 里找不到新建的占位"
    except Exception as exc:
        logger.warning("create placeholder in exec(%s) failed: %s", target_exec_id, exc)
        return None, placeholder_name, f"建占位失败：{exc}"


def _disambiguate_placeholder(canonical: str, exec_id: int) -> str:
    """给占位名加 _e<exec_id> 后缀，让它在 project 内独一份。

    canonical 里末尾通常是 ')'（如 '4.0.3.1.26xxxx(4030xxxx)'），把后缀塞在
    最后一个 '(' 之前看上去更整齐。没有括号的极端情况就直接 append。
    """
    base = canonical
    if base.endswith(')') and '(' in base:
        head, sep, tail = base.rpartition('(')
        return f"{head.rstrip()}_e{exec_id}({tail}"
    return f"{canonical}_e{exec_id}"


def _move_orphans_to_target(
    client,
    *,
    project_id: int,
    version_name: str,
    target_exec_id: int,
    skip_ids: set,
    errors: list[str],
) -> None:
    """扫项目里 normalize 后同名的 build，把不在 target_exec 的全部 PUT 过去。

    用于 reassign 收尾 —— 用户期望"切归属后源执行不再有这个 build"。但禅道 API
    不允许 DELETE（403），所以唯一让源 exec 失去 build 的办法就是 PUT 把
    execution 字段改成 target。命中的 build 即使在 target 已经存在同名变体
    （比如 (64-bit) vs 无 (64-bit)），也仍然是 project 内唯一 name 的两条记录，
    PUT 不会冲突。

    skip_ids：跳过的 build id 集合（通常是已经 adopt 的那条，避免对自己再 PUT）。
    """
    try:
        matches = _find_builds_by_name_in_project(client, project_id, version_name)
    except Exception as exc:
        logger.warning("find orphans for '%s' failed: %s", version_name, exc)
        return
    for row in matches:
        rid = _coerce_int(row.get("id"))
        if not rid or rid in skip_ids:
            continue
        if _coerce_int(row.get("execution")) == target_exec_id:
            continue
        try:
            client.update_build(rid, execution_id=target_exec_id)
            logger.info(
                "move orphan build #%s (was exec %s) → target exec %s",
                rid, row.get("execution"), target_exec_id,
            )
        except Exception as exc:
            logger.warning("move orphan build #%s → %s failed: %s", rid, target_exec_id, exc)
            errors.append(
                f"清理源执行 {row.get('execution')} 的孤儿 build #{rid} 失败：{exc}"
            )


def _find_builds_by_name_in_project(client, project_id: int, name: str) -> list[dict]:
    """在项目维度（跨所有 execution）按归一化 name 找 build，返回所有匹配。

    禅道 v1 没有 "GET /v1/builds?name=..." 这种全局搜索，但 GET /v1/projects/{id}/builds
    返回的每条 row 已经带 execution 字段。

    归一化对比：本地 minor.version_no 是 normalize_version_name 处理过的（剥掉
    (64-bit) / (32-bit) 平台后缀），但禅道 build name 经常保留原样。所以这里
    在比较前两边都跑一次 normalize_version_name 让 `...40301053)` 能匹到
    `...40301053)(64-bit)`，不至于落到 create 分支撞 unique-name。

    返回所有匹配 —— 调用方可以"adopt（同 exec）+ PUT-move（异 exec 的孤儿）"。
    """
    target = normalize_version_name((name or "").strip()).strip()
    if not target:
        return []
    try:
        rows = client.list_project_builds(project_id, limit=500) or []
    except Exception as exc:
        logger.warning("list_project_builds(%s) failed: %s", project_id, exc)
        return []
    out: list[dict] = []
    for row in rows:
        row_norm = normalize_version_name(str(row.get("name") or "").strip()).strip()
        if row_norm == target:
            out.append(row)
    return out


def _resolve_target_exec_meta(
    client, exec_id: int
) -> tuple[Optional[str], Optional[int], Optional[int], Optional[str]]:
    """读一次执行 detail，取 (execution_name, project_id, product_id, builder)。

    禅道 IPD 4.3 GET /v1/executions/{id} 字段：`name`、`project`(标量) + `products`(列表)、PM/openedBy。
    解析失败的字段返回 None；调用方自己判断够不够用。

    builder：MCP 实测 `POST /v1/projects/{id}/builds` 缺 builder ⇒ 400
    "构建者不能为空"，所以 reassign 走 create 分支前必须先把它解出来。
    """
    try:
        detail = client.get(f"executions/{exec_id}")
    except Exception as exc:
        logger.warning("resolve target execution(%s) detail failed: %s", exec_id, exc)
        return None, None, None, None
    if not isinstance(detail, dict):
        return None, None, None, None
    exec_obj = detail.get("execution") if isinstance(detail.get("execution"), dict) else detail
    if not isinstance(exec_obj, dict):
        return None, None, None, None
    exec_name = exec_obj.get("name") if isinstance(exec_obj.get("name"), str) else None
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

    builder: Optional[str] = None
    raw_builder = exec_obj.get("PM") or exec_obj.get("openedBy")
    if isinstance(raw_builder, dict):
        builder = raw_builder.get("account") or raw_builder.get("realname")
    elif isinstance(raw_builder, str):
        builder = raw_builder
    # 兜底：PM/openedBy 都空 ⇒ 回退到当前 token 对应的账号（/v1/user.profile.account）。
    # 否则 create build 会被禅道挡为 400 "构建者不能为空"。
    if not builder:
        try:
            me = client.get("user")
            profile = me.get("profile") if isinstance(me, dict) else None
            if isinstance(profile, dict):
                builder = profile.get("account") or profile.get("realname")
        except Exception as exc:
            logger.warning("fallback resolve current user account failed: %s", exc)
    return exec_name, project_id, product_id, builder


def _refresh_record_zentao_push_after_reassign(
    *,
    record: BuildRecord,
    version_name: str,
    target_major_no: str,
    target_exec_id,
    target_exec_name: Optional[str],
    moved_build_id: Optional[int],
    new_build_id: Optional[int],
    placeholder_build_id: Optional[int] = None,
    placeholder_name: Optional[str] = None,
    errors: Optional[list[str]] = None,
) -> None:
    """切归属后**永远**重写 BuildRecord.zentao_push_*，让"禅道写回"那张卡片立即
    反映新位置。

    设计原则：写一段崭新的、能让用户一眼看出新归属的 message —— 不再"在旧
    message 上 append 注解"，因为那种格式下旧位置信息会主导视觉，用户会以为
    什么都没变。

    message 形态：
      "切归属 → <exec_name> #<exec_id>: build \"<version>\" #<bid>"
      "切归属 → <target_major>（未绑定禅道执行）"
      失败时再 ` | 部分失败：xxx` 接在末尾
    """
    from app.utils.time_utils import local_now

    # 头部 —— 优先用执行名+id，没有执行就回落到大版本号
    if target_exec_name and target_exec_id:
        head = f"{target_exec_name} #{target_exec_id}"
    elif target_exec_id:
        head = f"执行 #{target_exec_id}"
    else:
        head = target_major_no or "目标大版本"

    bid = moved_build_id or new_build_id
    if bid:
        action = "已切归属" if moved_build_id else "新建"
        body = f'build "{version_name}" #{bid}（{action}）'
    elif errors:
        body = "未写入禅道"
    else:
        body = "切归属（无禅道 build 关联）"

    msg = f"切归属 → {head}: {body}"
    if placeholder_build_id and placeholder_name:
        # 注意：这条占位可能是这次新建的，也可能是之前 reassign 留下来的；不管哪种
        # 都是 target_exec 当前"有效"的占位，未来 push 来了就 rename 它
        msg = f'{msg}; 占位（就绪）"{placeholder_name}" #{placeholder_build_id}'
    if errors:
        note = "；".join(errors[:2])[:200]
        msg = f"{msg} | 部分失败：{note}"

    # 状态：禅道侧动作有失败时打 'error' 让卡片用红色标记，没有失败就是 'ok'
    record.zentao_push_status = "error" if errors and not bid else "ok"
    record.zentao_push_message = msg[:480]
    record.zentao_pushed_at = local_now()


__all__ = [
    "compare_local_vs_zentao",
    "apply_version_diff",
    "build_record_reassign_major",
]
