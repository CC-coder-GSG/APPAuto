package site.geonest.qa.core.data

import kotlinx.coroutines.flow.StateFlow
import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.User
import site.geonest.qa.core.network.QaApi
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 鉴权仓储：登录、取用户信息、登出。
 * 网络/存储细节封装在此，UI 只与 ApiResult 打交道。
 * 业务逻辑层（无 Android 依赖）——未来 KMP 共享候选。
 */
@Singleton
class AuthRepository @Inject constructor(
    private val api: QaApi,
    private val tokenStore: TokenStore,
) {
    val tokenFlow: StateFlow<String?> = tokenStore.token

    suspend fun login(username: String, password: String): ApiResult<User> {
        return runCatchingApi {
            val token = api.login(username.trim(), password)
            tokenStore.save(token.accessToken)
            // 立即拉取用户信息，验证 token 并缓存权限。
            api.me().toDomain()
        }
    }

    suspend fun fetchMe(): ApiResult<User> = runCatchingApi { api.me().toDomain() }

    fun logout() {
        // TODO(M0): 调用后端"吊销当前设备会话"接口后再清本地。
        tokenStore.clear()
    }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        val msg = when (e.code()) {
            400, 401 -> "用户名或密码错误"
            403 -> "无权限访问"
            else -> "服务异常（${e.code()}）"
        }
        ApiResult.Error(msg, e.code())
    } catch (e: IOException) {
        ApiResult.Error("网络连接失败，请检查网络后重试")
    } catch (e: Exception) {
        ApiResult.Error(e.message ?: "未知错误")
    }
}

private fun site.geonest.qa.core.network.dto.MeResponse.toDomain() = User(
    id = id,
    username = username,
    displayName = displayName,
    role = role,
    isTeamMember = isTeamMember,
    allowedTabs = allowedTabs,
)
