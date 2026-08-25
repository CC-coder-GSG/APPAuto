package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** GET /workbench/mine 返回数组的单项。 */
@Serializable
data class WorkbenchRequirementDto(
    val id: Int,
    @SerialName("zentao_req_id") val zentaoReqId: String? = null,
    val title: String? = null,
    @SerialName("case_completed") val caseCompleted: Boolean = false,
    @SerialName("test_completed") val testCompleted: Boolean = false,
    @SerialName("major_version_id") val majorVersionId: Int? = null,
    @SerialName("major_version_name") val majorVersionName: String? = null,
    @SerialName("test_notes") val testNotes: String? = null,
    @SerialName("test_notes_updated_at") val testNotesUpdatedAt: String? = null,
    @SerialName("test_notes_updated_by_name") val testNotesUpdatedByName: String? = null,
    @SerialName("auto_linked_case_count") val autoLinkedCaseCount: Int = 0,
    @SerialName("auto_linked_bug_count") val autoLinkedBugCount: Int = 0,
    @SerialName("test_cases") val testCases: List<TestCaseDto> = emptyList(),
    @SerialName("free_bugs") val freeBugs: List<BugDto> = emptyList(),
)

/** GET /bugs/dispatched-to-me 返回项（指派给我的 Bug）。 */
@Serializable
data class DispatchedBugDto(
    val id: Int,
    @SerialName("bug_id") val bugId: String? = null,
    @SerialName("zentao_bug_id") val zentaoBugId: String? = null,
    val closed: Boolean = false,
    @SerialName("req_title") val reqTitle: String? = null,
    @SerialName("test_done") val testDone: Boolean = false,
    val resolution: String? = null,
    @SerialName("newly_found_bug_id") val newlyFoundBugId: String? = null,
)

/** PUT /requirements/{id}/test-execution 请求体——提交即标记测试完成。 */
@Serializable
data class TestExecutionRequest(
    @SerialName("minor_version_id") val minorVersionId: Int,
    @SerialName("result_status") val resultStatus: String = "passed",
    @SerialName("test_completed") val testCompleted: Boolean = true,
    val notes: String? = null,
)

/** PUT /requirements/{id}/test-notes 请求体。 */
@Serializable
data class TestNotesRequest(
    @SerialName("test_notes") val testNotes: String? = null,
)

@Serializable
data class TestCaseDto(
    val id: Int,
    @SerialName("zentao_case_id") val zentaoCaseId: String? = null,
    @SerialName("zentao_case_url") val zentaoCaseUrl: String? = null,
    val bugs: List<BugDto> = emptyList(),
)

@Serializable
data class BugDto(
    val id: Int,
    @SerialName("bug_id") val bugId: String? = null,
    @SerialName("zentao_bug_url") val zentaoBugUrl: String? = null,
    @SerialName("zentao_bug_title") val zentaoBugTitle: String? = null,
    @SerialName("found_minor_version_no") val foundMinorVersionNo: String? = null,
    @SerialName("fixed_minor_version_no") val fixedMinorVersionNo: String? = null,
    @SerialName("dispatched_to_name") val dispatchedToName: String? = null,
)

/** PATCH /requirements/{id}/status 的请求体；null 字段在序列化时省略。 */
@Serializable
data class PatchStatusRequest(
    @SerialName("case_completed") val caseCompleted: Boolean? = null,
    @SerialName("test_completed") val testCompleted: Boolean? = null,
)

@Serializable
data class PatchStatusResponse(
    @SerialName("case_completed") val caseCompleted: Boolean = false,
    @SerialName("test_completed") val testCompleted: Boolean = false,
    val status: String? = null,
    val message: String? = null,
)
