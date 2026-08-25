package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** GET /overall-test/overview 响应。 */
@Serializable
data class OverallOverviewDto(
    @SerialName("major_version_id") val majorVersionId: Int? = null,
    @SerialName("all_versions_mode") val allVersionsMode: Boolean = false,
    @SerialName("bug_pool") val bugPool: List<OverallBugDto> = emptyList(),
    val stats: OverallStatsDto = OverallStatsDto(),
    @SerialName("filtered_stats") val filteredStats: OverallStatsDto = OverallStatsDto(),
)

@Serializable
data class OverallStatsDto(
    val total: Int = 0,
    val closed: Int = 0,
    val pending: Int = 0,
    @SerialName("ready_rate") val readyRate: Int = 0,
)

@Serializable
data class OverallBugDto(
    val id: Int,
    @SerialName("bug_id") val bugId: String? = null,
    @SerialName("zentao_bug_id") val zentaoBugId: String? = null,
    @SerialName("zentao_bug_url") val zentaoBugUrl: String? = null,
    @SerialName("zentao_bug_title") val zentaoBugTitle: String? = null,
    @SerialName("zentao_live_status") val zentaoLiveStatus: String? = null,
    @SerialName("zentao_assigned_to_name") val zentaoAssignedToName: String? = null,
    @SerialName("zentao_deleted") val zentaoDeleted: Boolean = false,
    @SerialName("source_type") val sourceType: String? = null,
    @SerialName("requirement_id") val requirementId: Int? = null,
    val closed: Boolean = false,
    @SerialName("my_test_done") val myTestDone: Boolean = false,
    @SerialName("my_resolution") val myResolution: String? = null,
    @SerialName("my_comment") val myComment: String? = null,
    @SerialName("dispatched_to_name") val dispatchedToName: String? = null,
    @SerialName("is_retest_failed") val isRetestFailed: Boolean = false,
    @SerialName("effective_status") val effectiveStatus: String? = null,
    @SerialName("major_version_no") val majorVersionNo: String? = null,
)

/** PUT /overall-test/bugs/{id}/result 请求体。 */
@Serializable
data class OverallResultRequest(
    @SerialName("minor_version_id") val minorVersionId: Int,
    @SerialName("test_done") val testDone: Boolean,
    @SerialName("newly_found_bug_id") val newlyFoundBugId: String? = null,
    val resolution: String = "fixed",
)

/** POST /zentao/bugs/{id}/close 请求体。 */
@Serializable
data class ZentaoCloseRequest(
    val comment: String? = null,
)
