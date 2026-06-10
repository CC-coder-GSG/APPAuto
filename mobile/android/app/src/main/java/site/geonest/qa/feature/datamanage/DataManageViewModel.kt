package site.geonest.qa.feature.datamanage

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.data.AdminRepository
import site.geonest.qa.core.data.WorkbenchContext
import site.geonest.qa.core.data.WorkbenchContextState
import site.geonest.qa.core.domain.model.DataOverview
import javax.inject.Inject

enum class DataTab(val label: String) {
    OVERVIEW("总览"),
    USERS("用户管理"),
    REQUIREMENTS("需求管理"),
}

data class DataManageUiState(
    val loading: Boolean = true,
    val busy: Boolean = false,
    val error: String? = null,
    val toast: String? = null,
    val tab: DataTab = DataTab.OVERVIEW,
    val overview: DataOverview = DataOverview(),
)

@HiltViewModel
class DataManageViewModel @Inject constructor(
    private val repo: AdminRepository,
    private val context: WorkbenchContext,
) : ViewModel() {

    val ctx: StateFlow<WorkbenchContextState> = context.state

    private val _ui = MutableStateFlow(DataManageUiState())
    val ui: StateFlow<DataManageUiState> = _ui.asStateFlow()

    init {
        viewModelScope.launch { context.ensureLoaded() }
        load()
    }

    fun setTab(tab: DataTab) = _ui.update { it.copy(tab = tab) }

    fun load() {
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val r = repo.overview()) {
                is ApiResult.Success -> _ui.update { it.copy(loading = false, overview = r.data) }
                is ApiResult.Error -> _ui.update { it.copy(loading = false, error = r.message) }
            }
        }
    }

    /** 包一层：执行写操作 → toast → 重新拉取总览。 */
    private fun mutate(block: suspend () -> ApiResult<Unit>, success: String) {
        if (_ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = block()
            _ui.update { it.copy(busy = false) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = success) }; reloadOverview() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    private fun reloadOverview() {
        viewModelScope.launch {
            when (val r = repo.overview()) {
                is ApiResult.Success -> _ui.update { it.copy(overview = r.data) }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun createUser(username: String, password: String, role: String) =
        mutate({ repo.createUser(username, password, role) }, "已创建用户")

    fun setRole(userId: Int, role: String) =
        mutate({ repo.setRole(userId, role) }, "已更新角色")

    fun setTeamMember(userId: Int, isTeamMember: Boolean) =
        mutate({ repo.setTeamMember(userId, isTeamMember) }, "已更新团队状态")

    fun setTabPermissions(userId: Int, tabs: List<String>) =
        mutate({ repo.setTabPermissions(userId, tabs) }, "已更新页面权限")

    fun setDisplayName(userId: Int, name: String) =
        mutate({ repo.setDisplayName(userId, name) }, "已更新显示名")

    fun deleteUser(userId: Int) =
        mutate({ repo.deleteUser(userId) }, "已删除用户")

    fun createRequirement(zentaoReqId: String, title: String, majorVersionId: Int) =
        mutate({ repo.createRequirement(zentaoReqId, title, majorVersionId) }, "已创建需求")

    fun updateRequirement(id: Int, zentaoReqId: String, title: String, majorVersionId: Int) =
        mutate({ repo.updateRequirement(id, zentaoReqId, title, majorVersionId) }, "已更新需求")

    fun deleteRequirement(id: Int) =
        mutate({ repo.deleteRequirement(id) }, "已删除需求")

    fun consumeToast() = _ui.update { it.copy(toast = null) }
}
