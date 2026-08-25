package site.geonest.qa.feature.assign

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
import site.geonest.qa.core.data.AssignRepository
import site.geonest.qa.core.data.WorkbenchContext
import site.geonest.qa.core.data.WorkbenchContextState
import site.geonest.qa.core.domain.model.AssignProgress
import site.geonest.qa.core.domain.model.AssignRequirement
import site.geonest.qa.core.domain.model.LinkOption
import site.geonest.qa.core.domain.model.UserOption
import javax.inject.Inject

enum class AssignSubtab(val label: String) { ASSIGN("分配"), PROGRESS("进行状态"), LINK("关联需求") }

data class AssignUiState(
    val subtab: AssignSubtab = AssignSubtab.ASSIGN,
    val loading: Boolean = true,
    val error: String? = null,
    val toast: String? = null,
    val busy: Boolean = false,
    val users: List<UserOption> = emptyList(),
    val reqs: List<AssignRequirement> = emptyList(),
    val owners: Map<Int, Int> = emptyMap(), // reqId -> ownerId（0=未分配）
    val progress: AssignProgress? = null,
    val progressPendingOnly: Boolean = false,
    val linkSourceMajorId: Int? = null,
    val linkLoading: Boolean = false,
    val linkOptions: List<LinkOption> = emptyList(),
    val linkSelected: Set<Int> = emptySet(),
    val linkCopyStatus: Boolean = true,
)

@HiltViewModel
class AssignViewModel @Inject constructor(
    private val repo: AssignRepository,
    private val context: WorkbenchContext,
) : ViewModel() {

    val ctx: StateFlow<WorkbenchContextState> = context.state

    private val _ui = MutableStateFlow(AssignUiState())
    val ui: StateFlow<AssignUiState> = _ui.asStateFlow()

    init {
        // 恢复持久化选择后 ids 加载前后一致，需在 ensureLoaded 后显式刷新，避免无限转圈。
        viewModelScope.launch { context.ensureLoaded(); reloadAssign(); reloadProgress() }
        viewModelScope.launch { loadUsers() }
        viewModelScope.launch {
            context.state.map { it.softwareId to it.majorId }.distinctUntilChanged().collect {
                reloadAssign(); reloadProgress()
            }
        }
    }

    fun setSubtab(t: AssignSubtab) = _ui.update { it.copy(subtab = t) }
    fun selectSoftware(id: Int) = viewModelScope.launch { context.selectSoftware(id) }
    fun selectMajor(id: Int) = context.selectMajor(id)
    fun consumeToast() = _ui.update { it.copy(toast = null) }

    private suspend fun loadUsers() {
        when (val r = repo.users()) {
            is ApiResult.Success -> _ui.update { it.copy(users = r.data) }
            is ApiResult.Error -> Unit
        }
    }

    fun reloadAssign() {
        val c = context.state.value
        if (c.softwareId == null) return
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val r = repo.requirements(c.majorId, c.softwareId)) {
                is ApiResult.Success -> _ui.update {
                    it.copy(loading = false, reqs = r.data, owners = r.data.associate { req -> req.id to (req.ownerId ?: 0) })
                }
                is ApiResult.Error -> _ui.update { it.copy(loading = false, error = r.message) }
            }
        }
    }

    fun setOwner(reqId: Int, ownerId: Int) = _ui.update { it.copy(owners = it.owners + (reqId to ownerId)) }

    fun publish() {
        val c = context.state.value
        val majorId = c.majorId
        if (majorId == null) { _ui.update { it.copy(toast = "请先选择一个具体大版本再发布分配") }; return }
        if (_ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = repo.publish(majorId, _ui.value.owners)
            _ui.update { it.copy(busy = false) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "分配发布成功") }; reloadAssign(); reloadProgress() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun syncZentao() {
        val majorId = context.state.value.majorId
        if (majorId == null) { _ui.update { it.copy(toast = "请先选择一个具体大版本再同步") }; return }
        if (_ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = repo.syncZentao(majorId)
            _ui.update { it.copy(busy = false) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = r.data) }; reloadAssign() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun reloadProgress() {
        val c = context.state.value
        if (c.softwareId == null) return
        viewModelScope.launch {
            when (val r = repo.progress(c.majorId, c.softwareId)) {
                is ApiResult.Success -> _ui.update { it.copy(progress = r.data) }
                is ApiResult.Error -> Unit
            }
        }
    }

    fun toggleProgressPendingOnly() = _ui.update { it.copy(progressPendingOnly = !it.progressPendingOnly) }

    // ---- 关联需求 ----
    fun setLinkSource(majorId: Int) {
        _ui.update { it.copy(linkSourceMajorId = majorId, linkOptions = emptyList(), linkSelected = emptySet()) }
        loadLinkOptions()
    }

    fun loadLinkOptions() {
        val target = context.state.value.majorId ?: return
        val source = _ui.value.linkSourceMajorId ?: return
        if (source == target) { _ui.update { it.copy(toast = "来源大版本不能与目标相同") }; return }
        _ui.update { it.copy(linkLoading = true) }
        viewModelScope.launch {
            when (val r = repo.linkOptions(source, target)) {
                is ApiResult.Success -> _ui.update { it.copy(linkLoading = false, linkOptions = r.data, linkSelected = emptySet()) }
                is ApiResult.Error -> _ui.update { it.copy(linkLoading = false, toast = r.message) }
            }
        }
    }

    fun toggleLinkSelect(id: Int) = _ui.update {
        it.copy(linkSelected = it.linkSelected.toMutableSet().apply { if (!add(id)) remove(id) })
    }

    fun setLinkCopyStatus(v: Boolean) = _ui.update { it.copy(linkCopyStatus = v) }

    fun confirmLink() {
        val target = context.state.value.majorId
        val source = _ui.value.linkSourceMajorId
        val ids = _ui.value.linkSelected.toList()
        if (target == null || source == null) { _ui.update { it.copy(toast = "请选择目标与来源大版本") }; return }
        if (ids.isEmpty()) { _ui.update { it.copy(toast = "请至少勾选一条需求") }; return }
        if (_ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = repo.linkMajor(target, source, ids, _ui.value.linkCopyStatus)
            _ui.update { it.copy(busy = false) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = r.data) }; loadLinkOptions(); reloadAssign() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }
}
