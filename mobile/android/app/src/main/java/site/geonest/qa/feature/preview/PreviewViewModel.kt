package site.geonest.qa.feature.preview

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.data.TokenStore
import site.geonest.qa.core.data.ZentaoRepository
import site.geonest.qa.core.domain.model.PreviewContent
import site.geonest.qa.core.domain.model.PreviewKind
import javax.inject.Inject

@HiltViewModel
class PreviewViewModel @Inject constructor(
    private val repo: ZentaoRepository,
    val tokenStore: TokenStore,
) : ViewModel() {

    data class State(
        val visible: Boolean = false,
        val loading: Boolean = false,
        val content: PreviewContent? = null,
        val error: String? = null,
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    fun open(kind: PreviewKind, id: Int) {
        if (id <= 0) return
        _state.value = State(visible = true, loading = true)
        viewModelScope.launch {
            when (val r = repo.preview(kind, id)) {
                is ApiResult.Success -> _state.update { it.copy(loading = false, content = r.data) }
                is ApiResult.Error -> _state.update { it.copy(loading = false, error = r.message) }
            }
        }
    }

    fun close() { _state.value = State() }
}
