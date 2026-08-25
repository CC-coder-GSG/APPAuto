package site.geonest.qa.core.domain.model

/** 测试工作台（全盘）统计。 */
data class OverallStats(
    val total: Int = 0,
    val closed: Int = 0,
    val pending: Int = 0,
    val readyRate: Int = 0,
)

data class OverallOverview(
    val stats: OverallStats,
    val filteredStats: OverallStats,
    val bugs: List<OverallBug>,
)

/** 全盘 Bug 条目。 */
data class OverallBug(
    val id: Int,
    val bugId: String,
    val zentaoBugId: String?,
    val zentaoBugTitle: String,
    val zentaoLiveStatus: String,
    val zentaoAssignedToName: String,
    val zentaoDeleted: Boolean,
    val requirementId: Int?,
    val closed: Boolean,
    val myTestDone: Boolean,
    val myResolution: String,
    val myComment: String,
    val dispatchedToName: String?,
    val isRetestFailed: Boolean,
    val effectiveStatus: String,
    val majorVersionNo: String,
) {
    val isZentao: Boolean get() = !zentaoBugId.isNullOrBlank()
}
