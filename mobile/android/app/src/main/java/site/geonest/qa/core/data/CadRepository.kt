package site.geonest.qa.core.data

import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.CadAttachment
import site.geonest.qa.core.domain.model.CadBoard
import site.geonest.qa.core.domain.model.CadBoardDetail
import site.geonest.qa.core.domain.model.CadColumn
import site.geonest.qa.core.domain.model.CadFile
import site.geonest.qa.core.domain.model.CadItem
import site.geonest.qa.core.domain.model.CadRecord
import site.geonest.qa.core.domain.model.CadVersion
import site.geonest.qa.core.network.QaApi
import site.geonest.qa.core.network.dto.CadBoardRequest
import site.geonest.qa.core.network.dto.CadItemRequest
import site.geonest.qa.core.network.dto.CadRecordRequest
import site.geonest.qa.core.network.dto.CadVersionRequest
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * CAD 测试统计仓储：查看统计表/版本/条目/记录，提交记录与上传截图。
 * 业务逻辑层，无 Android 依赖——未来 KMP 共享候选。
 */
@Singleton
class CadRepository @Inject constructor(
    private val api: QaApi,
) {
    suspend fun boards(): ApiResult<List<CadBoard>> = runCatchingApi {
        api.cadBoards().map { CadBoard(it.id, it.name.orEmpty(), it.versionCount, it.itemCount) }
    }

    suspend fun board(boardId: Int): ApiResult<CadBoardDetail> = runCatchingApi {
        val d = api.cadBoard(boardId)
        CadBoardDetail(
            id = d.id,
            name = d.name.orEmpty(),
            versions = d.versions.map { CadVersion(it.id, it.name.orEmpty()) },
            columns = d.columns.map { CadColumn(it.id, it.name.orEmpty()) },
            items = d.items.map { i ->
                CadItem(
                    id = i.id,
                    seq = i.seq,
                    title = i.title.orEmpty(),
                    zentaoBugId = i.zentaoBugId,
                    bugUrl = i.bug?.zentaoBugUrl,
                    cadFiles = i.cadFiles.map { CadFile(it.id, it.originalName.orEmpty()) },
                )
            },
            records = d.records.mapValues { (_, r) ->
                CadRecord(
                    normalCount = r.normalCount,
                    abnormalCount = r.abnormalCount,
                    description = r.description.orEmpty(),
                    customValues = r.customValues,
                    attachments = r.attachments.map {
                        CadAttachment(it.id, it.kind.orEmpty(), it.originalName.orEmpty(), it.isImage, it.isVideo, it.downloadUrl, it.streamUrl)
                    },
                )
            },
        )
    }

    suspend fun saveRecord(
        itemId: Int,
        versionId: Int,
        normal: Boolean,
        abnormal: Boolean,
        description: String,
        customValues: Map<String, String>,
    ): ApiResult<Unit> = runCatchingApi {
        api.cadSaveRecord(
            CadRecordRequest(
                itemId = itemId,
                versionId = versionId,
                normalCount = if (normal) 1 else 0,
                abnormalCount = if (abnormal) 1 else 0,
                description = description.ifBlank { null },
                customValues = customValues.filterValues { it.isNotBlank() }.ifEmpty { null },
            ),
        )
        Unit
    }

    suspend fun createItem(boardId: Int, seq: Int?, title: String?, zentaoBugId: Int?): ApiResult<Unit> = runCatchingApi {
        api.cadCreateItem(boardId, CadItemRequest(seq, title?.ifBlank { null }, zentaoBugId))
        Unit
    }

    /** 新建统计表，返回新表 id。 */
    suspend fun createBoard(name: String): ApiResult<Int> = runCatchingApi {
        api.cadCreateBoard(CadBoardRequest(name = name.trim())).id
    }

    /** 在某统计表下新建版本，返回新版本 id。 */
    suspend fun createVersion(boardId: Int, name: String): ApiResult<Int> = runCatchingApi {
        api.cadCreateVersion(boardId, CadVersionRequest(name = name.trim())).id
    }

    suspend fun uploadScreenshot(itemId: Int, versionId: Int, bytes: ByteArray, mime: String): ApiResult<Unit> = runCatchingApi {
        val ext = when {
            mime.contains("png") -> "png"
            mime.contains("webp") -> "webp"
            else -> "jpg"
        }
        val body = bytes.toRequestBody(mime.toMediaTypeOrNull())
        val part = MultipartBody.Part.createFormData("file", "cad-mobile-${System.currentTimeMillis()}.$ext", body)
        api.cadUploadAttachment(itemId, versionId, "screenshot", part)
        Unit
    }

    suspend fun deleteAttachment(attachmentId: Int): ApiResult<Unit> = runCatchingApi {
        api.cadDeleteAttachment(attachmentId); Unit
    }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        ApiResult.Error(
            when (e.code()) {
                401 -> "登录已失效，请重新登录"
                403 -> "无权限访问 CAD 测试统计"
                else -> "服务异常（${e.code()}）"
            },
            e.code(),
        )
    } catch (e: IOException) {
        ApiResult.Error("网络连接失败，请检查网络后重试")
    } catch (e: Exception) {
        ApiResult.Error(e.message ?: "未知错误")
    }
}
