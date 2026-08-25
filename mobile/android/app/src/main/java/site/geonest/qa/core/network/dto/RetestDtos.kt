package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * GET /retest/workbench 返回数组的单项。
 * 复用 [TestCaseDto] / [BugDto]——后端 bug 项多出的 is_retest_failed/closed 字段被忽略。
 */
@Serializable
data class RetestRequirementDto(
    val id: Int,
    @SerialName("zentao_req_id") val zentaoReqId: String? = null,
    val title: String? = null,
    @SerialName("major_version_name") val majorVersionName: String? = null,
    val owner: String? = null,
    @SerialName("retest_completed") val retestCompleted: Boolean = false,
    @SerialName("retest_passed") val retestPassed: Boolean? = null,
    @SerialName("retested_by") val retestedBy: String? = null,
    @SerialName("test_cases") val testCases: List<TestCaseDto> = emptyList(),
    @SerialName("free_bugs") val freeBugs: List<BugDto> = emptyList(),
    @SerialName("retest_bugs") val retestBugs: List<BugDto> = emptyList(),
)

/** PUT /requirements/{id}/retest 的请求体；null 字段在序列化时省略（explicitNulls=false）。 */
@Serializable
data class RetestSubmitRequest(
    @SerialName("retest_completed") val retestCompleted: Boolean,
    @SerialName("retest_passed") val retestPassed: Boolean? = null,
    @SerialName("retest_minor_version_id") val retestMinorVersionId: Int? = null,
)

@Serializable
data class RetestResultResponse(
    val message: String? = null,
)
