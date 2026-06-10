package site.geonest.qa.core.data

import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.Governance
import site.geonest.qa.core.domain.model.ReportData
import site.geonest.qa.core.domain.model.TeamMember
import site.geonest.qa.core.domain.model.TrendPoint
import site.geonest.qa.core.domain.model.VersionBug
import site.geonest.qa.core.network.QaApi
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 报表中心仓储：概览指标 + 治理看板。无 Android 依赖——KMP 共享候选。
 */
@Singleton
class ReportRepository @Inject constructor(
    private val api: QaApi,
) {
    suspend fun report(
        startDate: String,
        endDate: String,
        userId: Int?,
        majorVersionId: Int?,
        softwareId: Int?,
    ): ApiResult<ReportData> = runCatchingApi {
        val summary = api.reportsSummary(startDate, endDate, userId, majorVersionId, softwareId)
        val overview = summary.overview
        val gov = api.reportsGovernance(startDate, endDate, majorVersionId, softwareId).kpis
        val versionBugs = api.reportsVersionBugs(majorVersionId)
        ReportData(
            executedRequirements = overview.executedRequirements,
            createdCases = overview.createdCases,
            createdBugs = overview.createdBugs,
            retestedReqs = overview.retestedReqs,
            closedBugs = overview.closedBugs,
            governance = Governance(
                overdueRequirements = gov.overdueRequirements,
                overdueFeedbacks = gov.overdueFeedbacks,
                unassignedBugs = gov.unassignedBugs,
                overdueBugs = gov.overdueBugs,
                staleBugs = gov.staleBugs,
                assignedNoProgressBugs = gov.assignedNoProgressBugs,
                feedbackToBugRatio = gov.feedbackToBugRatio,
            ),
            trend = summary.trend.map {
                TrendPoint(it.date, it.executedRequirements, it.createdCases, it.createdBugs, it.retestedReqs, it.closedBugs)
            },
            team = summary.teamComparison.map {
                TeamMember(it.username, it.executedRequirements, it.createdCases, it.createdBugs, it.retestedReqs, it.closedBugs, it.processedFeedbacks)
            },
            versionBugs = versionBugs.map { VersionBug(it.majorName, it.minorName, it.bugCount) },
        )
    }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        ApiResult.Error(
            when (e.code()) {
                401 -> "登录已失效，请重新登录"
                403 -> "无权限访问报表中心"
                else -> "服务异常（${e.code()}）"
            },
            e.code(),
        )
    } catch (e: IOException) {
        ApiResult.Error("网络连接失败，请检查网络后重试")
    } catch (e: Exception) {
        ApiResult.Error(e.message ?: "未知错误")
    }
}
