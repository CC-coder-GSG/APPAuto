package site.geonest.qa.core.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.DispatchedBug
import site.geonest.qa.core.domain.model.WbBug
import site.geonest.qa.core.domain.model.WbTestCase
import site.geonest.qa.core.domain.model.WorkbenchMode
import site.geonest.qa.core.domain.model.WorkbenchRequirement
import site.geonest.qa.core.network.QaApi
import site.geonest.qa.core.network.dto.BugDto
import site.geonest.qa.core.network.dto.DispatchedBugDto
import site.geonest.qa.core.network.dto.PatchStatusRequest
import site.geonest.qa.core.network.dto.TestCaseDto
import site.geonest.qa.core.network.dto.TestExecutionRequest
import site.geonest.qa.core.network.dto.TestNotesRequest
import site.geonest.qa.core.network.dto.WorkbenchRequirementDto
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 需求工作台仓储：对齐网页 /workbench/mine 的能力——
 * 拉取我的需求、用例完成、测试完成（执行记录）、测试要点、指派 Bug。
 * 业务逻辑层，无 Android 依赖——未来 KMP 共享候选。
 */
@Singleton
class WorkbenchRepository @Inject constructor(
    private val api: QaApi,
    private val json: Json,
) {
    suspend fun myWorkbench(
        mode: WorkbenchMode,
        majorVersionId: Int?,
        softwareId: Int?,
    ): ApiResult<List<WorkbenchRequirement>> = runCatchingApi {
        api.workbenchMine(
            mode = mode.api,
            majorVersionId = if (mode == WorkbenchMode.VERSION) majorVersionId else null,
            softwareId = softwareId,
        ).map { it.toDomain() }
    }

    suspend fun dispatchedToMe(majorVersionId: Int): ApiResult<List<DispatchedBug>> = runCatchingApi {
        api.dispatchedToMe(majorVersionId).map { it.toDomain() }
    }

    /** 用例完成开关（PATCH status）。 */
    suspend fun setCaseCompleted(reqId: Int, completed: Boolean): ApiResult<Unit> = runCatchingApi {
        api.patchRequirementStatus(reqId, PatchStatusRequest(caseCompleted = completed)); Unit
    }

    /** 取消测试完成（PATCH status，test_completed=false）。勾选完成走 submitTestExecution。 */
    suspend fun clearTestCompleted(reqId: Int): ApiResult<Unit> = runCatchingApi {
        api.patchRequirementStatus(reqId, PatchStatusRequest(testCompleted = false)); Unit
    }

    /** 提交测试执行——即标记测试完成。 */
    suspend fun submitTestExecution(
        reqId: Int,
        minorVersionId: Int,
        resultStatus: String,
        notes: String?,
    ): ApiResult<Unit> = runCatchingApi {
        api.submitTestExecution(
            reqId,
            TestExecutionRequest(
                minorVersionId = minorVersionId,
                resultStatus = resultStatus,
                testCompleted = true,
                notes = notes?.ifBlank { null },
            ),
        ); Unit
    }

    suspend fun saveTestNotes(reqId: Int, notes: String?): ApiResult<Unit> = runCatchingApi {
        api.updateTestNotes(reqId, TestNotesRequest(testNotes = notes?.ifBlank { null })); Unit
    }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        val msg = when (e.code()) {
            400 -> errorDetail(e) ?: "操作无效"
            401 -> "登录已失效，请重新登录"
            403 -> "无权限操作该需求"
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

private fun WorkbenchRequirementDto.toDomain() = WorkbenchRequirement(
    id = id,
    zentaoReqId = zentaoReqId.orEmpty(),
    title = title.orEmpty(),
    caseCompleted = caseCompleted,
    testCompleted = testCompleted,
    majorVersionId = majorVersionId,
    majorVersionName = majorVersionName.orEmpty(),
    testNotes = testNotes,
    testNotesUpdatedAt = testNotesUpdatedAt,
    testNotesUpdatedByName = testNotesUpdatedByName,
    autoLinkedCaseCount = autoLinkedCaseCount,
    autoLinkedBugCount = autoLinkedBugCount,
    testCases = testCases.map { it.toDomain() },
    freeBugs = freeBugs.map { it.toDomain() },
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

private fun DispatchedBugDto.toDomain() = DispatchedBug(
    id = id,
    bugId = bugId.orEmpty(),
    zentaoBugId = zentaoBugId,
    closed = closed,
    reqTitle = reqTitle.orEmpty(),
    testDone = testDone,
    resolution = resolution ?: "fixed",
    newlyFoundBugId = newlyFoundBugId,
)
