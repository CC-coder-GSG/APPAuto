package site.geonest.qa.core.data

import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.AdminReqItem
import site.geonest.qa.core.domain.model.AdminUser
import site.geonest.qa.core.domain.model.DataOverview
import site.geonest.qa.core.network.QaApi
import site.geonest.qa.core.network.dto.DisplayNameRequest
import site.geonest.qa.core.network.dto.RequirementUpsertRequest
import site.geonest.qa.core.network.dto.RoleUpdateRequest
import site.geonest.qa.core.network.dto.TabPermissionsRequest
import site.geonest.qa.core.network.dto.TeamStatusRequest
import site.geonest.qa.core.network.dto.UserCreateRequest
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 数据管理台仓储：用户管理 + 需求管理 + 数据总览。
 * 业务逻辑层，无 Android 依赖——KMP 共享候选。
 */
@Singleton
class AdminRepository @Inject constructor(
    private val api: QaApi,
) {
    suspend fun overview(): ApiResult<DataOverview> = runCatchingApi {
        val d = api.dataOverview()
        DataOverview(
            users = d.users.map { AdminUser(it.id, it.username, it.displayName.ifBlank { it.username }, it.role, it.isTeamMember, it.allowedTabs) },
            requirements = d.requirements.map { AdminReqItem(it.id, it.zentaoReqId, it.title, it.majorVersionId) },
            userCount = d.users.size,
            versionCount = d.versions.size,
            requirementCount = d.requirements.size,
            bugCount = d.bugs.size,
        )
    }

    suspend fun createUser(username: String, password: String, role: String): ApiResult<Unit> = runCatchingApi {
        api.createUser(UserCreateRequest(username.trim(), password, role)); Unit
    }

    suspend fun setRole(userId: Int, role: String): ApiResult<Unit> = runCatchingApi {
        api.updateUserRole(userId, RoleUpdateRequest(role)); Unit
    }

    suspend fun setTeamMember(userId: Int, isTeamMember: Boolean): ApiResult<Unit> = runCatchingApi {
        api.updateUserTeamStatus(userId, TeamStatusRequest(isTeamMember)); Unit
    }

    suspend fun setTabPermissions(userId: Int, tabs: List<String>): ApiResult<Unit> = runCatchingApi {
        api.updateUserTabPermissions(userId, TabPermissionsRequest(tabs)); Unit
    }

    suspend fun setDisplayName(userId: Int, name: String): ApiResult<Unit> = runCatchingApi {
        api.updateUserDisplayName(userId, DisplayNameRequest(name.trim())); Unit
    }

    suspend fun deleteUser(userId: Int): ApiResult<Unit> = runCatchingApi {
        api.deleteUser(userId); Unit
    }

    suspend fun createRequirement(zentaoReqId: String, title: String, majorVersionId: Int): ApiResult<Unit> = runCatchingApi {
        api.createRequirement(RequirementUpsertRequest(zentaoReqId.trim(), title.trim(), majorVersionId)); Unit
    }

    suspend fun updateRequirement(id: Int, zentaoReqId: String, title: String, majorVersionId: Int): ApiResult<Unit> = runCatchingApi {
        api.updateRequirement(id, RequirementUpsertRequest(zentaoReqId.trim(), title.trim(), majorVersionId)); Unit
    }

    suspend fun deleteRequirement(id: Int): ApiResult<Unit> = runCatchingApi {
        api.deleteRequirement(id); Unit
    }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        ApiResult.Error(
            when (e.code()) {
                401 -> "登录已失效，请重新登录"
                403 -> "无权限访问数据管理台"
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
