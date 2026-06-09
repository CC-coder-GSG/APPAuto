package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** 通用应答：仅在意是否 2xx；body 任意 JSON 对象都能被忽略未知键解析。 */
@Serializable
data class AckResponse(
    val message: String? = null,
)

/** GET /softwares 返回项。 */
@Serializable
data class SoftwareDto(
    val id: Int,
    val name: String? = null,
)

/** GET /versions 返回项。version_type 取值 major / minor。 */
@Serializable
data class VersionDto(
    val id: Int,
    @SerialName("version_no") val versionNo: String? = null,
    @SerialName("version_type") val versionType: String? = null,
    @SerialName("parent_id") val parentId: Int? = null,
    @SerialName("software_id") val softwareId: Int? = null,
)
