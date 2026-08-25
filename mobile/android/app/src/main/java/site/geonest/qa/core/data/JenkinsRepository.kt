package site.geonest.qa.core.data

import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.JenkinsArtifact
import site.geonest.qa.core.domain.model.JenkinsBinding
import site.geonest.qa.core.domain.model.JenkinsBuild
import site.geonest.qa.core.domain.model.JenkinsJob
import site.geonest.qa.core.domain.model.JenkinsJobDetail
import site.geonest.qa.core.network.QaApi
import site.geonest.qa.core.network.dto.JenkinsBindingRequest
import site.geonest.qa.core.network.dto.JenkinsBuildRequest
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/** 绑定状态 + 默认配置。 */
data class JenkinsBindingState(
    val binding: JenkinsBinding?,
    val defaultBaseUrl: String,
    val defaultView: String,
)

/** 触发构建结果：消息 + 队列 URL（用于轮询真实构建号）。 */
data class JenkinsTriggerResult(val message: String, val queueUrl: String?)

@Singleton
class JenkinsRepository @Inject constructor(
    private val api: QaApi,
) {
    suspend fun binding(): ApiResult<JenkinsBindingState> = runCatchingApi {
        val r = api.jenkinsBinding()
        JenkinsBindingState(
            binding = r.binding?.let {
                JenkinsBinding(it.baseUrl, it.account, it.hasToken, it.lastCheckStatus, it.lastErrorMessage)
            },
            defaultBaseUrl = r.defaults.baseUrl,
            defaultView = r.defaults.view,
        )
    }

    suspend fun saveBinding(baseUrl: String, account: String, token: String): ApiResult<String> = runCatchingApi {
        val r = api.jenkinsSaveBinding(JenkinsBindingRequest(baseUrl.trim(), account.trim(), token.trim()))
        if (r.verified) "绑定已保存并验证通过" else (r.message ?: "已保存，但验证未通过")
    }

    suspend fun testBinding(baseUrl: String, account: String, token: String): ApiResult<String> = runCatchingApi {
        val r = api.jenkinsTestBinding(JenkinsBindingRequest(baseUrl.trim(), account.trim(), token.trim()))
        if (r.ok) (r.message ?: "凭据验证通过") else throw IllegalStateException(r.message ?: "凭据验证失败")
    }

    suspend fun deleteBinding(): ApiResult<Unit> = runCatchingApi { api.jenkinsDeleteBinding(); Unit }

    /** 所有视图名 + 默认视图。 */
    suspend fun views(): ApiResult<Pair<List<String>, String?>> = runCatchingApi {
        val r = api.jenkinsViews()
        r.views.map { it.name }.filter { it.isNotBlank() } to r.defaultView
    }

    suspend fun jobs(view: String?): ApiResult<List<JenkinsJob>> = runCatchingApi {
        api.jenkinsJobs(view).jobs.map { JenkinsJob(it.name, it.color, it.buildable) }
    }

    suspend fun job(name: String): ApiResult<JenkinsJobDetail> = runCatchingApi {
        val d = api.jenkinsJob(name)
        val params = d.property.flatMap { it.parameterDefinitions }.map { it.name }
        JenkinsJobDetail(
            name = d.name.ifBlank { name },
            color = d.color,
            buildable = d.buildable,
            description = d.description,
            healthScore = d.healthReport.firstOrNull()?.score,
            params = params,
            builds = d.builds.map { JenkinsBuild(it.number, it.result, it.building, it.timestamp, it.duration) },
        )
    }

    suspend fun triggerBuild(name: String): ApiResult<JenkinsTriggerResult> = runCatchingApi {
        val r = api.jenkinsTriggerBuild(name, JenkinsBuildRequest(params = null))
        JenkinsTriggerResult(r.message ?: "已触发构建", r.queueItemUrl)
    }

    /** 轮询队列项，返回真实构建号（未就绪返回 null）。 */
    suspend fun queueBuildNumber(queueUrl: String): ApiResult<Int?> = runCatchingApi {
        val q = api.jenkinsQueue(queueUrl)
        if (q.cancelled) throw IllegalStateException("构建已被取消")
        if (q.started) q.buildNumber else null
    }

    suspend fun artifacts(job: String, number: Int): ApiResult<List<JenkinsArtifact>> = runCatchingApi {
        api.jenkinsBuild(job, number).artifacts.map { JenkinsArtifact(it.fileName, it.relativePath) }
    }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        ApiResult.Error(
            when (e.code()) {
                400 -> "请先配置 Jenkins 绑定"
                401 -> "登录已失效，请重新登录"
                403 -> "无权限访问 Jenkins"
                502 -> "连接 Jenkins 失败，请检查地址/凭据"
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
