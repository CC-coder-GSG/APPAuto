package site.geonest.qa.core.domain.model

/** 软件产品。 */
data class Software(val id: Int, val name: String)

/** 版本（大版本 major / 小版本 minor）。小版本的 parentId 指向其大版本。 */
data class VersionItem(
    val id: Int,
    val versionNo: String,
    val type: String,
    val parentId: Int?,
    val softwareId: Int?,
) {
    val isMajor: Boolean get() = type == "major"
    val isMinor: Boolean get() = type == "minor"
}

/** 工作台数据模式：按指定大版本，或跨版本只看待办。 */
enum class WorkbenchMode(val api: String, val label: String) {
    VERSION("version", "按版本"),
    ALL_PENDING("all_pending", "跨版本待办"),
}
