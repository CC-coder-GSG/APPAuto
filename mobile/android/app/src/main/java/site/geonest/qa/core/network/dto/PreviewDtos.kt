package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * hydrate/stories 单条状态（map 的 value）。
 *
 * 注：需求/Bug/用例的详情预览不再用强类型 DTO——禅道字段数字/字符串混用，
 * 统一在 ZentaoRepository 里按 JsonObject 容错解析。
 */
@Serializable
data class StoryStatusDto(
    val id: Int? = null,
    @SerialName("status_zh") val statusZh: String? = null,
    @SerialName("stage_zh") val stageZh: String? = null,
)
