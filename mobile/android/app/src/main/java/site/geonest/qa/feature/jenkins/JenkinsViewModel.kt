package site.geonest.qa.feature.jenkins

import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import site.geonest.qa.BuildConfig
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.data.JenkinsRepository
import site.geonest.qa.core.data.PreferenceStore
import site.geonest.qa.core.data.TokenStore
import site.geonest.qa.core.domain.model.JenkinsArtifact
import site.geonest.qa.core.domain.model.JenkinsBinding
import site.geonest.qa.core.domain.model.JenkinsJob
import site.geonest.qa.core.domain.model.JenkinsJobDetail
import java.net.URLEncoder
import javax.inject.Inject

data class JenkinsUiState(
    val loading: Boolean = true,
    val error: String? = null,
    val toast: String? = null,
    val busy: Boolean = false,
    val binding: JenkinsBinding? = null,
    val defaultBaseUrl: String = "",
    val defaultView: String = "",
    val views: List<String> = emptyList(),
    val selectedView: String = "",
    val jobsLoading: Boolean = false,
    val jobsError: String? = null,
    val jobs: List<JenkinsJob> = emptyList(),
    val detail: JenkinsJobDetail? = null,
    val detailLoading: Boolean = false,
    val artifacts: Map<Int, List<JenkinsArtifact>> = emptyMap(),
    val artifactsLoadingFor: Int? = null,
) {
    val bound: Boolean get() = binding != null
}

