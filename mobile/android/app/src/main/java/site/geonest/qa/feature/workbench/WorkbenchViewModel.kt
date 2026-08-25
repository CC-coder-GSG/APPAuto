package site.geonest.qa.feature.workbench

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
import site.geonest.qa.core.data.WorkbenchContext
import site.geonest.qa.core.data.WorkbenchContextState
import site.geonest.qa.core.data.WorkbenchRepository
import site.geonest.qa.core.data.ZentaoRepository
import site.geonest.qa.core.domain.model.DispatchedBug
import site.geonest.qa.core.domain.model.StoryStatus
import site.geonest.qa.core.domain.model.WorkbenchMode
import site.geonest.qa.core.domain.model.WorkbenchRequirement
import javax.inject.Inject

data class WorkbenchUiState(
    val loading: Boolean = true,
    val refreshing: Boolean = false,
    val items: List<WorkbenchRequirement> = emptyList(),
    val dispatched: List<DispatchedBug> = emptyList(),
    /** 需求禅道实时状态，按禅道数字 story id 索引。 */
    val statuses: Map<Int, StoryStatus> = emptyMap(),
    val error: String? = null,
    val toast: String? = null,
) {
    val isEmpty: Boolean get() = !loading && error == null && items.isEmpty()
}

@HiltViewModel
class WorkbenchViewModel @Inject constructor(
    private val repo: WorkbenchRepository,
    private val zentao: ZentaoRepository,
    private val context: WorkbenchContext,
) : ViewModel() {

    /** 共享版本上下文，供顶部筛选条与测试完成弹窗读取小版本。 */
    val ctx: StateFlow<WorkbenchContextState> = context.state

    private val _ui = MutableStateFlow(WorkbenchUiState())
    val ui: StateFlow<WorkbenchUiState> = _ui.asStateFlow()

    init {
        // 恢复持久化选择后，ids 在加载前后可能一致，distinctUntilChanged 不会再触发，
        // 因此 ensureLoaded 完成后显式 reload 一次，避免重启后无限转圈。
        viewModelScope.launch { context.ensureLoaded(); reload() }
        // 软件 / 大版本 / 模式任一变化即重拉数据。
        viewModelScope.launch {
            context.state
                .map { Triple(it.softwareId, it.majorId, it.mode) }
                .distinctUntilChanged()
                .collect { reload() }
        }
    }

    fun selectSoftware(id: Int) = viewModelScope.launch { context.selectSoftware(id) }
    fun selectMajor(id: Int) = context.selectMajor(id)
    fun setMode(mode: WorkbenchMode) = context.setMode(mode)

    fun reload(refresh: Boolean = false) {
        val c = context.state.value
        if (!c.ready) {
            _ui.update { it.copy(loading = c.loading, refreshing = false, items = emptyList(), dispatched = emptyList(), error = c.error) }
            return
        }
        _ui.update { it.copy(loading = !refresh, refreshing = refresh, error = null) }
        viewModelScope.launch {
            when (val r = repo.myWorkbench(c.mode, c.majorId, c.softwareId)) {
                is ApiResult.Success -> {
                    _ui.update { it.copy(loading = false, refreshing = false, items = r.data) }
                    loadStatuses(r.data)
                }
                is ApiResult.Error -> _ui.update { it.copy(loading = false, refreshing = false, error = r.message) }
            }
            loadDispatched(c)
        }
    }

    /** 拉取需求的禅道实时状态（开发完成/激活等）。增强信息，失败静默。 */
    private fun loadStatuses(items: List<WorkbenchRequirement>) {
        val ids = items.mapNotNull { it.zentaoReqId.filter(Char::isDigit).toIntOrNull()?.takeIf { n -> n > 0 } }.distinct()
        if (ids.isEmpty()) { _ui.update { it.copy(statuses = emptyMap()) }; return }
        viewModelScope.launch {
            val map = zentao.storyStatuses(ids)
            _ui.update { it.copy(statuses = map) }
        }
    }

    private suspend fun loadDispatched(c: WorkbenchContextState) {
        if (c.mode != WorkbenchMode.VERSION || c.majorId == null) {
            _ui.update { it.copy(dispatched = emptyList()) }
            return
        }
        when (val r = repo.dispatchedToMe(c.majorId)) {
            is ApiResult.Success -> _ui.update { it.copy(dispatched = r.data) }
            is ApiResult.Error -> Unit // 指派区加载失败不打断主列表
        }
    }

    fun toggleCaseCompleted(reqId: Int, completed: Boolean) {
        viewModelScope.launch {
            when (val r = repo.setCaseCompleted(reqId, completed)) {
                is ApiResult.Success -> reload(refresh = true)
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    /** 取消测试完成（勾选完成请走 [submitTestExecution]）。 */
    fun clearTestCompleted(reqId: Int) {
        viewModelScope.launch {
            when (val r = repo.clearTestCompleted(reqId)) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "已取消测试完成状态") }; reload(refresh = true) }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun submitTestExecution(reqId: Int, minorVersionId: Int, resultStatus: String, notes: String) {
        viewModelScope.launch {
            when (val r = repo.submitTestExecution(reqId, minorVersionId, resultStatus, notes)) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "已提交测试执行并标记测试完成") }; reload(refresh = true) }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun saveTestNotes(reqId: Int, notes: String) {
        viewModelScope.launch {
            when (val r = repo.saveTestNotes(reqId, notes)) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "测试要点已保存") }; reload(refresh = true) }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun retryContext() = viewModelScope.launch { context.reload() }

    fun consumeToast() = _ui.update { it.copy(toast = null) }
}
