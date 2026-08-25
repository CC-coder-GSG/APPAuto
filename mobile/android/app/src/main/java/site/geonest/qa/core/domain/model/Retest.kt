package site.geonest.qa.core.domain.model

/** 复测工作台领域模型，与传输层解耦。未来 KMP 共享候选。 */
data class RetestRequirement(
    val id: Int,
    val zentaoReqId: String,
    val title: String,
    val majorVersionName: String,
    /** 负责测试该需求的人（即我要替其复测的对象）。 */
    val owner: String,
    val retestCompleted: Boolean,
    val retestPassed: Boolean?,
    val retestedBy: String?,
    val testCases: List<WbTestCase>,
    val freeBugs: List<WbBug>,
    val retestBugs: List<WbBug>,
) {
    /** 卡片上可见的 Bug 总数：自由 Bug + 复测 Bug + 用例下挂的 Bug。 */
    val bugCount: Int get() = freeBugs.size + retestBugs.size + testCases.sumOf { it.bugs.size }
}
