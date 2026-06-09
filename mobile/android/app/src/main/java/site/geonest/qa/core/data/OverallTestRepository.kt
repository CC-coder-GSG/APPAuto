package site.geonest.qa.core.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.OverallBug
import site.geonest.qa.core.domain.model.OverallOverview
import site.geonest.qa.core.domain.model.OverallStats
import site.geonest.qa.core.network.QaApi
import site.geonest.qa.core.network.dto.OverallBugDto
import site.geonest.qa.core.network.dto.OverallResultRequest
import site.geonest.qa.core.network.dto.OverallStatsDto
import site.geonest.qa.core.network.dto.ZentaoCloseRequest
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 测试工作台（全盘）仓储：拉取全盘 Bug 概览、提交修复结果/闭环。
 * 业务逻辑层，无 Android 依赖——未来 KMP 共享候选。
 */
@Singleton
class OverallTestRepository @Inject constructor(
    private val api: QaApi,
    private val json: Json,
) {
    suspend fun overview(
        majorVersionId: Int,
        softwareId: Int?,
        statuses: Set<String>,
        keyword: String?,
    ): ApiResult<OverallOverview> = runCatchingApi {
        val dto = api.overallOverview(
            majorVersionId = majorVersionId,
            softwareId = softwareId,
            statuses = statuses.takeIf { it.isNotEmpty() }?.joinToString(","),
            keyword = keyword?.ifBlank { null },
        )
        OverallOverview(
            stats = dto.stats.toDomain(),
            filteredStats = dto.filteredStats.toDomain(),
            // 禅道已删除的 Bug 不在移动端列出，避免误操作。
            bugs = dto.bugPool.filterNot { it.zentaoDeleted }.map { it.toDomain() },
        )
    }

    /**
     * 提交修复结果/闭环。若勾选闭环且为禅道 Bug，先同步关闭禅道（写入闭环说明），再落本地结果。
     * 取消闭环的禅道重新激活流程较重，移动端暂不支持，交由电脑端处理。
     */
    suspend fun submitResult(
        bugId: Int,
        minorVersionId: Int,
        testDone: Boolean,
        resolution: String,
        comment: String,
        zentaoBugId: String?,
    ): ApiResult<Unit> = runCatchingApi {
        if (testDone && !zentaoBugId.isNullOrBlank()) {
            zentaoBugId.toIntOrNull()?.let { zid ->
                api.closeZentaoBug(zid, ZentaoCloseRequest(comment = comment.ifBlank { null }))
            }
        }
        api.submitOverallResult(
            bugId,
            OverallResultRequest(
                minorVersionId = minorVersionId,
                testDone = testDone,
                resolution = resolution,
            ),
        )
        Unit
    }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        val msg = when (e.code()) {
            400 -> errorDetail(e) ?: "操作无效"
            401 -> "登录已失效，请重新登录"
            403 -> "无权限"
            404 -> "Bug 不存在"
            else -> errorDetail(e) ?: "服务异常（${e.code()}）"
        }
        ApiResult.Error(msg, e.code())
    } catch (e: IOException) {
        ApiResult.Error("网络连接失败，请检查网络后重试")
    } catch (e: Exception) {
        ApiResult.Error(e.message ?: "未知错误")
    }

    private fun errorDetail(e: HttpException): String? = try {
        e.response()?.errorBody()?.string()?.takeIf { it.isNotBlank() }?.let { body ->
            json.parseToJsonElement(body).jsonObject["detail"]?.jsonPrimitive?.content
        }
    } catch (_: Exception) {
        null
    }
}

private fun OverallStatsDto.toDomain() = OverallStats(total = total, closed = closed, pending = pending, readyRate = readyRate)

private fun OverallBugDto.toDomain() = OverallBug(
    id = id,
    bugId = bugId.orEmpty(),
    zentaoBugId = zentaoBugId,
    zentaoBugTitle = zentaoBugTitle.orEmpty(),
    zentaoLiveStatus = zentaoLiveStatus.orEmpty(),
    zentaoAssignedToName = zentaoAssignedToName.orEmpty(),
    zentaoDeleted = zentaoDeleted,
    requirementId = requirementId,
    closed = closed,
    myTestDone = myTestDone,
    myResolution = myResolution ?: "fixed",
    myComment = myComment.orEmpty(),
    dispatchedToName = dispatchedToName,
    isRetestFailed = isRetestFailed,
    effectiveStatus = effectiveStatus.orEmpty(),
    majorVersionNo = majorVersionNo.orEmpty(),
)
