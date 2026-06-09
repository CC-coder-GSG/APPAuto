package site.geonest.qa.core.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.AssignProgress
import site.geonest.qa.core.domain.model.AssignRequirement
import site.geonest.qa.core.domain.model.LinkOption
import site.geonest.qa.core.domain.model.MajorSummary
import site.geonest.qa.core.domain.model.OwnerProgress
import site.geonest.qa.core.domain.model.ProgressReq
import site.geonest.qa.core.domain.model.ProgressSummary
import site.geonest.qa.core.domain.model.RetestPending
import site.geonest.qa.core.domain.model.UserOption
import site.geonest.qa.core.network.QaApi
import site.geonest.qa.core.network.dto.AssignItem
import site.geonest.qa.core.network.dto.AssignPublishRequest
import site.geonest.qa.core.network.dto.LinkMajorRequest
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 任务分配台仓储（管理员）：用户列表、需求分配、进行状态、关联需求。
 * 业务逻辑层，无 Android 依赖——未来 KMP 共享候选。
 */
@Singleton
class AssignRepository @Inject constructor(
    private val api: QaApi,
    private val json: Json,
) {
    suspend fun users(): ApiResult<List<UserOption>> = runCatchingApi {
        api.users().map { UserOption(it.id, it.displayName ?: it.username.orEmpty()) }
    }

    suspend fun requirements(majorId: Int?, softwareId: Int?): ApiResult<List<AssignRequirement>> = runCatchingApi {
        api.adminReqList(majorId, softwareId).map {
            AssignRequirement(
                id = it.id,
                zentaoReqId = it.zentaoReqId.orEmpty(),
                title = it.title.orEmpty(),
                ownerId = it.ownerId,
                caseCompleted = it.caseCompleted,
                testCompleted = it.testCompleted,
                majorVersionName = it.majorVersionName.orEmpty(),
            )
        }
    }

    /** assignments: reqId -> ownerId(0/null=未分配)。 */
    suspend fun publish(majorId: Int, assignments: Map<Int, Int>): ApiResult<Unit> = runCatchingApi {
        api.assignAndPublish(
            AssignPublishRequest(
                majorVersionId = majorId,
                assignments = assignments.map { (reqId, ownerId) -> AssignItem(reqId, ownerId.takeIf { it > 0 }) },
            ),
        )
        Unit
    }

    suspend fun syncZentao(majorId: Int): ApiResult<String> = runCatchingApi {
        val r = api.syncZentaoRequirements(majorId)
        "禅道同步完成：远端 ${r.remoteTotal}，新增 ${r.created}，更新 ${r.updated}"
    }

    suspend fun progress(majorId: Int?, softwareId: Int?): ApiResult<AssignProgress> = runCatchingApi {
        val d = api.adminProgress(majorId, softwareId)
        AssignProgress(
            summary = d.summary.let {
                ProgressSummary(it.owners, it.requirements, it.caseDone, it.casePending, it.testDone, it.testPending, it.retestPendingTotal)
            },
            owners = d.owners.map { o ->
                OwnerProgress(
                    ownerId = o.ownerId,
                    ownerName = o.ownerName.orEmpty(),
                    majorSummaries = o.majorSummaries.map { MajorSummary(it.majorVersionName.orEmpty(), it.totalRequirements, it.casePending, it.testPending) },
                    requirements = o.requirements.map {
                        ProgressReq(it.id, it.zentaoReqId.orEmpty(), it.title.orEmpty(), it.majorVersionName.orEmpty(), it.caseCompleted, it.testCompleted, it.caseCount, it.bugCount)
                    },
                )
            },
            retestPending = d.retestPendingByMajor.map { RetestPending(it.majorVersionName.orEmpty(), it.pendingRetestCount) },
        )
    }

    suspend fun linkOptions(sourceMajorId: Int, targetMajorId: Int): ApiResult<List<LinkOption>> = runCatchingApi {
        api.linkOptions(sourceMajorId, targetMajorId).map {
            LinkOption(it.id, it.zentaoReqId.orEmpty(), it.title.orEmpty(), it.ownerName ?: "未分配", it.caseCount, it.alreadyLinked)
        }
    }

    suspend fun linkMajor(targetMajorId: Int, sourceMajorId: Int, reqIds: List<Int>, copyStatus: Boolean): ApiResult<String> = runCatchingApi {
        val r = api.linkMajor(LinkMajorRequest(targetMajorId, sourceMajorId, reqIds, copyStatus))
        r.message ?: "关联成功：新增 ${r.createdCount}，跳过 ${r.skippedCount}"
    }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        val msg = when (e.code()) {
            400 -> errorDetail(e) ?: "操作无效"
            401 -> "登录已失效，请重新登录"
            403 -> "需要「任务分配」权限（管理员）"
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
