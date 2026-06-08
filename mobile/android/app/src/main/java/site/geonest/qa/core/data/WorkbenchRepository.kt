package site.geonest.qa.core.data

import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.WbBug
import site.geonest.qa.core.domain.model.WbTestCase
import site.geonest.qa.core.domain.model.WorkbenchRequirement
import site.geonest.qa.core.network.QaApi
import site.geonest.qa.core.network.dto.BugDto
import site.geonest.qa.core.network.dto.PatchStatusRequest
import site.geonest.qa.core.network.dto.TestCaseDto
import site.geonest.qa.core.network.dto.WorkbenchRequirementDto
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 工作台仓储：拉取我的需求、切换完成状态。
 * 业务逻辑层，无 Android 依赖——未来 KMP 共享候选。
 */
@Singleton
class WorkbenchRepository @Inject constructor(
    private val api: QaApi,
) {
    suspend fun myWorkbench(pendingOnly: Boolean = false): ApiResult<List<WorkbenchRequirement>> =
        runCatchingApi {
            api.myWorkbench(mode = if (pendingOnly) "all_pending" else null)
                .map { it.toDomain() }
        }

    suspend fun setCaseCompleted(reqId: Int, completed: Boolean): ApiResult<Boolean> =
        runCatchingApi {
            api.patchRequirementStatus(reqId, PatchStatusRequest(caseCompleted = completed)).caseCompleted
        }

    suspend fun setTestCompleted(reqId: Int, completed: Boolean): ApiResult<Boolean> =
        runCatchingApi {
            api.patchRequirementStatus(reqId, PatchStatusRequest(testCompleted = completed)).testCompleted
        }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        val msg = when (e.code()) {
            401 -> "登录已失效，请重新登录"
            403 -> "无权限操作该需求"
            404 -> "需求不存在"
            else -> "服务异常（${e.code()}）"
        }
        ApiResult.Error(msg, e.code())
    } catch (e: IOException) {
        ApiResult.Error("网络连接失败，请检查网络后重试")
    } catch (e: Exception) {
        ApiResult.Error(e.message ?: "未知错误")
    }
}

private fun WorkbenchRequirementDto.toDomain() = WorkbenchRequirement(
    id = id,
    zentaoReqId = zentaoReqId.orEmpty(),
    title = title.orEmpty(),
    caseCompleted = caseCompleted,
    testCompleted = testCompleted,
    majorVersionName = majorVersionName.orEmpty(),
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
