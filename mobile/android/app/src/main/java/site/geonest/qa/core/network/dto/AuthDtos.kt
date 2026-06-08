package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** POST /auth/token 的响应（OAuth2 标准字段）。 */
@Serializable
data class TokenResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("token_type") val tokenType: String = "bearer",
)

/** GET /auth/me 的响应。 */
@Serializable
data class MeResponse(
    val id: Int,
    val username: String,
    @SerialName("display_name") val displayName: String,
    val role: String,
    @SerialName("is_team_member") val isTeamMember: Boolean = true,
    @SerialName("allowed_tabs") val allowedTabs: List<String> = emptyList(),
)
