package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** GET /users 返回项（负责人下拉用）。 */
@Serializable
data class UserDto(
    val id: Int,
    val username: String? = null,
    @SerialName("display_name") val displayName: String? = null,
)

/** GET /requirements/admin/list 返回项。 */
@Serializable
data class AdminReqDto(
    val id: Int,
    @SerialName("zentao_req_id") val zentaoReqId: String? = null,
    val title: String? = null,
    @SerialName("owner_id") val ownerId: Int? = null,
    @SerialName("case_completed") val caseCompleted: Boolean = false,
    @SerialName("test_completed") val testCompleted: Boolean = false,
    @SerialName("major_version_name") val majorVersionName: String? = null,
)

/** POST /requirements/assign-and-publish 请求体。 */
@Serializable
data class AssignPublishRequest(
    @SerialName("major_version_id") val majorVersionId: Int,
    val assignments: List<AssignItem>,
)

@Serializable
data class AssignItem(
    @SerialName("requirement_id") val requirementId: Int,
    @SerialName("owner_id") val ownerId: Int? = null,
)

// ---- 进行状态 ----
@Serializable
data class ProgressDto(
    val summary: ProgressSummaryDto = ProgressSummaryDto(),
    val owners: List<OwnerProgressDto> = emptyList(),
    @SerialName("retest_pending_by_major") val retestPendingByMajor: List<RetestPendingDto> = emptyList(),
)

@Serializable
data class ProgressSummaryDto(
    val owners: Int = 0,
    val requirements: Int = 0,
    @SerialName("case_done") val caseDone: Int = 0,
    @SerialName("case_pending") val casePending: Int = 0,
    @SerialName("test_done") val testDone: Int = 0,
    @SerialName("test_pending") val testPending: Int = 0,
    @SerialName("retest_pending_total") val retestPendingTotal: Int = 0,
)

@Serializable
data class OwnerProgressDto(
    @SerialName("owner_id") val ownerId: Int = 0,
    @SerialName("owner_name") val ownerName: String? = null,
    @SerialName("major_summaries") val majorSummaries: List<MajorSummaryDto> = emptyList(),
    val requirements: List<ProgressReqDto> = emptyList(),
)

@Serializable
data class MajorSummaryDto(
    @SerialName("major_version_name") val majorVersionName: String? = null,
    @SerialName("total_requirements") val totalRequirements: Int = 0,
    @SerialName("case_pending") val casePending: Int = 0,
    @SerialName("test_pending") val testPending: Int = 0,
)

@Serializable
data class ProgressReqDto(
    val id: Int,
    @SerialName("zentao_req_id") val zentaoReqId: String? = null,
    val title: String? = null,
    @SerialName("major_version_name") val majorVersionName: String? = null,
    @SerialName("case_completed") val caseCompleted: Boolean = false,
    @SerialName("test_completed") val testCompleted: Boolean = false,
    @SerialName("case_count") val caseCount: Int = 0,
    @SerialName("bug_count") val bugCount: Int = 0,
)

@Serializable
data class RetestPendingDto(
    @SerialName("major_version_name") val majorVersionName: String? = null,
    @SerialName("pending_retest_count") val pendingRetestCount: Int = 0,
)

// ---- 关联需求 ----
@Serializable
data class LinkOptionDto(
    val id: Int,
    @SerialName("zentao_req_id") val zentaoReqId: String? = null,
    val title: String? = null,
    @SerialName("owner_name") val ownerName: String? = null,
    @SerialName("case_count") val caseCount: Int = 0,
    @SerialName("already_linked") val alreadyLinked: Boolean = false,
)

@Serializable
data class LinkMajorRequest(
    @SerialName("target_major_version_id") val targetMajorVersionId: Int,
    @SerialName("source_major_version_id") val sourceMajorVersionId: Int,
    @SerialName("source_requirement_ids") val sourceRequirementIds: List<Int>,
    @SerialName("copy_status") val copyStatus: Boolean = true,
)

@Serializable
data class LinkMajorResponse(
    @SerialName("created_count") val createdCount: Int = 0,
    @SerialName("skipped_count") val skippedCount: Int = 0,
    val message: String? = null,
)

@Serializable
data class SyncReqResponse(
    @SerialName("remote_total") val remoteTotal: Int = 0,
    val created: Int = 0,
    val updated: Int = 0,
)
