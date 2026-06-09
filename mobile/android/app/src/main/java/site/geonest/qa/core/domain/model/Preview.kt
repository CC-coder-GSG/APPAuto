package site.geonest.qa.core.domain.model

/** 可预览的禅道实体类型。 */
enum class PreviewKind(val label: String, val prefix: String) {
    STORY("需求", "s#"),
    BUG("Bug", "b#"),
    TESTCASE("用例", "case#"),
}

/** 需求实时状态（卡片徽标用）。 */
data class StoryStatus(
    val statusZh: String,
    val stageZh: String,
)

/**
 * 预览内容：头部信息原生渲染，正文富文本交给 WebView 渲染（含图片/表格/流转记录）。
 */
data class PreviewContent(
    val kind: PreviewKind,
    val idLabel: String,
    val title: String,
    val metaLine: String,
    val bodyHtml: String,
    val zentaoUrl: String?,
)
