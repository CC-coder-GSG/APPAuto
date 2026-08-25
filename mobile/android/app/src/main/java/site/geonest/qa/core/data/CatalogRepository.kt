package site.geonest.qa.core.data

import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.Software
import site.geonest.qa.core.domain.model.VersionItem
import site.geonest.qa.core.network.QaApi
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/** 软件 / 版本目录仓储——供工作台的软件、大/小版本选择器使用。 */
@Singleton
class CatalogRepository @Inject constructor(
    private val api: QaApi,
) {
    suspend fun softwares(): ApiResult<List<Software>> = runCatchingApi {
        api.softwares().map { Software(it.id, it.name.orEmpty()) }
    }

    suspend fun versions(softwareId: Int?): ApiResult<List<VersionItem>> = runCatchingApi {
        api.versions(softwareId).map {
            VersionItem(
                id = it.id,
                versionNo = it.versionNo.orEmpty(),
                type = (it.versionType ?: "").lowercase(),
                parentId = it.parentId,
                softwareId = it.softwareId,
            )
        }
    }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        ApiResult.Error(if (e.code() == 401) "登录已失效，请重新登录" else "加载失败（${e.code()}）", e.code())
    } catch (e: IOException) {
        ApiResult.Error("网络连接失败，请检查网络后重试")
    } catch (e: Exception) {
        ApiResult.Error(e.message ?: "未知错误")
    }
}
