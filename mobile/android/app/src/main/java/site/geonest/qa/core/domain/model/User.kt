package site.geonest.qa.core.domain.model

/** 领域层用户模型，与传输层 DTO 解耦。未来可整体抽入 KMP 共享层。 */
data class User(
    val id: Int,
    val username: String,
    val displayName: String,
    val role: String,
    val isTeamMember: Boolean,
    val allowedTabs: List<String>,
) {
    val isAdmin: Boolean get() = role == "admin"
}
