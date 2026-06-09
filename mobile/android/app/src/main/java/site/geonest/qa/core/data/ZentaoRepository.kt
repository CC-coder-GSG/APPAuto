package site.geonest.qa.core.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.decodeFromJsonElement
import retrofit2.HttpException
import site.geonest.qa.core.common.ApiResult
import site.geonest.qa.core.domain.model.PreviewContent
import site.geonest.qa.core.domain.model.PreviewKind
import site.geonest.qa.core.domain.model.StoryStatus
import site.geonest.qa.core.network.QaApi
import site.geonest.qa.core.network.dto.StoryStatusDto
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 禅道集成仓储：实体预览（需求/Bug/用例）+ 需求实时状态批量拉取。
 * 预览响应统一按 JsonObject 容错解析——禅道字段数字/字符串混用，强类型反序列化会报
 * "Unexpected JSON token"。正文为禅道富 HTML，交给 WebView 渲染。
 */
@Singleton
class ZentaoRepository @Inject constructor(
    private val api: QaApi,
    private val json: Json,
) {
    suspend fun preview(kind: PreviewKind, id: Int): ApiResult<PreviewContent> = runCatchingApi {
        val obj = when (kind) {
            PreviewKind.STORY -> api.storyDetail(id)
            PreviewKind.BUG -> api.bugPreview(id)
            PreviewKind.TESTCASE -> api.testcaseDetail(id)
        }
        // 后端错误以 { "error": ..., "message": ... } 形式返回（HTTP 200）。
        if (obj.str("error") != null) fail(obj.str("message") ?: "加载失败")
        when (kind) {
            PreviewKind.STORY -> obj.toStory(id)
            PreviewKind.BUG -> obj.toBug(id)
            PreviewKind.TESTCASE -> obj.toTestcase(id)
        }
    }

    /** 批量需求状态。无禅道绑定时后端返回空对象，这里得到空 Map。 */
    suspend fun storyStatuses(ids: List<Int>): Map<Int, StoryStatus> {
        if (ids.isEmpty()) return emptyMap()
        return try {
            val obj: JsonObject = api.hydrateStories(ids.joinToString(","))
            val out = mutableMapOf<Int, StoryStatus>()
            for ((key, value) in obj) {
                if (key == "__fetch_errors__") continue
                val sid = key.toIntOrNull() ?: continue
                val dto = runCatching { json.decodeFromJsonElement<StoryStatusDto>(value) }.getOrNull() ?: continue
                out[sid] = StoryStatus(statusZh = dto.statusZh.orEmpty(), stageZh = dto.stageZh.orEmpty())
            }
            out
        } catch (_: Exception) {
            emptyMap() // 状态徽标为增强信息，失败静默
        }
    }

    private inline fun <T> runCatchingApi(block: () -> T): ApiResult<T> = try {
        ApiResult.Success(block())
    } catch (e: HttpException) {
        ApiResult.Error(if (e.code() == 401) "登录已失效，请重新登录" else "加载预览失败（${e.code()}）", e.code())
    } catch (e: IOException) {
        ApiResult.Error("网络连接失败，请检查网络后重试")
    } catch (e: Exception) {
        ApiResult.Error(e.message ?: "加载预览失败")
    }
}

// ─────────────────────────── JsonObject 容错读取 ───────────────────────────
private fun JsonObject.str(key: String): String? {
    val el = this[key]
    if (el == null || el is JsonNull) return null
    return (el as? JsonPrimitive)?.content?.takeIf { it.isNotEmpty() && it != "null" }
}

private fun JsonObject.bool(key: String): Boolean {
    val c = (this[key] as? JsonPrimitive)?.content ?: return false
    return c.equals("true", ignoreCase = true) || c == "1"
}

private fun JsonObject.objList(key: String): List<JsonObject> =
    (this[key] as? JsonArray)?.mapNotNull { it as? JsonObject } ?: emptyList()

