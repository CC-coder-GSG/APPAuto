"""任务工时记录（effort）服务（2026-07-17 工时口径升级）。

旧口径的问题：开始/暂停只在平台本地结算，完成时把总耗时一次性通过 finish 的
currentConsumed 提交禅道 → 禅道只有一条落在完成当天的工时记录，公司工时系统
（按天统计）会看到「一天消耗好几天的工时」。

新口径：每次暂停把「本段开始→暂停」的工时立刻提交为禅道工时记录，跨天的段
按天拆行落到实际发生的日期；完成时只提交最后一段。禅道任务总消耗 = 各段记录
之和，总数不变、按天分布正确。

⚠️ 工时记录归属禅道「当前登录人」，必须用本人的网页登录凭据提交——用系统账号
代提会把工时算到别人头上。没绑定/登录失败时调用方退回旧口径（本地累计、完成时
一次性提交），由 zentao_task_mirror_service / zentao_task_sync_service 兜底。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Requirement, User
from app.models.zentao_task_mirror import ZentaoTaskMirror
from app.services.sse_service import sse_publish
from app.services.zentao_system_client import (
    get_system_zentao_client,
    get_system_zentao_web_login,
    get_user_zentao_client,
    get_user_zentao_web_login,
)
from app.services.zentao_task_status import effective_task_status, raw_task_status
from app.services.zentao_web_session import (
    ZentaoWebSessionError,
    cancel_task_via_web,
    close_task_via_web,
    delete_task_effort_via_web,
    edit_task_effort_via_web,
    list_task_efforts_via_web,
    pause_task_via_web,
    record_task_efforts_via_web,
)
from app.utils.time_utils import local_now
logger = logging.getLogger(__name__)

# 平台自动结算的工时记录统一带此前缀，便于在禅道里区分手工记录
AUTO_NOTE = "OmniQA 自动结算"


def merge_extra_hours(day_rows: list[tuple[date, float]], extra_hours: float, on_day: date) -> list[tuple[date, float]]:
    """把无法归属到具体日期的小时数（旧口径遗留的本地累计）并入 on_day 行。"""
    extra = round(float(extra_hours or 0.0), 2)
    if extra <= 0:
        return day_rows
    out = [(d, h) for d, h in day_rows if d != on_day]
    hit = next((h for d, h in day_rows if d == on_day), 0.0)
    out.append((on_day, round(hit + extra, 2)))
    out.sort(key=lambda r: r[0])
    return out


def submit_day_efforts(
    login,
    task_id: int,
    day_rows: list[tuple[date, float]],
    *,
    left_before: float,
    note: str = "",
) -> float:
    """把 [(日期, 小时)] 提交为禅道工时记录，left 按行递减（最后一行的 left 会被
    禅道用作任务剩余工时）。返回提交的小时合计；失败抛 ZentaoWebSessionError。

    ⚠️ left 绝不能递减到 0：禅道对 left=0 的工时记录会把任务自动标记「已完成」，
    且这种自动完成不写 finishedDate/finishedBy（2026-07-17 线上实证，任务 #17499
    暂停结算直接被完成、看板延期归列随之错乱）。地板值 0.1，真正的完成由
    finish 动作显式提交（它会正确写完成时间与完成人）。"""
    rows = []
    remaining = max(round(float(left_before or 0.0), 2), 0.1)
    total = 0.0
    for d, hours in day_rows:
        hours = round(float(hours), 2)
        if hours <= 0:
            continue
        remaining = max(round(remaining - hours, 2), 0.1)
        total = round(total + hours, 2)
        rows.append({
            "date": d.isoformat(),
            "consumed": hours,
            "left": remaining,
            "work": note or AUTO_NOTE,
        })
    if not rows:
        return 0.0
    record_task_efforts_via_web(login, task_id, rows)
    return total


# ── 面向 API 的查看 / 编辑 ─────────────────────────────────────────────────────


def _task_clients(db: Session, current_user: User) -> list:
    """本人优先、系统账号兜底的去重任务客户端列表。"""
    acting = get_user_zentao_client(current_user.id, db)
    system = get_system_zentao_client(db)
    clients = []
    for client in (acting, system):
        if client is not None and all(client is not item for item in clients):
            clients.append(client)
    return clients


def _read_task_snapshot(clients: list, task_id: int) -> Optional[dict]:
    """从任一可用 REST 客户端读取任务；读取失败时尝试下一账号。"""
    for client in clients:
        try:
            task = client.get_task(task_id) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("read task %s while protecting effort edit failed: %s", task_id, exc)
            continue
        if isinstance(task, dict) and effective_task_status(task):
            return task
    return None


def _cached_task_snapshot(db: Session, task_id: int) -> Optional[dict]:
    """REST 暂时不可读时，用两套本地状态缓存确定修改前状态。"""
    mirror = db.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == task_id).first()
    if mirror and (mirror.status or "").strip():
        return {"status": mirror.status}
    requirement = db.query(Requirement).filter(Requirement.zentao_task_id == task_id).first()
    if requirement and (requirement.zentao_task_status_cache or "").strip():
        return {"status": requirement.zentao_task_status_cache}
    return None


def _restore_task_status(
    clients: list,
    login,
    task_id: int,
    original_task: dict,
) -> dict:
    """恢复工时编辑前的任务状态并回读确认。"""
    expected = effective_task_status(original_task)
    raw_expected = raw_task_status(original_task) or expected
    last_error: Optional[Exception] = None

    # pause/cancel/closed 优先使用刚完成工时编辑的本人网页凭据恢复，保证禅道
    # 动作人仍是本人；REST 账号只作为网页动作未生效时的兜底。
    web_action = {
        "pause": pause_task_via_web,
        "cancel": cancel_task_via_web,
        "closed": close_task_via_web,
    }.get(expected)
    if web_action is not None:
        try:
            web_action(login, task_id, comment="OmniQA 修改工时后恢复原任务状态")
            verified = _read_task_snapshot(clients, task_id)
            if verified is None or effective_task_status(verified) == expected:
                return verified or {"status": raw_expected}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("restore task %s status=%s via web failed: %s", task_id, expected, exc)

    for client in clients:
        try:
            current = client.get_task(task_id) or {}
            if effective_task_status(current) == expected:
                return current
            if expected == "pause":
                client.pause_task(task_id, comment="OmniQA 修改工时后恢复原任务状态")
            elif expected == "cancel":
                client.cancel_task(task_id, comment="OmniQA 修改工时后恢复原任务状态")
            elif expected == "closed":
                client.close_task(task_id, comment="OmniQA 修改工时后恢复原任务状态")
            else:
                client.update_task(task_id, {"status": raw_expected})
            current = client.get_task(task_id) or {}
            if effective_task_status(current) == expected:
                return current
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("restore task %s status=%s via REST failed: %s", task_id, expected, exc)

    detail = f"：{last_error}" if last_error else ""
    raise ZentaoWebSessionError(f"任务原状态“{expected}”恢复失败{detail}")


def _sync_task_snapshot(db: Session, task_id: int, task: dict, *, action: str) -> None:
    """同步最终状态/工时摘要到本地缓存，并通知已打开的工作台刷新。"""
    status = effective_task_status(task)
    mirror = db.query(ZentaoTaskMirror).filter(ZentaoTaskMirror.task_id == task_id).first()
    if mirror is not None:
        if status:
            mirror.status = status
        for attr, key in (("estimate", "estimate"), ("consumed", "consumed"), ("left", "left")):
            if task.get(key) is None:
                continue
            try:
                setattr(mirror, attr, float(task[key]))
            except (TypeError, ValueError):
                pass
        mirror.synced_at = local_now()
    requirement = db.query(Requirement).filter(Requirement.zentao_task_id == task_id).first()
    if requirement is not None and status:
        requirement.zentao_task_status_cache = status
    db.commit()
    sse_publish(
        "zentao_task_changed",
        {
            "task_id": task_id,
            "status": status,
            "action": action,
            "source": "effort_modal",
            "requirement_id": requirement.id if requirement is not None else None,
        },
        channels=["global"],
    )


def list_efforts_for_user(db: Session, task_id: int, current_user: User) -> dict:
    """任务的工时记录列表。查看优先走本人网页凭据，没有则回退系统账号（只读，
    不涉及归属问题）；编辑权限按「记录人 == 本人禅道账号」标注。"""
    my_account = (current_user.zentao_account or "").strip().lower()
    self_login = get_user_zentao_web_login(current_user.id, db)
    system_login = get_system_zentao_web_login(db)
    last_err: Optional[Exception] = None
    for login in (self_login, system_login):
        if login is None:
            continue
        try:
            efforts = list_task_efforts_via_web(login, task_id)
        except Exception as exc:  # noqa: BLE001 — 换下一个凭据
            last_err = exc
            logger.warning("list efforts task %s via %s failed: %s", task_id, login.account, exc)
            continue
        for e in efforts:
            e["can_edit"] = bool(my_account) and e.get("account", "").strip().lower() == my_account
        return {
            "ok": True,
            "efforts": efforts,
            # 编辑必须用本人凭据（工时归属），没绑定就整体只读
            "can_edit_any": self_login is not None and bool(my_account),
        }
    detail = f"：{last_err}" if last_err else "（没有可用的禅道网页登录凭据）"
    return {"ok": False, "efforts": [], "can_edit_any": False, "error": f"拉取工时记录失败{detail}"}


def edit_effort_for_user(
    db: Session,
    task_id: int,
    effort_id: int,
    current_user: User,
    *,
    new_date: str,
    consumed: float,
    work: Optional[str] = None,
) -> dict:
    """修改一条工时记录（仅限本人的记录，必须用本人网页凭据——保持工时归属）。"""
    from fastapi import HTTPException

    if not (0 < consumed <= 999):
        raise HTTPException(status_code=400, detail="工时需在 0~999 小时之间")
    try:
        datetime.strptime(new_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式需为 YYYY-MM-DD")
    login = get_user_zentao_web_login(current_user.id, db)
    if login is None:
        raise HTTPException(status_code=400, detail="未绑定禅道网页登录凭据，无法修改工时记录（工时须以本人身份提交）")
    my_account = (current_user.zentao_account or "").strip().lower()
    try:
        efforts = list_task_efforts_via_web(login, task_id)
    except ZentaoWebSessionError as exc:
        raise HTTPException(status_code=502, detail=f"拉取工时记录失败：{exc}")
    row = next((e for e in efforts if e["id"] == int(effort_id)), None)
    if row is None:
        raise HTTPException(status_code=404, detail="工时记录不存在（可能已被删除）")
    if (row.get("account") or "").strip().lower() != my_account:
        raise HTTPException(status_code=403, detail="只能修改本人的工时记录")
    clients = _task_clients(db, current_user)
    original_task = _read_task_snapshot(clients, task_id) or _cached_task_snapshot(db, task_id)
    if original_task is None or not effective_task_status(original_task):
        raise HTTPException(
            status_code=502,
            detail="无法读取任务原状态，已取消修改工时，避免意外改变任务状态",
        )
    original_status = effective_task_status(original_task)
    try:
        efforts = edit_task_effort_via_web(
            login,
            task_id,
            effort_id,
            date=new_date,
            consumed=round(float(consumed), 2),
            left=row.get("left") or 0.0,
            work=(work if work is not None else row.get("work") or ""),
        )
    except ZentaoWebSessionError as exc:
        raise HTTPException(status_code=502, detail=f"修改工时记录失败：{exc}")
    final_task = _read_task_snapshot(clients, task_id)
    status_restored = False
    if final_task is None or effective_task_status(final_task) != original_status:
        try:
            final_task = _restore_task_status(clients, login, task_id, original_task)
            status_restored = True
        except ZentaoWebSessionError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"工时记录已修改，但任务状态恢复失败：{exc}；请立即检查禅道任务状态",
            )
    _sync_task_snapshot(db, task_id, final_task or original_task, action="edit_effort")
    for e in efforts:
        e["can_edit"] = (e.get("account") or "").strip().lower() == my_account
    return {
        "ok": True,
        "efforts": efforts,
        "can_edit_any": True,
        "task_status": original_status,
        "status_restored": status_restored,
    }


def delete_effort_for_user(
    db: Session,
    task_id: int,
    effort_id: int,
    current_user: User,
) -> dict:
    """删除本人的一条工时记录；禅道回读确认记录消失后才返回成功。"""
    from fastapi import HTTPException

    login = get_user_zentao_web_login(current_user.id, db)
    if login is None:
        raise HTTPException(status_code=400, detail="未绑定禅道网页登录凭据，无法删除工时记录（必须以本人身份同步禅道）")
    my_account = (current_user.zentao_account or "").strip().lower()
    try:
        efforts = list_task_efforts_via_web(login, task_id)
    except ZentaoWebSessionError as exc:
        raise HTTPException(status_code=502, detail=f"拉取工时记录失败：{exc}")
    row = next((e for e in efforts if e["id"] == int(effort_id)), None)
    if row is None:
        raise HTTPException(status_code=404, detail="工时记录不存在（可能已被删除）")
    if (row.get("account") or "").strip().lower() != my_account:
        raise HTTPException(status_code=403, detail="只能删除本人的工时记录")
    try:
        efforts = delete_task_effort_via_web(login, task_id, effort_id)
    except ZentaoWebSessionError as exc:
        raise HTTPException(status_code=502, detail=f"删除工时记录失败，禅道未确认删除：{exc}")
    if any(e["id"] == int(effort_id) for e in efforts):
        # 双重保护：底层本应已拦截，服务层仍不允许把残留记录当作成功。
        raise HTTPException(status_code=502, detail="删除工时记录失败：禅道回读后记录仍然存在")
    for e in efforts:
        e["can_edit"] = (e.get("account") or "").strip().lower() == my_account
    return {"ok": True, "efforts": efforts, "can_edit_any": True}


__all__ = [
    "AUTO_NOTE",
    "merge_extra_hours",
    "submit_day_efforts",
    "list_efforts_for_user",
    "edit_effort_for_user",
    "delete_effort_for_user",
]
