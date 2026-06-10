package site.geonest.qa.core.domain.model

data class JenkinsBinding(
    val baseUrl: String,
    val account: String,
    val hasToken: Boolean,
    val lastCheckStatus: String?,
    val lastErrorMessage: String?,
) {
    val verified: Boolean get() = lastCheckStatus == "ok" || lastCheckStatus == "success"
}

data class JenkinsJob(
    val name: String,
    val color: String?,
    val buildable: Boolean,
)

data class JenkinsJobDetail(
    val name: String,
    val color: String?,
    val buildable: Boolean,
    val description: String?,
    val healthScore: Int?,
    val params: List<String>,
    val builds: List<JenkinsBuild>,
)

data class JenkinsBuild(
    val number: Int,
    val result: String?,
    val building: Boolean,
    val timestamp: Long,
    val durationMs: Long,
)

data class JenkinsArtifact(
    val fileName: String,
    val relativePath: String,
)
