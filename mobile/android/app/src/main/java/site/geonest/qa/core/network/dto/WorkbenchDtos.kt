package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** GET /requirements/my-workbench 返回数组的单项。 */
@Serializable
data class WorkbenchRequirementDto(
    val id: Int,
    @SerialName("zentao_req_id") val zentaoReqId: String? = null,
    val title: String? = null,
    @SerialName("case_completed") val caseCompleted: Boolean = false,
    @SerialName("test_completed") val testCompleted: Boolean = false,
    @SerialName("major_version_name") val majorVersionName: String? = null,
    @SerialName("test_notes") val testNotes: String? = null,
    @SerialName("test_cases") val testCases: List<TestCaseDto> = emptyList(),
    @SerialName("free_bugs") val freeBugs: List<BugDto> = emptyList(),
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
