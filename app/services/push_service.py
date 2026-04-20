from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.integrations.wecom import send_markdown
from app.models import BugTracking, Requirement
from app.services.requirement_service import RequirementService
from app.services.retest_service import RetestService
from app.services.overall_test_service import OverallTestService
from app.utils.time_utils import local_now


class PushService:
    def __init__(self, db: Session):
        self.db = db

    async def send_markdown(self, markdown: str) -> None:
        await send_markdown(markdown)

    async def push_case_progress(self, major_version_id: int, current_user) -> dict:
        md = RequirementService(self.db).build_case_progress_message(major_version_id, current_user)
        await self.send_markdown(md)
        return {"message": "Case progress pushed"}

    async def push_test_progress(self, major_version_id: int, minor_version_id: int, current_user) -> dict:
        md = RequirementService(self.db).build_test_progress_message(major_version_id, minor_version_id, current_user)
        await self.send_markdown(md)
        return {"message": "Test progress pushed"}

    async def push_retest_result(self, major_version_id: int, current_user) -> dict:
        md, count = RetestService(self.db).build_retest_push_message(major_version_id, current_user)
        await self.send_markdown(md)
        return {"message": "Retest results pushed", "count": count}

    async def push_overall_test_status(self, major_version_id: int, minor_version_id: int) -> dict:
        md, remaining = OverallTestService(self.db).build_overall_test_push_message(major_version_id, minor_version_id)
        await self.send_markdown(md)
        return {"message": "Overall-test status pushed", "remaining": remaining}

    # Legacy alias — routes pinned to the old name keep working.
    push_stage5_status = push_overall_test_status

    async def push_bug_dispatch_notice(self, bug_id: str, username: str) -> None:
        await self.send_markdown(f"📢 **Bug 特派专项通知**\n> 缺陷 **{bug_id}** 已被管理员特派给 @{username} 进行专项验证！请前往【我的工作台】顶部处理。")

    async def push_assignment_change(self, change_msgs: list[str]) -> None:
        if change_msgs:
            md = "### 需求负责人变更通知\n" + "\n".join(change_msgs) + "\n\n*提示：移交的需求已自动重置完成状态，请新负责人重新校验。*"
        else:
            md = "需求分配状态已整体更新发布"
        await self.send_markdown(md)

    async def push_feedback_assignment_notice(
        self,
        *,
        feedback_no: str,
        summary: str,
        major_version_no: str,
        minor_version_no: str,
        assignee_username: str,
    ) -> None:
        md = "\n".join(
            [
                "### 反馈指派通知",
                f"> 反馈编号：{feedback_no}",
                f"> 反馈概览：{summary}",
                f"> 反馈版本：{major_version_no} / {minor_version_no}",
                f"> 指派给：@{assignee_username}",
                "> 请及时进入【反馈记录与处理】查看并处理。",
            ]
        )
        await self.send_markdown(md)

    def build_daily_report_message(self) -> str:
        today = local_now().date()
        total = self.db.query(Requirement).count()
        tested = self.db.query(Requirement).filter(Requirement.test_completed.is_(True)).count()
        untested = total - tested
        new_bugs = self.db.query(BugTracking).filter(BugTracking.created_at >= datetime.combine(today, datetime.min.time())).count()
        return "\n".join([
            "## 每日18:00测试进度播报",
            f"- 总需求数: {total}",
            f"- 已测数: {tested}",
            f"- 未测数: {untested}",
            f"- 当日新增 b# Bug 数: {new_bugs}",
        ])
