"""
System-level Zentao client provider.

Jenkins build-report 这种匿名/系统调用没有用户 JWT，必须借用某个真实禅道账号
的绑定才能调用禅道写接口。本模块封装"借谁的 token"的策略：

优先级：
  1. 用户名 == "chenwenbo"
  2. 任意管理员账号
  3. 任意已配置有效绑定的账号

任何一步拿不到 token 就退到下一个。全部失败返回 None — 调用方应当把这种情况
当作"无法写禅道"，记录 push_status='no_binding' 后继续，而不要让 Jenkins 入库失败。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models import User, UserRole
from app.models.user_zentao_binding import UserZentaoBinding
from app.services.zentao_auth_service import get_valid_token
from app.services.zentao_client_service import ZentaoClient

logger = logging.getLogger(__name__)

PREFERRED_USERNAMES = ["chenwenbo"]


def _try_user(user_id: int, db: Session) -> Optional[ZentaoClient]:
    binding = (
        db.query(UserZentaoBinding)
        .filter(UserZentaoBinding.user_id == user_id)
        .first()
    )
    if not binding or not binding.base_url:
        return None
    token = get_valid_token(user_id, db)
    if not token:
        return None
    return ZentaoClient(base_url=binding.base_url, token=token)


def get_system_zentao_client(db: Session) -> Optional[ZentaoClient]:
    # 1) Try the preferred usernames first
    for username in PREFERRED_USERNAMES:
        user = db.query(User).filter(User.username == username).first()
        if user:
            client = _try_user(user.id, db)
            if client:
                return client

    # 2) Any admin with a binding
    admin_ids = [
        uid for (uid,) in db.query(User.id).filter(User.role == UserRole.ADMIN).all()
    ]
    for uid in admin_ids:
        client = _try_user(uid, db)
        if client:
            return client

    # 3) Any user with a valid binding
    candidate_ids = [
        uid for (uid,) in db.query(UserZentaoBinding.user_id).all()
    ]
    for uid in candidate_ids:
        client = _try_user(uid, db)
        if client:
            return client

    logger.warning("get_system_zentao_client: no usable Zentao binding found")
    return None


__all__ = ["get_system_zentao_client"]
