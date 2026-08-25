package site.geonest.qa.feature.cad

import android.content.Context
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import coil.ImageLoader
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import site.geonest.qa.BuildConfig
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.data.CadRepository
import site.geonest.qa.core.data.PreferenceStore
import site.geonest.qa.core.data.TokenStore
import site.geonest.qa.core.domain.model.CadBoard
import site.geonest.qa.core.domain.model.CadBoardDetail
import java.net.URLEncoder
import javax.inject.Inject

data class CadUiState(
    val loading: Boolean = true,
    val error: String? = null,
    val toast: String? = null,
    val busy: Boolean = false,
    val boards: List<CadBoard> = emptyList(),
    val boardId: Int? = null,
    val board: CadBoardDetail? = null,
    val versionId: Int? = null,
)

@HiltViewModel
class CadViewModel @Inject constructor(
    private val repo: CadRepository,
    @ApplicationContext private val context: Context,
    private val tokenStore: TokenStore,
    private val prefs: PreferenceStore,
    val imageLoader: ImageLoader,
) : ViewModel() {

    private val keyBoard = "cad_board_id"
    private val keyVersion = "cad_version_id"

    private val _ui = MutableStateFlow(CadUiState())
    val ui: StateFlow<CadUiState> = _ui.asStateFlow()

    private val base = BuildConfig.API_BASE_URL.trimEnd('/')

    /** 鉴权由 Coil 复用的 OkHttp 拦截器注入，截图直接用绝对地址。 */
    fun absUrl(path: String?): String? = path?.let { base + it }

    /** 视频流：<video>/外部播放器无法设请求头，JWT 走 query token。 */
    fun streamUrlWithToken(path: String?): String? {
        if (path == null) return null
        val token = tokenStore.token.value.orEmpty()
        return base + path + "?token=" + URLEncoder.encode(token, "UTF-8")
    }

    init { loadBoards() }

    fun loadBoards() {
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val r = repo.boards()) {
                is ApiResult.Success -> {
                    val boards = r.data
                    val boardId = (_ui.value.boardId ?: prefs.getInt(keyBoard))
                        ?.takeIf { id -> boards.any { it.id == id } } ?: boards.firstOrNull()?.id
                    _ui.update { it.copy(boards = boards, boardId = boardId) }
                    if (boardId != null) loadBoard() else _ui.update { it.copy(loading = false, board = null) }
                }
                is ApiResult.Error -> _ui.update { it.copy(loading = false, error = r.message) }
            }
        }
    }

    fun selectBoard(id: Int) {
        if (id == _ui.value.boardId) return
        prefs.putInt(keyBoard, id)
        prefs.putInt(keyVersion, null) // 换统计表时清掉版本记忆，由新表重新解析
        _ui.update { it.copy(boardId = id, versionId = null) }
        loadBoard()
    }

    fun selectVersion(id: Int) {
        prefs.putInt(keyVersion, id)
        _ui.update { it.copy(versionId = id) }
    }

    fun createBoard(name: String) {
        if (name.isBlank() || _ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = repo.createBoard(name)
            _ui.update { it.copy(busy = false) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "已创建统计表", boardId = r.data, versionId = null) }; loadBoards() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun createVersion(name: String) {
        val boardId = _ui.value.boardId ?: return
        if (name.isBlank() || _ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = repo.createVersion(boardId, name)
            _ui.update { it.copy(busy = false) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "已新增版本", versionId = r.data) }; loadBoard() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun loadBoard() {
        val boardId = _ui.value.boardId ?: return
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val r = repo.board(boardId)) {
                is ApiResult.Success -> _ui.update { s ->
                    val b = r.data
                    val versionId = (s.versionId ?: prefs.getInt(keyVersion))
                        ?.takeIf { id -> b.versions.any { it.id == id } } ?: b.versions.firstOrNull()?.id
                    if (versionId != null) prefs.putInt(keyVersion, versionId)
                    s.copy(loading = false, board = b, versionId = versionId)
                }
                is ApiResult.Error -> _ui.update { it.copy(loading = false, error = r.message) }
            }
        }
    }

    fun saveRecord(itemId: Int, versionId: Int, normal: Boolean, abnormal: Boolean, description: String, custom: Map<String, String>) {
        if (_ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = repo.saveRecord(itemId, versionId, normal, abnormal, description, custom)
            _ui.update { it.copy(busy = false) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "已保存") }; loadBoard() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun createItem(seq: Int?, title: String?, zentaoBugId: Int?) {
        val boardId = _ui.value.boardId ?: return
        if (_ui.value.busy) return
        _ui.update { it.copy(busy = true) }
        viewModelScope.launch {
            val r = repo.createItem(boardId, seq, title, zentaoBugId)
            _ui.update { it.copy(busy = false) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "已新增条目") }; loadBoard() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun uploadScreenshot(itemId: Int, versionId: Int, uri: Uri) {
        viewModelScope.launch {
            val read = withContext(Dispatchers.IO) {
                try {
                    val mime = context.contentResolver.getType(uri) ?: "image/jpeg"
                    val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    if (bytes == null) null else mime to bytes
                } catch (e: Exception) {
                    null
                }
            }
            if (read == null) { _ui.update { it.copy(toast = "读取图片失败") }; return@launch }
            _ui.update { it.copy(busy = true) }
            val r = repo.uploadScreenshot(itemId, versionId, read.second, read.first)
            _ui.update { it.copy(busy = false) }
            when (r) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "截图已上传") }; loadBoard() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun deleteAttachment(attachmentId: Int) {
        viewModelScope.launch {
            when (val r = repo.deleteAttachment(attachmentId)) {
                is ApiResult.Success -> { _ui.update { it.copy(toast = "已删除") }; loadBoard() }
                is ApiResult.Error -> _ui.update { it.copy(toast = r.message) }
            }
        }
    }

    fun consumeToast() = _ui.update { it.copy(toast = null) }
}