// ─────────────────────────── 组装 ───────────────────────────
private fun fail(message: String): Nothing = throw IllegalStateException(message)
private fun esc(s: String?): String = (s ?: "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
private fun fmtDate(s: String?): String = (s ?: "").take(19).replace("T", " ")

private fun JsonObject.toStory(reqId: Int): PreviewContent {
    val meta = buildList {
        str("status_zh")?.let { add("状态：$it") }
        str("stage_zh")?.let { add("阶段：$it") }
        str("assigned_to")?.let { add("指派给：$it") }
        str("opened_by")?.let { add("创建：$it ${fmtDate(str("opened_date"))}") }
        str("module")?.let { add("模块：$it") }
        str("pri")?.let { add("P$it") }
    }.joinToString(" · ")
    val spec = str("spec").orEmpty().ifBlank { emptyHint("该需求暂无描述") }
    val verify = str("verify").orEmpty().ifBlank { emptyHint("该需求暂无验收标准") }
    val body = """
        <div class="card gray"><div class="h">📝 需求描述</div><div class="rich">$spec</div></div>
        <div class="card green"><div class="h">✅ 验收标准</div><div class="rich">$verify</div></div>
    """.trimIndent()
    return PreviewContent(PreviewKind.STORY, "s#${str("id") ?: reqId}", str("title").orEmpty(), meta, htmlDoc(body), str("zentao_url"))
}

private fun JsonObject.toBug(bugId: Int): PreviewContent {
    val meta = buildList {
        str("status_zh")?.let { add("状态：$it") }
        str("resolution_zh")?.let { add("解决方案：$it") }
        str("type_zh")?.let { add("类型：$it") }
        str("assigned_to")?.let { add("指派给：$it") }
        str("opened_by")?.let { add("提出：$it ${fmtDate(str("opened_date"))}") }
        str("resolved_by")?.let { add("解决：$it ${fmtDate(str("resolved_date"))}") }
        str("opened_build")?.let { add("发现版本：$it") }
        str("resolved_build")?.let { add("解决版本：$it") }
        str("module")?.let { add("模块：$it") }
    }.joinToString(" · ")

    val files = objList("files")
    val filesHtml = if (files.isNotEmpty()) {
        "<div class=\"files\">" + files.joinToString("") { f ->
            val url = esc(f.str("url"))
            if (f.bool("is_image")) "<a href=\"$url\"><img src=\"$url\" alt=\"${esc(f.str("title"))}\"></a>"
            else "<span class=\"file\">📎 ${esc(f.str("title") ?: f.str("url"))}</span>"
        } + "</div>"
    } else emptyHint("无附件")

    val actions = objList("actions")
    val actionsHtml = if (actions.isNotEmpty()) {
        "<div class=\"card white\"><div class=\"h\">🕘 流转记录</div>" + actions.joinToString("") { a ->
            "<div class=\"act\"><div class=\"act-h\">${esc(fmtDate(a.str("date")))} · <b>${esc(a.str("actor"))}</b> · ${esc(a.str("action_zh") ?: a.str("action"))}</div>" +
                (a.str("comment")?.let { "<div class=\"rich sm\">$it</div>" } ?: "") + "</div>"
        } + "</div>"
    } else ""

    val steps = str("steps").orEmpty().ifBlank { emptyHint("无步骤描述") }
    val body = """
        <div class="card red"><div class="h">🐞 复现步骤</div><div class="rich">$steps</div></div>
        <div class="card gray"><div class="h">📎 附件</div>$filesHtml</div>
        $actionsHtml
    """.trimIndent()
    return PreviewContent(PreviewKind.BUG, "b#${str("id") ?: bugId}", str("title").orEmpty(), meta, htmlDoc(body), str("zentao_url"))
}

private fun JsonObject.toTestcase(caseId: Int): PreviewContent {
    val meta = buildList {
        str("type_zh")?.let { add("类型：$it") }
        str("status_zh")?.let { add("状态：$it") }
        str("pri")?.let { add("P$it") }
        str("story")?.let { add("关联需求：$it") }
        str("module")?.let { add("模块：$it") }
        str("last_run_result")?.let { add("上次结果：$it") }
    }.joinToString(" · ")

    val steps = objList("steps")
    val stepsHtml = if (steps.isNotEmpty()) {
        "<table><thead><tr><th>#</th><th>步骤</th><th>预期结果</th></tr></thead><tbody>" +
            steps.joinToString("") { s ->
                "<tr><td>${esc(s.str("name"))}</td><td><div class=\"rich\">${s.str("step").orEmpty()}</div></td><td><div class=\"rich\">${s.str("expect").orEmpty()}</div></td></tr>"
            } + "</tbody></table>"
    } else emptyHint("该用例暂无步骤")

    val pre = str("precondition")?.let {
        "<div class=\"card amber\"><div class=\"h\">⚙️ 前置条件</div><div class=\"rich\">$it</div></div>"
    } ?: ""
    val body = "$pre<div class=\"card gray\"><div class=\"h\">🧪 用例步骤</div>$stepsHtml</div>"
    return PreviewContent(PreviewKind.TESTCASE, "case#${str("id") ?: caseId}", str("title").orEmpty(), meta, htmlDoc(body), str("zentao_url"))
}

private fun emptyHint(text: String) = "<span class=\"muted\">$text</span>"

/** 包成完整 HTML 文档；样式贴合移动端阅读。图片相对路径由 WebView baseUrl 解析、拦截器注入鉴权。 */
private fun htmlDoc(body: String): String = """
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=3">
<style>
  body { margin:0; padding:12px; font-size:15px; line-height:1.7; color:#1f2937;
         font-family:-apple-system,Roboto,'Helvetica Neue',Arial,sans-serif; -webkit-text-size-adjust:100%; }
  .card { border:1px solid #e2e8f0; border-radius:10px; padding:12px 14px; margin-bottom:12px; background:#f8fafc; }
  .card.green { border-color:#bbf7d0; background:#f0fdf4; } .card.green .h { color:#166534; }
  .card.red { border-color:#fecaca; background:#fef2f2; } .card.red .h { color:#991b1b; }
  .card.amber { background:#fffbeb; } .card.amber .h { color:#92400e; }
  .card.white { background:#fff; }
  .h { font-weight:600; color:#0f172a; margin-bottom:8px; }
  .rich { word-break:break-word; } .rich.sm { font-size:13px; margin-top:4px; }
  .rich img, img { max-width:100%; height:auto; border-radius:4px; margin:4px 0; }
  .rich table, table { border-collapse:collapse; margin:6px 0; width:100%; }
  .rich th, .rich td, th, td { border:1px solid #cbd5e1; padding:6px 8px; vertical-align:top; text-align:left; }
  th { background:#f1f5f9; }
  a { color:#2563eb; }
  .muted { color:#94a3b8; }
  .files { display:flex; flex-wrap:wrap; gap:8px; }
  .files img { max-width:160px; max-height:120px; border:1px solid #e2e8f0; }
  .file { border:1px solid #cbd5e1; border-radius:6px; padding:4px 10px; font-size:13px; background:#f8fafc; }
  .act { border-left:3px solid #cbd5e1; padding:4px 10px; margin-bottom:8px; }
  .act-h { font-size:12px; color:#64748b; }
</style></head><body>$body</body></html>
""".trimIndent()
