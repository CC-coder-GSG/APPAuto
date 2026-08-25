package site.geonest.qa.core.common

/** 统一的结果封装，避免在 UI 层散落 try/catch。未来可整体抽入 KMP 共享层。 */
sealed interface ApiResult<out T> {
    data class Success<T>(val data: T) : ApiResult<T>
    data class Error(val message: String, val code: Int? = null) : ApiResult<Nothing>
}

inline fun <T> ApiResult<T>.onSuccess(block: (T) -> Unit): ApiResult<T> {
    if (this is ApiResult.Success) block(data)
    return this
}

inline fun <T> ApiResult<T>.onError(block: (String, Int?) -> Unit): ApiResult<T> {
    if (this is ApiResult.Error) block(message, code)
    return this
}
