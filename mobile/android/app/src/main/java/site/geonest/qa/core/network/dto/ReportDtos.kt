package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** GET /reports/summary —— 概览 + 趋势 + 团队对比（全员模式才有 team_comparison）。 */
@Serializable
data class ReportSummaryDto(
    val overview: ReportOverviewDto = ReportOverviewDto(),
    val trend: List<TrendPointDto> = emptyList(),
    @SerialName("team_comparison") val teamComparison: List<TeamMemberDto> = emptyList(),
)

@Serializable
data class TrendPointDto(
    val date: String = "",
    @SerialName("executed_requirements") val executedRequirements: Int = 0,
    @SerialName("created_cases") val createdCases: Int = 0,
    @SerialName("created_bugs") val createdBugs: Int = 0,
    @SerialName("retested_reqs") val retestedReqs: Int = 0,
    @SerialName("closed_bugs") val closedBugs: Int = 0,
)

@Serializable
data class TeamMemberDto(
    val username: String = "",
    @SerialName("executed_requirements") val executedRequirements: Int = 0,
    @SerialName("created_cases") val createdCases: Int = 0,
    @SerialName("created_bugs") val createdBugs: Int = 0,
    @SerialName("retested_reqs") val retestedReqs: Int = 0,
    @SerialName("closed_bugs") val closedBugs: Int = 0,
    @SerialName("processed_feedbacks") val processedFeedbacks: Int = 0,
)

/** GET /reports/version-bugs —— 各发包 Bug 检出分布。 */
@Serializable
data class VersionBugDto(
    @SerialName("major_name") val majorName: String = "",
    @SerialName("minor_name") val minorName: String = "",
    @SerialName("bug_count") val bugCount: Int = 0,
)

@Serializable
data class ReportOverviewDto(
    @SerialName("executed_requirements") val executedRequirements: Int = 0,
    @SerialName("created_cases") val createdCases: Int = 0,
    @SerialName("created_bugs") val createdBugs: Int = 0,
    @SerialName("retested_reqs") val retestedReqs: Int = 0,
    @SerialName("closed_bugs") val closedBugs: Int = 0,
)

/** GET /reports/governance —— 治理看板 KPI。 */
@Serializable
data class GovernanceDto(
    val kpis: GovernanceKpisDto = GovernanceKpisDto(),
)

@Serializable
data class GovernanceKpisDto(
    @SerialName("overdue_requirements") val overdueRequirements: Int = 0,
    @SerialName("overdue_feedbacks") val overdueFeedbacks: Int = 0,
    @SerialName("unassigned_bugs") val unassignedBugs: Int = 0,
    @SerialName("overdue_bugs") val overdueBugs: Int = 0,
    @SerialName("stale_bugs") val staleBugs: Int = 0,
    @SerialName("assigned_no_progress_bugs") val assignedNoProgressBugs: Int = 0,
    @SerialName("feedback_to_bug_ratio") val feedbackToBugRatio: Double = 0.0,
)
