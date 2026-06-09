package site.geonest.qa.core.domain.model

/** 负责人候选（用户）。 */
data class UserOption(val id: Int, val name: String)

/** 任务创建台中的需求行（可改负责人）。 */
data class AssignRequirement(
    val id: Int,
    val zentaoReqId: String,
    val title: String,
    val ownerId: Int?,
    val caseCompleted: Boolean,
    val testCompleted: Boolean,
    val majorVersionName: String,
)

// ---- 进行状态 ----
data class AssignProgress(
    val summary: ProgressSummary,
    val owners: List<OwnerProgress>,
    val retestPending: List<RetestPending>,
)

data class ProgressSummary(
    val owners: Int,
    val requirements: Int,
    val caseDone: Int,
    val casePending: Int,
    val testDone: Int,
    val testPending: Int,
    val retestPendingTotal: Int,
)

data class OwnerProgress(
    val ownerId: Int,
    val ownerName: String,
    val majorSummaries: List<MajorSummary>,
    val requirements: List<ProgressReq>,
) {
    fun visibleReqs(pendingOnly: Boolean): List<ProgressReq> =
        if (pendingOnly) requirements.filter { !(it.caseCompleted && it.testCompleted) } else requirements
}

data class MajorSummary(
    val majorVersionName: String,
    val totalRequirements: Int,
    val casePending: Int,
    val testPending: Int,
)

data class ProgressReq(
    val id: Int,
    val zentaoReqId: String,
    val title: String,
    val majorVersionName: String,
    val caseCompleted: Boolean,
    val testCompleted: Boolean,
    val caseCount: Int,
    val bugCount: Int,
)

data class RetestPending(val majorVersionName: String, val count: Int)

// ---- 关联需求 ----
data class LinkOption(
    val id: Int,
    val zentaoReqId: String,
    val title: String,
    val ownerName: String,
    val caseCount: Int,
    val alreadyLinked: Boolean,
)
