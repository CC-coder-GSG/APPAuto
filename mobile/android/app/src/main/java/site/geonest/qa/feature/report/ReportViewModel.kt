package site.geonest.qa.feature.report

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.data.ReportRepository
import site.geonest.qa.core.data.WorkbenchContext
import site.geonest.qa.core.data.WorkbenchContextState
import site.geonest.qa.core.domain.model.ReportData
import java.time.LocalDate
import javax.inject.Inject

/** 时间范围预设（避免移动端日期选择器复杂度）。 */
enum class ReportRange(val label: String) {
    LAST_7("近 7 天"),
    LAST_30("近 30 天"),
    LAST_90("近 90 天"),
    THIS_MONTH("本月"),
    ;

    fun startEnd(): Pair<LocalDate, LocalDate> {
        val today = LocalDate.now()
        val start = when (this) {
            LAST_7 -> today.minusDays(6)
            LAST_30 -> today.minusDays(29)
            LAST_90 -> today.minusDays(89)
            THIS_MONTH -> today.withDayOfMonth(1)
        }
        return start to today
    }
}

data class ReportUiState(
    val loading: Boolean = false,
    val error: String? = null,
    val data: ReportData? = null,
    val range: ReportRange = ReportRange.LAST_30,
    /** null = 全部大版本。 */
    val majorId: Int? = null,
)

@HiltViewModel
class ReportViewModel @Inject constructor(
    private val repo: ReportRepository,
    private val context: WorkbenchContext,
) : ViewModel() {

    val ctx: StateFlow<WorkbenchContextState> = context.state

    private val _ui = MutableStateFlow(ReportUiState())
    val ui: StateFlow<ReportUiState> = _ui.asStateFlow()

    init {
        viewModelScope.launch {
            context.ensureLoaded()
            // 默认跟随工作台已选大版本（若有）。
            _ui.update { it.copy(majorId = context.state.value.majorId) }
            load()
        }
    }

    fun selectSoftware(id: Int) = viewModelScope.launch {
        context.selectSoftware(id)
        _ui.update { it.copy(majorId = null) }
        load()
    }

    fun selectMajor(id: Int) {
        _ui.update { it.copy(majorId = id.takeIf { v -> v != 0 }) }
        load()
    }

    fun setRange(range: ReportRange) {
        _ui.update { it.copy(range = range) }
        load()
    }

    fun load() {
        val c = context.state.value
        if (c.softwareId == null) {
            _ui.update { it.copy(loading = false, error = null, data = null) }
            return
        }
        val (start, end) = _ui.value.range.startEnd()
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val r = repo.report(start.toString(), end.toString(), null, _ui.value.majorId, c.softwareId)) {
                is ApiResult.Success -> _ui.update { it.copy(loading = false, data = r.data) }
                is ApiResult.Error -> _ui.update { it.copy(loading = false, error = r.message) }
            }
        }
    }

    fun retryContext() = viewModelScope.launch { context.reload(); load() }
}