@HiltViewModel
class JenkinsViewModel @Inject constructor(
    private val repo: JenkinsRepository,
    private val tokenStore: TokenStore,
    private val prefs: PreferenceStore,
    @ApplicationContext private val context: Context,
) : ViewModel() {

    private val _ui = MutableStateFlow(JenkinsUiState())
    val ui: StateFlow<JenkinsUiState> = _ui.asStateFlow()

    private val base = BuildConfig.API_BASE_URL.trimEnd('/')
    private val keyView = "jenkins_view"

    init { loadBinding() }

    fun loadBinding() {
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val r = repo.binding()) {
                is ApiResult.Success -> {
                    _ui.update {
                        it.copy(
                            loading = false,
                            binding = r.data.binding,
                            defaultBaseUrl = r.data.defaultBaseUrl,
                            defaultView = r.data.defaultView,
                        )
                    }
                    if (r.data.binding != null) {
                        if (_ui.value.selectedView.isBlank()) {
                            // 优先恢复上次选择的视图，其次后端默认视图。
                            _ui.update { it.copy(selectedView = prefs.getString(keyView) ?: r.data.defaultView) }
                        }
                        loadViews()
                        loadJobs()
                    }
                }
                is ApiResult.Error -> _ui.update { it.copy(loading = false, error = r.message) }
            }
        }
    }

    private fun loadViews() {
        viewModelScope.launch {
            when (val r = repo.views()) {
                is ApiResult.Success -> {
                    val (names, default) = r.data
                    _ui.update { st ->
                        val sel = st.selectedView.takeIf { it.isNotBlank() && it in names }
                            ?: prefs.getString(keyView)?.takeIf { it in names }
                            ?: default?.takeIf { it in names }
                            ?: names.firstOrNull().orEmpty()
                        st.copy(views = names, selectedView = sel)
                    }
                }
                is ApiResult.Error -> { /* 视图列表失败不致命，保留默认视图 */ }
            }
        }
    }

    fun selectView(view: String) {
        if (view == _ui.value.selectedView) return
        prefs.putString(keyView, view)
        _ui.update { it.copy(selectedView = view, detail = null) }
        loadJobs()
    }

    fun saveBinding(baseUrl: String, account: String, token: String) {
        if (_ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = repo.saveBinding(baseUrl, account, token)
            _ui.update { it.copy(busy = false) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = r.data) }; loadBinding() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun testBinding(baseUrl: String, account: String, token: String) {
        if (_ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = repo.testBinding(baseUrl, account, token)
            _ui.update { it.copy(busy = false) }
            _ui.update { it.copy(toast = if (r is ApiResult.Success) r.data else (r as ApiResult.Error).message) }
        }
    }

    fun deleteBinding() {
        viewModelScope.launch {
            when (val r = repo.deleteBinding()) {
                is ApiResult.Success -> {
                    _ui.update { it.copy(toast = "已删除绑定", binding = null, jobs = emptyList(), detail = null) }
                }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun loadJobs() {
        _ui.update { it.copy(jobsLoading = true, jobsError = null) }
        viewModelScope.launch {
            val view = _ui.value.selectedView.ifBlank { _ui.value.defaultView.ifBlank { null } }
            when (val r = repo.jobs(view)) {
                is ApiResult.Success -> _ui.update { it.copy(jobsLoading = false, jobs = r.data) }
                is ApiResult.Error -> _ui.update { it.copy(jobsLoading = false, jobsError = r.message) }
            }
        }
    }

    fun openJob(name: String) {
        _ui.update { it.copy(detailLoading = true, detail = null, artifacts = emptyMap()) }
        viewModelScope.launch {
            when (val r = repo.job(name)) {
                is ApiResult.Success -> _ui.update { it.copy(detailLoading = false, detail = r.data) }
                is ApiResult.Error -> _ui.update { it.copy(detailLoading = false, toast = r.message) }
            }
        }
    }

    fun closeJob() = _ui.update { it.copy(detail = null, artifacts = emptyMap()) }

    fun refreshJob() { _ui.value.detail?.let { openJob(it.name) } }

    fun triggerBuild(name: String) {
        if (_ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            when (val r = repo.triggerBuild(name)) {
                is ApiResult.Success -> {
                    _ui.update { it.copy(toast = r.data.message) }
                    val queueUrl = r.data.queueUrl
                    if (queueUrl != null) pollQueue(queueUrl, name)
                    _ui.update { it.copy(busy = false) }
                }
                is ApiResult.Error -> _ui.update { it.copy(busy = false, toast = r.message) }
            }
        }
    }

    private suspend fun pollQueue(queueUrl: String, jobName: String) {
        repeat(8) {
            delay(2000)
            when (val r = repo.queueBuildNumber(queueUrl)) {
                is ApiResult.Success -> {
                    val n = r.data
                    if (n != null) {
                        _ui.update { it.copy(toast = "已启动构建 #$n") }
                        refreshJob()
                        return
                    }
                }
                is ApiResult.Error -> { _ui.update { it.copy(toast = r.message) }; return }
            }
        }
    }

    /** 展开/收起某构建的产物列表；首次展开时拉取。 */
    fun toggleArtifacts(jobName: String, number: Int) {
        if (_ui.value.artifacts.containsKey(number)) {
            _ui.update { it.copy(artifacts = it.artifacts - number) }
            return
        }
        _ui.update { it.copy(artifactsLoadingFor = number) }
        viewModelScope.launch {
            when (val r = repo.artifacts(jobName, number)) {
                is ApiResult.Success -> _ui.update { it.copy(artifactsLoadingFor = null, artifacts = it.artifacts + (number to r.data)) }
                is ApiResult.Error -> _ui.update { it.copy(artifactsLoadingFor = null, toast = r.message) }
            }
        }
    }

    /** 下载产物：优先 DownloadManager（带通知、落地到“下载”目录），失败回退浏览器。 */
    fun download(jobName: String, number: Int, relativePath: String, fileName: String) {
        val token = tokenStore.token.value.orEmpty()
        fun enc(s: String) = URLEncoder.encode(s, "UTF-8")
        val url = "$base/jenkins/download?job=${enc(jobName)}&number=$number&path=${enc(relativePath)}&__jp_auth=${enc(token)}"
        try {
            val req = DownloadManager.Request(Uri.parse(url))
                .setTitle(fileName)
                .setDescription("Jenkins 产物下载")
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fileName)
            val dm = context.getSystemService(Context.DOWNLOAD_SERVICE) as? DownloadManager
            if (dm != null) {
                dm.enqueue(req)
                _ui.update { it.copy(toast = "开始下载 $fileName") }
            } else {
                openInBrowser(url)
            }
        } catch (e: Exception) {
            openInBrowser(url)
        }
    }

    private fun openInBrowser(url: String) {
        try {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            _ui.update { it.copy(toast = "已在浏览器中打开下载") }
        } catch (e: Exception) {
            _ui.update { it.copy(toast = "无法发起下载：${e.message}") }
        }
    }

    fun consumeToast() = _ui.update { it.copy(toast = null) }
}
