package site.geonest.qa.core.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// ---- 绑定 ----
@Serializable
data class JenkinsBindingResponse(
    val binding: JenkinsBindingDto? = null,
    val defaults: JenkinsDefaultsDto = JenkinsDefaultsDto(),
)

@Serializable
data class JenkinsBindingDto(
    @SerialName("base_url") val baseUrl: String = "",
    @SerialName("jenkins_account") val account: String = "",
    @SerialName("has_token") val hasToken: Boolean = false,
    @SerialName("last_check_status") val lastCheckStatus: String? = null,
    @SerialName("last_error_message") val lastErrorMessage: String? = null,
    @SerialName("last_check_at") val lastCheckAt: String? = null,
)

@Serializable
data class JenkinsDefaultsDto(
    @SerialName("base_url") val baseUrl: String = "",
    val view: String = "",
)

@Serializable
data class JenkinsBindingRequest(
    @SerialName("base_url") val baseUrl: String,
    @SerialName("jenkins_account") val account: String,
    @SerialName("jenkins_token") val token: String,
)

@Serializable
data class JenkinsTestResponse(val ok: Boolean = false, val message: String? = null)

@Serializable
data class JenkinsSaveResponse(
    val ok: Boolean = false,
    val verified: Boolean = false,
    val message: String? = null,
)

// ---- 视图 ----
@Serializable
data class JenkinsViewsResponse(
    val views: List<JenkinsViewDto> = emptyList(),
    @SerialName("default_view") val defaultView: String? = null,
)

@Serializable
data class JenkinsViewDto(val name: String = "")

// ---- Job ----
@Serializable
data class JenkinsJobsResponse(
    val view: String = "",
    val jobs: List<JenkinsJobDto> = emptyList(),
)

@Serializable
data class JenkinsJobDto(
    val name: String = "",
    val color: String? = null,
    val buildable: Boolean = true,
)

@Serializable
data class JenkinsJobDetailDto(
    val name: String = "",
    val color: String? = null,
    val buildable: Boolean = true,
    val description: String? = null,
    val builds: List<JenkinsBuildDto> = emptyList(),
    val property: List<JenkinsPropertyDto> = emptyList(),
    val healthReport: List<JenkinsHealthDto> = emptyList(),
)

@Serializable
data class JenkinsPropertyDto(
    val parameterDefinitions: List<JenkinsParamDto> = emptyList(),
)

@Serializable
data class JenkinsParamDto(val name: String = "")

@Serializable
data class JenkinsHealthDto(val score: Int = 0, val description: String? = null)

@Serializable
data class JenkinsBuildDto(
    val number: Int = 0,
    val result: String? = null,
    val building: Boolean = false,
    val timestamp: Long = 0,
    val duration: Long = 0,
)

@Serializable
data class JenkinsBuildRequest(val params: Map<String, String>? = null)

@Serializable
data class JenkinsBuildTriggerResponse(
    val ok: Boolean = false,
    val message: String? = null,
    @SerialName("queue_item_url") val queueItemUrl: String? = null,
)

@Serializable
data class JenkinsQueueResponse(
    val cancelled: Boolean = false,
    val started: Boolean = false,
    @SerialName("build_number") val buildNumber: Int? = null,
)

@Serializable
data class JenkinsBuildDetailDto(
    val artifacts: List<JenkinsArtifactDto> = emptyList(),
)

@Serializable
data class JenkinsArtifactDto(
    val fileName: String = "",
    val relativePath: String = "",
)
