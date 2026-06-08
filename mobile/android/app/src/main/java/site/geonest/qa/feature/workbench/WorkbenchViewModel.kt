package site.geonest.qa.feature.workbench

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.data.WorkbenchRepository
import site.geonest.qa.core.domain.model.WorkbenchRequirement
import javax.inject.Inject

data class WorkbenchUiState(
    val loading: Boolean = true,
    val refreshing: Boolean = false,
    val pendingOnly: Boolean = false,
    val items: List<WorkbenchRequirement> = emptyList(),
    val error: String? = null,
    val toast: String? = null,
) {
    val isEmpty: Boolean get() = !loading && error == null && items.isEmpty()
}

@HiltViewModel
class WorkbenchViewModel @Inject constructor(
    private val repository: WorkbenchRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(WorkbenchUiState())
    val state: StateFlow<WorkbenchUiState> = _state.asStateFlow()

    init { load() }

    fun load(refresh: Boolean = false) {
        _state.update { it.copy(loading = !refresh, refreshing = refresh, error = null) }
        viewModelScope.launch {
            when (val result = repository.myWorkbench(pendingOnly = _state.value.pendingOnly)) {
                is ApiResult.Success -> _state.update {
                    it.copy(loading = false, refreshing = false, items = result.data)
                }
                is ApiResult.Error -> _state.update {
                    it.copy(loading = false, refreshing = false, error = result.message)
                }
            }
        }
    }

    fun togglePendingOnly() {
        _state.update { it.copy(pendingOnly = !it.pendingOnly) }
        load()
    }

    fun toggleCaseCompleted(reqId: Int, completed: Boolean) {
        viewModelScope.launch {
            when (val result = repository.setCaseCompleted(reqId, completed)) {
                is ApiResult.Success -> updateLocal(reqId) { it.copy(caseCompleted = result.data) }
                is ApiResult.Error -> _state.update { it.copy(toast = result.message) }
            }
        }
    }

    fun toggleTestCompleted(reqId: Int, completed: Boolean) {
        viewModelScope.launch {
            when (val result = repository.setTestCompleted(reqId, completed)) {
                is ApiResult.Success -> updateLocal(reqId) { it.copy(testCompleted = result.data) }
                is ApiResult.Error -> _state.update { it.copy(toast = result.message) }
            }
        }
    }

    fun consumeToast() = _state.update { it.copy(toast = null) }

    private fun updateLocal(reqId: Int, transform: (WorkbenchRequirement) -> WorkbenchRequirement) {
        _state.update { s ->
            s.copy(items = s.items.map { if (it.id == reqId) transform(it) else it })
        }
    }
}
