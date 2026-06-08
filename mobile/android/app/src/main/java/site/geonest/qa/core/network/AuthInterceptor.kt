package site.geonest.qa.core.network

import okhttp3.Interceptor
import okhttp3.Response
import site.geonest.qa.core.data.TokenStore
import javax.inject.Inject

/**
 * 为每个请求注入 JWT Bearer。登录接口本身不带 token。
 * 401 的统一处理（续期/登出）后续在 M0 token 续期完成后补 Authenticator。
 */
class AuthInterceptor @Inject constructor(
    private val tokenStore: TokenStore,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        val token = tokenStore.token.value
        val request = if (!token.isNullOrBlank() && original.header("Authorization") == null) {
            original.newBuilder()
                .addHeader("Authorization", "Bearer $token")
                .build()
        } else {
            original
        }
        return chain.proceed(request)
    }
}
