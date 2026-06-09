package site.geonest.qa.core.domain.model

/** 工作台领域模型，与传输层解耦。未来 KMP 共享候选。 */
data class WorkbenchRequirement(
    val id: Int,
    val zentaoReqId: String,
    val title: String,
    val caseCompleted: Boolean,
    val testCompleted: Boolean,
    val majorVersionId: Int?,
    val majorVersionName: String,
    val testNotes: String?,
    val testNotesUpdatedAt: String?,
    val testNotesUpdatedByName: String?,
    val autoLinkedCaseCount: Int,
    val autoLinkedBugCount: Int,
    val testCases: List<WbTestCase>,
    val freeBugs: List<WbBug>,
) {
    val bugCount: Int get() = freeBugs.size + testCases.sumOf { it.bugs.size }
    val hasNotes: Boolean get() = !testNotes.isNullOrBlank()
    val fullyCompleted: Boolean get() = caseCompleted && testCompleted
}

/** 指派给我的 Bug。 */
data class DispatchedBug(
    val id: Int,
    val bugId: String,
    val zentaoBugId: String?,
    val closed: Boolean,
    val reqTitle: String,
    val testDone: Boolean,
    val resolution: String,
    val newlyFoundBugId: String?,
)

data class WbTestCase(
    val id: Int,
    val zentaoCaseId: String,
    val zentaoCaseUrl: String?,
    val bugs: List<WbBug>,
)

data class WbBug(
    val id: Int,
    val bugId: String,
    val zentaoBugUrl: String?,
    val title: String,
    val foundVersion: String?,
    val fixedVersion: String?,
    val dispatchedToName: String?,
)
