from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationFailed
from app.models import FeatureTreeMark, FeatureTreeNode, SoftwareProduct, User
from app.services.sse_service import sse_publish
from app.utils.time_utils import local_now

# 测试人配色板：按 user_id 取模确定性配色，保证"每人不同颜色"且全局稳定，
# 前端 weave 图例用同一组色值（见 frontend/js/tabs/feature-tree.js）。
MARK_PALETTE = [
    "#5b8cff", "#8b5cf6", "#d946a0", "#f59e0b", "#10b981", "#0ea5e9",
    "#ef4444", "#14b8a6", "#a855f7", "#f97316", "#22c55e", "#ec4899",
]


def user_color(user_id: int) -> str:
    return MARK_PALETTE[(user_id or 0) % len(MARK_PALETTE)]


class FeatureTreeService:
    def __init__(self, db: Session):
        self.db = db

    # ── 读取 ────────────────────────────────────────────────
    def get_tree(self, software_id: int, version_id: Optional[int] = None) -> dict[str, Any]:
        software = self.db.query(SoftwareProduct).filter(SoftwareProduct.id == software_id).first()
        if not software:
            raise ValidationFailed("软件不存在")

        root = self._ensure_root(software)
        nodes = (
            self.db.query(FeatureTreeNode)
            .filter(FeatureTreeNode.software_id == software_id)
            .order_by(FeatureTreeNode.sort_order, FeatureTreeNode.id)
            .all()
        )

        marks_by_node: dict[int, list[dict[str, Any]]] = {}
        if version_id:
            mark_rows = (
                self.db.query(FeatureTreeMark)
                .filter(FeatureTreeMark.version_id == version_id)
                .all()
            )
            node_ids = {n.id for n in nodes}
            mark_rows = [m for m in mark_rows if m.node_id in node_ids]
            user_ids = {m.user_id for m in mark_rows}
            users = (
                {u.id: u for u in self.db.query(User).filter(User.id.in_(user_ids)).all()}
                if user_ids else {}
            )
            for m in mark_rows:
                u = users.get(m.user_id)
                marks_by_node.setdefault(m.node_id, []).append({
                    "user_id": m.user_id,
                    "display_name": (u.display_name or u.username) if u else f"#{m.user_id}",
                    "color": user_color(m.user_id),
                    "comment_html": m.comment_html or "",
                    "is_auto": bool(m.is_auto),
                    "updated_at": m.updated_at.isoformat() if m.updated_at else None,
                })

        by_id = {n.id: self._serialize(n, marks_by_node.get(n.id, [])) for n in nodes}
        for n in nodes:
            if n.parent_id and n.parent_id in by_id:
                by_id[n.parent_id]["children"].append(by_id[n.id])

        return {
            "software_id": software_id,
            "software_name": software.name,
            "version_id": version_id,
            "tree": by_id.get(root.id),
        }

    def _serialize(self, n: FeatureTreeNode, marks: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": n.id,
            "parent_id": n.parent_id,
            "name": n.name,
            "is_root": n.is_root,
            "note_html": n.note_html or "",
            "has_note": bool((n.note_html or "").strip()),
            "note_updated_at": n.note_updated_at.isoformat() if n.note_updated_at else None,
            "marks": marks,
            "children": [],
        }

    def _ensure_root(self, software: SoftwareProduct) -> FeatureTreeNode:
        root = (
            self.db.query(FeatureTreeNode)
            .filter(FeatureTreeNode.software_id == software.id, FeatureTreeNode.is_root.is_(True))
            .first()
        )
        if root:
            return root
        root = FeatureTreeNode(
            software_id=software.id, parent_id=None, name=software.name, is_root=True,
        )
        self.db.add(root)
        self.db.commit()
        self.db.refresh(root)
        return root

    def _get_node(self, node_id: int) -> FeatureTreeNode:
        node = self.db.query(FeatureTreeNode).filter(FeatureTreeNode.id == node_id).first()
        if not node:
            raise ValidationFailed("节点不存在")
        return node

    # ── 写入 ────────────────────────────────────────────────
    def create_node(self, software_id: int, parent_id: int, name: str, user: User) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValidationFailed("分支名称不能为空")
        parent = self._get_node(parent_id)
        if parent.software_id != software_id:
            raise ValidationFailed("父节点不属于该软件")
        max_order = (
            self.db.query(FeatureTreeNode.sort_order)
            .filter(FeatureTreeNode.parent_id == parent_id)
            .order_by(FeatureTreeNode.sort_order.desc())
            .first()
        )
        node = FeatureTreeNode(
            software_id=software_id, parent_id=parent_id, name=name,
            is_root=False, sort_order=((max_order[0] + 1) if max_order else 0),
            created_by=user.id,
        )
        self.db.add(node)
        self.db.flush()
        # 新增的子分支默认未标记 → 让原本"已全标自动汇总"的父链标记失效
        for v, u in self._marks_vu([parent_id]):
            self._propagate_auto(parent_id, v, u)
        self.db.commit()
        self.db.refresh(node)
        self._notify(software_id)
        return {"id": node.id, "name": node.name, "parent_id": node.parent_id}

    def update_node(self, node_id: int, user: User, *, name: Optional[str] = None,
                    note_html: Optional[str] = None) -> dict[str, Any]:
        node = self._get_node(node_id)
        if name is not None:
            name = name.strip()
            if not name:
                raise ValidationFailed("名称不能为空")
            node.name = name
        if note_html is not None:
            node.note_html = note_html
            node.note_updated_by = user.id
            node.note_updated_at = local_now()
        self.db.commit()
        self._notify(node.software_id)
        return {"id": node.id}

    def delete_node(self, node_id: int) -> dict[str, Any]:
        node = self._get_node(node_id)
        if node.is_root:
            raise ValidationFailed("根节点不可删除")
        software_id = node.software_id
        parent_id = node.parent_id
        self.db.delete(node)  # 子树与标记经 cascade 级联删除
        self.db.flush()
        # 子树消失后父链可能"变全标"（少了未标的子）或"残留失效自动标记"，重算
        if parent_id is not None:
            remaining = [c.id for c in self._children(parent_id)]
            for v, u in self._marks_vu(remaining + [parent_id]):
                self._propagate_auto(parent_id, v, u)
        self.db.commit()
        self._notify(software_id)
        return {"deleted": node_id}

    def set_mark(self, node_id: int, version_id: int, comment_html: Optional[str], user: User) -> dict[str, Any]:
        node = self._get_node(node_id)
        if not version_id:
            raise ValidationFailed("缺少最终测试版本")
        if self._children(node_id):
            raise ValidationFailed("含子分支的节点会在其所有子分支都标记后自动汇总，无法手动标记")
        mark = self._mark_row(node_id, version_id, user.id)
        if mark:
            if mark.is_auto:
                # 理论上叶子不会有自动标记，防御性处理：转为手动
                mark.is_auto = False
            mark.comment_html = comment_html
            mark.updated_at = local_now()
        else:
            mark = FeatureTreeMark(
                node_id=node_id, version_id=version_id, user_id=user.id,
                comment_html=comment_html, is_auto=False,
            )
            self.db.add(mark)
        self.db.flush()
        self._propagate_auto(node_id, version_id, user.id)  # 向上汇总
        self.db.commit()
        self._notify(node.software_id)
        return {"node_id": node_id, "user_id": user.id, "color": user_color(user.id)}

    def delete_mark(self, node_id: int, version_id: int, user: User) -> dict[str, Any]:
        node = self._get_node(node_id)
        mark = self._mark_row(node_id, version_id, user.id)
        if mark:
            if mark.is_auto:
                raise ValidationFailed("该标记由子分支自动汇总产生，请取消对应子分支的标记")
            self.db.delete(mark)
            self.db.flush()
            self._propagate_auto(node_id, version_id, user.id)  # 取消后向上撤销自动汇总
            self.db.commit()
            self._notify(node.software_id)
        return {"node_id": node_id, "user_id": user.id}

    # ── 自动汇总（子节点全标 → 父节点自动标记，逐级向上）──────
    def _children(self, node_id: int) -> list[FeatureTreeNode]:
        return self.db.query(FeatureTreeNode).filter(FeatureTreeNode.parent_id == node_id).all()

    def _mark_row(self, node_id: int, version_id: int, user_id: int) -> Optional[FeatureTreeMark]:
        return (
            self.db.query(FeatureTreeMark)
            .filter(
                FeatureTreeMark.node_id == node_id,
                FeatureTreeMark.version_id == version_id,
                FeatureTreeMark.user_id == user_id,
            )
            .first()
        )

    def _has_mark(self, node_id: int, version_id: int, user_id: int) -> bool:
        return self._mark_row(node_id, version_id, user_id) is not None

    def _marks_vu(self, node_ids: list[int]) -> set[tuple[int, int]]:
        """收集给定节点上出现过的 (version_id, user_id) 组合，作为重算候选。"""
        if not node_ids:
            return set()
        rows = (
            self.db.query(FeatureTreeMark.version_id, FeatureTreeMark.user_id)
            .filter(FeatureTreeMark.node_id.in_(node_ids))
            .distinct()
            .all()
        )
        return {(r[0], r[1]) for r in rows}

    def _propagate_auto(self, start_node_id: int, version_id: int, user_id: int) -> None:
        """从 start_node 沿父链逐级评估某 (version,user) 的自动标记：
        - 有子节点且子节点全部已被该用户标记 → 该节点应有自动标记（缺则补）
        - 否则该节点不应有自动标记（有则撤；手动标记不动）
        """
        cur_id: Optional[int] = start_node_id
        while cur_id is not None:
            cur = self.db.query(FeatureTreeNode).filter(FeatureTreeNode.id == cur_id).first()
            if cur is None:
                break
            children = self._children(cur.id)
            should = bool(children) and all(self._has_mark(c.id, version_id, user_id) for c in children)
            row = self._mark_row(cur.id, version_id, user_id)
            if should and row is None:
                self.db.add(FeatureTreeMark(
                    node_id=cur.id, version_id=version_id, user_id=user_id, is_auto=True,
                ))
                self.db.flush()
            elif not should and row is not None and row.is_auto:
                self.db.delete(row)
                self.db.flush()
            cur_id = cur.parent_id

    def _notify(self, software_id: int) -> None:
        sse_publish("feature_tree_updated", {"software_id": software_id})


__all__ = ["FeatureTreeService", "user_color", "MARK_PALETTE"]
