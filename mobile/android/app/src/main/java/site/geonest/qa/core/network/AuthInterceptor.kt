package site.geonest.qa.core.network

import okhttp3.Interceptor
import okhttp3.Response
import site.geonest.qa.core.data.TokenStore
import javax.inject.Inject

/**
 * 统一请求拦截：
 *  - 声明终端类型 X-Client-Type: mobile（后端据此分配独立会话，实现与桌面端并存）。
 *  - 为已登录请求注入 JWT Bearer。
 *  - 当带 token 的请求返回 401（会话失效/被同类型设备踢下线）时清空本地 token，
 *    导航层据此自动回到登录页（避免"重试"反复 401 卡死）。
 */
class AuthInterceptor @Inject constructor(
    private val tokenStore: TokenStore,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        val token = tokenStore.token.value

        val builder = original.newBuilder()
            .header("X-Client-Type", "mobile")
        val hadAuth = !token.isNullOrBlank()
        if (hadAuth && original.header("Authorization") == null) {
            builder.header("Authorization", "Bearer $token")
        }

        val response = chain.proceed(builder.build())

        // 登录接口（无 token）的 400/401 属于"账号密码错误"，不在此清理。
        if (response.code == 401 && hadAuth) {
            tokenStore.clear()
        }
        return response
    }
}
