package site.geonest.qa.feature.retest

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
import site.geonest.qa.core.data.RetestRepository
import site.geonest.qa.core.data.WorkbenchContext
import site.geonest.qa.core.data.WorkbenchContextState
import site.geonest.qa.core.domain.model.RetestRequirement
import site.geonest.qa.core.domain.model.WorkbenchMode
import javax.inject.Inject

data class RetestUiState(
    val loading: Boolean = true,
    val refreshing: Boolean = false,
    val items: List<RetestRequirement> = emptyList(),
    /** 正在提交结论的需求 id，用于禁用其卡片按钮、显示进度。 */
    val submittingId: Int? = null,
    val error: String? = null,
    val toast: String? = null,
) {
    val isEmpty: Boolean get() = !loading && error == null && items.isEmpty()
}

@HiltViewModel
class RetestViewModel @Inject constructor(
    private val repository: RetestRepository,
    private val context: WorkbenchContext,
) : ViewModel() {

    /** 共享版本上下文，供顶部筛选条读取。 */
    val ctx: StateFlow<WorkbenchContextState> = context.state

    private val _state = MutableStateFlow(RetestUiState())
    val state: StateFlow<RetestUiState> = _state.asStateFlow()

    init {
        // 恢复持久化选择后 ids 加载前后一致，需在 ensureLoaded 后显式 load，避免无限转圈。
        viewModelScope.launch { context.ensureLoaded(); load() }
        viewModelScope.launch {
            context.state
                .map { Triple(it.softwareId, it.majorId, it.mode) }
                .distinctUntilChanged()
                .collect { load() }
        }
    }

    fun selectSoftware(id: Int) = viewModelScope.launch { context.selectSoftware(id) }
    fun selectMajor(id: Int) = context.selectMajor(id)
    fun setMode(mode: WorkbenchMode) = context.setMode(mode)
    fun retryContext() = viewModelScope.launch { context.reload() }

    fun load(refresh: Boolean = false) {
        val c = context.state.value
        if (!c.ready) {
            _state.update { it.copy(loading = c.loading, refreshing = false, items = emptyList(), error = c.error) }
            return
        }
        _state.update { it.copy(loading = !refresh, refreshing = refresh, error = null) }
        val major = if (c.mode == WorkbenchMode.VERSION) c.majorId else null
        viewModelScope.launch {
            when (val result = repository.workbench(c.mode.api, major, c.softwareId)) {
                is ApiResult.Success -> _state.update {
                    it.copy(loading = false, refreshing = false, items = result.data)
                }
                is ApiResult.Error -> _state.update {
                    it.copy(loading = false, refreshing = false, error = result.message)
                }
            }
        }
    }

    fun markPassed(reqId: Int) = submit(reqId, passed = true)
    fun markFailed(reqId: Int) = submit(reqId, passed = false)

    private fun submit(reqId: Int, passed: Boolean) {
        if (_state.value.submittingId != null) return
        _state.update { it.copy(submittingId = reqId) }
        viewModelScope.launch {
            val result = if (passed) repository.markPassed(reqId) else repository.markFailed(reqId)
            when (result) {
                is ApiResult.Success -> _state.update { s ->
                    // 已给出结论的需求离开待复测队列；本地直接移除，避免整页刷新闪烁。
                    s.copy(
                        submittingId = null,
                        items = s.items.filterNot { it.id == reqId },
                        toast = if (passed) "已标记通过" else "已打回",
                    )
                }
                is ApiResult.Error -> _state.update {
                    it.copy(submittingId = null, toast = result.message)
                }
            }
        }
    }

    fun consumeToast() = _state.update { it.copy(toast = null) }
}
