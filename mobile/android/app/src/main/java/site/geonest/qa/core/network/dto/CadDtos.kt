package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** GET api/cad/boards 列表项。 */
@Serializable
data class CadBoardDto(
    val id: Int,
    val name: String? = null,
    @SerialName("version_count") val versionCount: Int = 0,
    @SerialName("item_count") val itemCount: Int = 0,
)

/** GET api/cad/boards/{id} 详情。records 以 "itemId:versionId" 为键。 */
@Serializable
data class CadBoardDetailDto(
    val id: Int,
    val name: String? = null,
    val versions: List<CadVersionDto> = emptyList(),
    val columns: List<CadColumnDto> = emptyList(),
    val items: List<CadItemDto> = emptyList(),
    val records: Map<String, CadRecordDto> = emptyMap(),
)

@Serializable
data class CadVersionDto(val id: Int, val name: String? = null)

@Serializable
data class CadColumnDto(val id: Int, val name: String? = null)

@Serializable
data class CadItemDto(
    val id: Int,
    val seq: Int? = null,
    val title: String? = null,
    @SerialName("zentao_bug_id") val zentaoBugId: Int? = null,
    val bug: CadBugDto? = null,
    @SerialName("cad_files") val cadFiles: List<CadFileDto> = emptyList(),
)

@Serializable
data class CadBugDto(
    @SerialName("bug_id") val bugId: String? = null,
    @SerialName("zentao_bug_url") val zentaoBugUrl: String? = null,
    @SerialName("zentao_bug_title") val zentaoBugTitle: String? = null,
)

@Serializable
data class CadFileDto(
    val id: Int,
    @SerialName("original_name") val originalName: String? = null,
)

@Serializable
data class CadRecordDto(
    @SerialName("normal_count") val normalCount: Int = 0,
    @SerialName("abnormal_count") val abnormalCount: Int = 0,
    val description: String? = null,
    @SerialName("custom_values") val customValues: Map<String, String> = emptyMap(),
    val attachments: List<CadAttachmentDto> = emptyList(),
)

@Serializable
data class CadAttachmentDto(
    val id: Int,
    val kind: String? = null,
    @SerialName("original_name") val originalName: String? = null,
    @SerialName("is_image") val isImage: Boolean = false,
    @SerialName("is_video") val isVideo: Boolean = false,
    @SerialName("download_url") val downloadUrl: String? = null,
    @SerialName("stream_url") val streamUrl: String? = null,
)

/** POST api/cad/records 请求体。 */
@Serializable
data class CadRecordRequest(
    @SerialName("item_id") val itemId: Int,
    @SerialName("version_id") val versionId: Int,
    @SerialName("normal_count") val normalCount: Int = 0,
    @SerialName("abnormal_count") val abnormalCount: Int = 0,
    val description: String? = null,
    @SerialName("custom_values") val customValues: Map<String, String>? = null,
)

/** POST api/cad/boards/{id}/items 请求体。 */
@Serializable
data class CadItemRequest(
    val seq: Int? = null,
    val title: String? = null,
    @SerialName("zentao_bug_id") val zentaoBugId: Int? = null,
)

/** POST api/cad/boards 请求体。 */
@Serializable
data class CadBoardRequest(
    val name: String,
    val description: String? = null,
)

/** POST api/cad/boards/{id}/versions 请求体。 */
@Serializable
data class CadVersionRequest(val name: String)

/** 创建统计表/版本的响应（{id, name}）。 */
@Serializable
data class CadCreatedDto(val id: Int, val name: String? = null)
