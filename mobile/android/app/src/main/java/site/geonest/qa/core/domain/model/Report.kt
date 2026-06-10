package site.geonest.qa.core.domain.model

/** 报表概览指标 + 治理看板 KPI + 趋势 / 团队对比 / 各版本 Bug 分布。 */
data class ReportData(
    val executedRequirements: Int = 0,
    val createdCases: Int = 0,
    val createdBugs: Int = 0,
    val retestedReqs: Int = 0,
    val closedBugs: Int = 0,
    val governance: Governance = Governance(),
    val trend: List<TrendPoint> = emptyList(),
    val team: List<TeamMember> = emptyList(),
    val versionBugs: List<VersionBug> = emptyList(),
)

/** 单日趋势点。 */
data class TrendPoint(
    val date: String,
    val executedRequirements: Int,
    val createdCases: Int,
    val createdBugs: Int,
    val retestedReqs: Int,
    val closedBugs: Int,
)

/** 团队成员产出（全员模式）。 */
data class TeamMember(
    val username: String,
    val executedRequirements: Int,
    val createdCases: Int,
    val createdBugs: Int,
    val retestedReqs: Int,
    val closedBugs: Int,
    val processedFeedbacks: Int,
)

/** 某发包（小版本）检出 Bug 数。 */
data class VersionBug(
    val majorName: String,
    val minorName: String,
    val bugCount: Int,
)

data class Governance(
    val overdueRequirements: Int = 0,
    val overdueFeedbacks: Int = 0,
    val unassignedBugs: Int = 0,
    val overdueBugs: Int = 0,
    val staleBugs: Int = 0,
    val assignedNoProgressBugs: Int = 0,
    val feedbackToBugRatio: Double = 0.0,
)
