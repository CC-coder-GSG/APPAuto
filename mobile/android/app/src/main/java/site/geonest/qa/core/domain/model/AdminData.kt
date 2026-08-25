package site.geonest.qa.core.domain.model

/** 数据管理台总览。 */
data class DataOverview(
    val users: List<AdminUser> = emptyList(),
    val requirements: List<AdminReqItem> = emptyList(),
    val userCount: Int = 0,
    val versionCount: Int = 0,
    val requirementCount: Int = 0,
    val bugCount: Int = 0,
)

data class AdminUser(
    val id: Int,
    val username: String,
    val displayName: String,
    val role: String,
    val isTeamMember: Boolean,
    val allowedTabs: List<String>,
) {
    val isAdmin: Boolean get() = role == "admin"
}

data class AdminReqItem(
    val id: Int,
    val zentaoReqId: String,
    val title: String,
    val majorVersionId: Int?,
)

/** 页面权限 tab 键 → 中文名（对齐网页 TAB_PERMISSION_OPTIONS）。 */
val TAB_PERMISSION_OPTIONS: List<Pair<String, String>> = listOf(
    "assign" to "任务分配台",
    "mine" to "我的工作台",
    "task-board" to "任务看板",
    "feedback" to "反馈记录与处理",
    "retest" to "复测工作台",
    "overall-test" to "整体测试",
    "field-test" to "外业测试",
    "build-records" to "构建记录",
    "testcase-center" to "用例中心",
    "zentao-sync" to "禅道同步中心",
    "report" to "报表中心",
    "activity" to "活动中心",
    "data" to "数据管理台",
    "dispatch" to "BUG特派",
    "zentao-ai" to "禅道AI用例生成",
    "cad-test" to "CAD测试统计",
)
