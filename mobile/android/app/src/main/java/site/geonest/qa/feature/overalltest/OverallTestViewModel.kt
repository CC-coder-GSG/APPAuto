package site.geonest.qa.feature.overalltest

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.data.OverallTestRepository
import site.geonest.qa.core.data.WorkbenchContext
import site.geonest.qa.core.data.WorkbenchContextState
import site.geonest.qa.core.domain.model.OverallBug
import site.geonest.qa.core.domain.model.OverallStats
import javax.inject.Inject

data class OverallTestUiState(
    val loading: Boolean = true,
    val refreshing: Boolean = false,
    val bugs: List<OverallBug> = emptyList(),
    val stats: OverallStats = OverallStats(),
    val statuses: Set<String> = emptySet(),
    val keyword: String = "",
    val submittingId: Int? = null,
    val error: String? = null,
    val toast: String? = null,
) {
    val isEmpty: Boolean get() = !loading && error == null && bugs.isEmpty()
}

/** 状态筛选可选项（与后端 effective_status 口径一致）。 */
val OVERALL_STATUS_OPTIONS = listOf(
    "active" to "激活",
    "resolved" to "已解决",
    "closed" to "已关闭",
    "local" to "本地",
)

@HiltViewModel
class OverallTestViewModel @Inject constructor(
    private val repo: OverallTestRepository,
    private val context: WorkbenchContext,
) : ViewModel() {

    val ctx: StateFlow<WorkbenchContextState> = context.state

    private val _ui = MutableStateFlow(OverallTestUiState())
    val ui: StateFlow<OverallTestUiState> = _ui.asStateFlow()

    init {
        // 恢复持久化选择后 ids 加载前后一致，需在 ensureLoaded 后显式 reload，避免无限转圈。
        viewModelScope.launch { context.ensureLoaded(); reload() }
        // 测试工作台始终按大版本，软件/大版本变化即重拉。
        viewModelScope.launch {
            context.state
                .map { it.softwareId to it.majorId }
                .distinctUntilChanged()
                .collect { reload() }
        }
    }

    fun selectSoftware(id: Int) = viewModelScope.launch { context.selectSoftware(id) }
    fun selectMajor(id: Int) = context.selectMajor(id)

    fun toggleStatus(code: String) {
        _ui.update { st ->
            val next = st.statuses.toMutableSet().apply { if (!add(code)) remove(code) }
            st.copy(statuses = next)
        }
        reload()
    }

    fun setKeyword(kw: String) { _ui.update { it.copy(keyword = kw) } }
    fun search() = reload()

    private fun ready(c: WorkbenchContextState) = c.softwareId != null && c.majorId != null

    fun reload(refresh: Boolean = false) {
        val c = context.state.value
        if (!ready(c)) {
            _ui.update { it.copy(loading = c.loading, refreshing = false, bugs = emptyList(), error = c.error) }
            return
        }
        _ui.update { it.copy(loading = !refresh, refreshing = refresh, error = null) }
        viewModelScope.launch {
            when (val r = repo.overview(c.majorId!!, c.softwareId, _ui.value.statuses, _ui.value.keyword)) {
                is ApiResult.Success -> _ui.update {
                    it.copy(loading = false, refreshing = false, bugs = r.data.bugs, stats = r.data.stats)
                }
                is ApiResult.Error -> _ui.update { it.copy(loading = false, refreshing = false, error = r.message) }
            }
        }
    }

    /** 提交修复结果/闭环。minorVersionId 取自结果弹窗所选小版本。 */
    fun submitResult(bugId: Int, minorVersionId: Int, testDone: Boolean, resolution: String, comment: String, zentaoBugId: String?) {
        if (_ui.value.submittingId != null) return
        _ui.update { it.copy(submittingId = bugId) }
        viewModelScope.launch {
            val r = repo.submitResult(bugId, minorVersionId, testDone, resolution, comment, zentaoBugId)
            _ui.update { it.copy(submittingId = null) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "已保存修复结果") }; reload(refresh = true) }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun retryContext() = viewModelScope.launch { context.reload() }
    fun consumeToast() = _ui.update { it.copy(toast = null) }
}
