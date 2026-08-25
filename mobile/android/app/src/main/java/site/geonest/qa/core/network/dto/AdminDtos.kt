package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** GET /admin/data-overview —— 用户/版本/需求/Bug 全量。 */
@Serializable
data class DataOverviewDto(
    val users: List<AdminUserDto> = emptyList(),
    val versions: List<AdminIdDto> = emptyList(),
    val requirements: List<AdminReqItemDto> = emptyList(),
    val bugs: List<AdminIdDto> = emptyList(),
)

@Serializable
data class AdminIdDto(val id: Int)

@Serializable
data class AdminUserDto(
    val id: Int,
    val username: String = "",
    @SerialName("display_name") val displayName: String = "",
    val role: String = "user",
    @SerialName("is_team_member") val isTeamMember: Boolean = true,
    @SerialName("allowed_tabs") val allowedTabs: List<String> = emptyList(),
)

@Serializable
data class AdminReqItemDto(
    val id: Int,
    @SerialName("zentao_req_id") val zentaoReqId: String = "",
    val title: String = "",
    @SerialName("major_version_id") val majorVersionId: Int? = null,
)

// ---- 请求体 ----
@Serializable
data class UserCreateRequest(val username: String, val password: String, val role: String)

@Serializable
data class RoleUpdateRequest(val role: String)

@Serializable
data class TeamStatusRequest(@SerialName("is_team_member") val isTeamMember: Boolean)

@Serializable
data class TabPermissionsRequest(@SerialName("allowed_tabs") val allowedTabs: List<String>)

@Serializable
data class DisplayNameRequest(@SerialName("display_name") val displayName: String)

@Serializable
data class RequirementUpsertRequest(
    @SerialName("zentao_req_id") val zentaoReqId: String,
    val title: String,
    @SerialName("major_version_id") val majorVersionId: Int,
)
