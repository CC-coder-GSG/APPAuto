package site.geonest.qa.core.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.Software
import site.geonest.qa.core.domain.model.VersionItem
import site.geonest.qa.core.domain.model.WorkbenchMode
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 工作台共享上下文：软件 + 大版本 + 小版本 + 模式。
 * 三个工作台（需求 / 复测 / 测试）共用同一份选择，切换其一其余同步。
 * 单例长驻进程；选择仅内存保存（重启回到默认），后续可接 DataStore 持久化。
 */
data class WorkbenchContextState(
    val loading: Boolean = true,
    val error: String? = null,
    val softwares: List<Software> = emptyList(),
    val versions: List<VersionItem> = emptyList(),
    val softwareId: Int? = null,
    val majorId: Int? = null,
    val minorId: Int? = null,
    val mode: WorkbenchMode = WorkbenchMode.VERSION,
) {
    val majors: List<VersionItem>
        get() = versions.filter { it.isMajor && (softwareId == null || it.softwareId == softwareId) }
    val minors: List<VersionItem>
        get() = versions.filter { it.isMinor && it.parentId == majorId }
    /** 上下文是否已就绪到可以拉取工作台数据（已选软件，且按版本模式下已选大版本）。 */
    val ready: Boolean
        get() = !loading && error == null && softwareId != null &&
            (mode == WorkbenchMode.ALL_PENDING || majorId != null)

    /** 取某大版本下的小版本（测试完成弹窗按需求所属大版本取，而非当前选中）。 */
    fun minorsOfMajor(majorVersionId: Int?): List<VersionItem> =
        versions.filter { it.isMinor && it.parentId == majorVersionId }
}

@Singleton
class WorkbenchContext @Inject constructor(
    private val catalog: CatalogRepository,
    private val prefs: PreferenceStore,
) {
    // 启动即用上次选择初始化（id 会在拉到目录后校验是否仍存在）。
    private val _state = MutableStateFlow(
        WorkbenchContextState(
            softwareId = prefs.getInt(KEY_SOFTWARE),
            majorId = prefs.getInt(KEY_MAJOR),
            minorId = prefs.getInt(KEY_MINOR),
            mode = prefs.getString(KEY_MODE)?.let { runCatching { WorkbenchMode.valueOf(it) }.getOrNull() }
                ?: WorkbenchMode.VERSION,
        ),
    )
    val state: StateFlow<WorkbenchContextState> = _state.asStateFlow()

    private val mutex = Mutex()
    private var loaded = false

    private fun persistSelection() {
        val s = _state.value
        prefs.putInt(KEY_SOFTWARE, s.softwareId)
        prefs.putInt(KEY_MAJOR, s.majorId)
        prefs.putInt(KEY_MINOR, s.minorId)
        prefs.putString(KEY_MODE, s.mode.name)
    }

    /** 首次进入任一工作台时调用，仅真正加载一次。 */
    suspend fun ensureLoaded() {
        mutex.withLock {
            if (loaded) return
            reloadInternal()
        }
    }

    suspend fun reload() = mutex.withLock { reloadInternal() }

    private suspend fun reloadInternal() {
        _state.update { it.copy(loading = true, error = null) }
        when (val sw = catalog.softwares()) {
            is ApiResult.Error -> _state.update { it.copy(loading = false, error = sw.message) }
            is ApiResult.Success -> {
                val softwares = sw.data
                val softwareId = _state.value.softwareId?.takeIf { id -> softwares.any { it.id == id } }
                    ?: softwares.firstOrNull()?.id
                loadVersions(softwares, softwareId)
                loaded = true
            }
        }
    }

    suspend fun selectSoftware(id: Int) = mutex.withLock {
        if (id == _state.value.softwareId) return@withLock
        // 切软件需重新拉该软件的版本列表，并重置大/小版本到默认。
        _state.update { it.copy(softwareId = id, majorId = null, minorId = null) }
        loadVersions(_state.value.softwares, id)
    }

    private suspend fun loadVersions(softwares: List<Software>, softwareId: Int?) {
        when (val vs = catalog.versions(softwareId)) {
            is ApiResult.Error -> _state.update {
                it.copy(loading = false, error = vs.message, softwares = softwares)
            }
            is ApiResult.Success -> _state.update { st ->
                val versions = vs.data
                val majors = versions.filter { it.isMajor && (softwareId == null || it.softwareId == softwareId) }
                val majorId = st.majorId?.takeIf { id -> majors.any { it.id == id } } ?: majors.firstOrNull()?.id
                val minors = versions.filter { it.isMinor && it.parentId == majorId }
                val minorId = st.minorId?.takeIf { id -> minors.any { it.id == id } } ?: minors.firstOrNull()?.id
                st.copy(
                    loading = false, error = null,
                    softwares = softwares, versions = versions,
                    softwareId = softwareId, majorId = majorId, minorId = minorId,
                )
            }
        }
        persistSelection()
    }

    /** 选大版本——纯内存计算，重置小版本到该大版本下首个。 */
    fun selectMajor(id: Int) {
        _state.update { st ->
            if (id == st.majorId) return@update st
            val minors = st.versions.filter { it.isMinor && it.parentId == id }
            st.copy(majorId = id, minorId = minors.firstOrNull()?.id)
        }
        persistSelection()
    }

    fun selectMinor(id: Int) {
        _state.update { it.copy(minorId = id) }
        persistSelection()
    }

    fun setMode(mode: WorkbenchMode) {
        _state.update { it.copy(mode = mode) }
        persistSelection()
    }

    private companion object {
        const val KEY_SOFTWARE = "wb_software_id"
        const val KEY_MAJOR = "wb_major_id"
        const val KEY_MINOR = "wb_minor_id"
        const val KEY_MODE = "wb_mode"
    }
}
