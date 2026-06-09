package site.geonest.qa.feature.retest

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.data.RetestRepository
import site.geonest.qa.core.domain.model.RetestRequirement
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
) : ViewModel() {

    private val _state = MutableStateFlow(RetestUiState())
    val state: StateFlow<RetestUiState> = _state.asStateFlow()

    init { load() }

    fun load(refresh: Boolean = false) {
        _state.update { it.copy(loading = !refresh, refreshing = refresh, error = null) }
        viewModelScope.launch {
            when (val result = repository.workbench()) {
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
