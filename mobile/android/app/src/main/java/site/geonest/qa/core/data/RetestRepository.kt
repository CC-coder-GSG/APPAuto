package site.geonest.qa.core.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.RetestRequirement
import site.geonest.qa.core.domain.model.WbBug
import site.geonest.qa.core.domain.model.WbTestCase
import site.geonest.qa.core.network.QaApi
import site.geonest.qa.core.network.dto.BugDto
import site.geonest.qa.core.network.dto.RetestRequirementDto
import site.geonest.qa.core.network.dto.RetestSubmitRequest
import site.geonest.qa.core.network.dto.TestCaseDto
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 复测工作台仓储：拉取待我复测的需求、提交复测结论。
 * 业务逻辑层，无 Android 依赖——未来 KMP 共享候选。
 */
@Singleton
class RetestRepository @Inject constructor(
    private val api: QaApi,
    private val json: Json,
) {
    /**
     * 待复测需求。mode=all_pending 跨版本待办；mode=version 限定某大版本。
     * majorVersionId 仅在 version 模式必填；softwareId 作为可选过滤。
     */
    suspend fun workbench(
        mode: String = "all_pending",
        majorVersionId: Int? = null,
        softwareId: Int? = null,
    ): ApiResult<List<RetestRequirement>> = runCatchingApi {
        api.retestWorkbench(mode = mode, majorVersionId = majorVersionId, softwareId = softwareId).map { it.toDomain() }
    }

    /** 标记复测通过。后端会校验该需求是否仍有未闭环问题。 */
    suspend fun markPassed(reqId: Int): ApiResult<String> = submit(reqId, completed = true, passed = true)

    /** 打回（复测未通过）。后端要求至少存在一个未修好/未闭环 Bug 作为证据。 */
    suspend fun markFailed(reqId: Int): ApiResult<String> = submit(reqId, completed = true, passed = false)

    private suspend fun submit(reqId: Int, completed: Boolean, passed: Boolean?): ApiResult<String> =
        runCatchingApi {
            api.submitRetest(
                reqId,
                RetestSubmitRequest(retestCompleted = completed, retestPassed = passed),
            ).message ?: "已提交"
        }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        // 400 多为业务校验失败（打回需证据 / 通过需先闭环），后端 detail 即面向用户的提示，优先透传。
        val msg = when (e.code()) {
            400 -> errorDetail(e) ?: "操作无效"
            401 -> "登录已失效，请重新登录"
            403 -> "不能复测自己负责的需求"
            404 -> "需求不存在"
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

private fun RetestRequirementDto.toDomain() = RetestRequirement(
    id = id,
    zentaoReqId = zentaoReqId.orEmpty(),
    title = title.orEmpty(),
    majorVersionName = majorVersionName.orEmpty(),
    owner = owner.orEmpty(),
    retestCompleted = retestCompleted,
    retestPassed = retestPassed,
    retestedBy = retestedBy,
    testCases = testCases.map { it.toDomain() },
    freeBugs = freeBugs.map { it.toDomain() },
    retestBugs = retestBugs.map { it.toDomain() },
)

private fun TestCaseDto.toDomain() = WbTestCase(
    id = id,
    zentaoCaseId = zentaoCaseId.orEmpty(),
    zentaoCaseUrl = zentaoCaseUrl,
    bugs = bugs.map { it.toDomain() },
)

private fun BugDto.toDomain() = WbBug(
    id = id,
    bugId = bugId.orEmpty(),
    zentaoBugUrl = zentaoBugUrl,
    title = zentaoBugTitle.orEmpty(),
    foundVersion = foundMinorVersionNo,
    fixedVersion = fixedMinorVersionNo,
    dispatchedToName = dispatchedToName,
)
