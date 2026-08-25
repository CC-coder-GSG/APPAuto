package site.geonest.qa.core.network

import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.Field
import retrofit2.http.FormUrlEncoded
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query
import kotlinx.serialization.json.JsonObject
import okhttp3.MultipartBody
import retrofit2.http.Multipart
import retrofit2.http.Part
import site.geonest.qa.core.network.dto.AckResponse
import site.geonest.qa.core.network.dto.AdminReqDto
import site.geonest.qa.core.network.dto.JenkinsBindingRequest
import site.geonest.qa.core.network.dto.JenkinsBindingResponse
import site.geonest.qa.core.network.dto.JenkinsBuildDetailDto
import site.geonest.qa.core.network.dto.JenkinsBuildRequest
import site.geonest.qa.core.network.dto.JenkinsBuildTriggerResponse
import site.geonest.qa.core.network.dto.JenkinsJobDetailDto
import site.geonest.qa.core.network.dto.JenkinsJobsResponse
import site.geonest.qa.core.network.dto.JenkinsQueueResponse
import site.geonest.qa.core.network.dto.JenkinsSaveResponse
import site.geonest.qa.core.network.dto.JenkinsTestResponse
import site.geonest.qa.core.network.dto.JenkinsViewsResponse
import site.geonest.qa.core.network.dto.DataOverviewDto
import site.geonest.qa.core.network.dto.DisplayNameRequest
import site.geonest.qa.core.network.dto.GovernanceDto
import site.geonest.qa.core.network.dto.ReportSummaryDto
import site.geonest.qa.core.network.dto.RequirementUpsertRequest
import site.geonest.qa.core.network.dto.RoleUpdateRequest
import site.geonest.qa.core.network.dto.TabPermissionsRequest
import site.geonest.qa.core.network.dto.TeamStatusRequest
import site.geonest.qa.core.network.dto.UserCreateRequest
import site.geonest.qa.core.network.dto.VersionBugDto
import site.geonest.qa.core.network.dto.CadBoardDetailDto
import site.geonest.qa.core.network.dto.CadBoardDto
import site.geonest.qa.core.network.dto.CadBoardRequest
import site.geonest.qa.core.network.dto.CadCreatedDto
import site.geonest.qa.core.network.dto.CadItemRequest
import site.geonest.qa.core.network.dto.CadRecordRequest
import site.geonest.qa.core.network.dto.CadVersionRequest
import site.geonest.qa.core.network.dto.AssignPublishRequest
import site.geonest.qa.core.network.dto.DispatchedBugDto
import site.geonest.qa.core.network.dto.LinkMajorRequest
import site.geonest.qa.core.network.dto.LinkMajorResponse
import site.geonest.qa.core.network.dto.LinkOptionDto
import site.geonest.qa.core.network.dto.ProgressDto
import site.geonest.qa.core.network.dto.SyncReqResponse
import site.geonest.qa.core.network.dto.UserDto
import site.geonest.qa.core.network.dto.MeResponse
import site.geonest.qa.core.network.dto.OverallOverviewDto
import site.geonest.qa.core.network.dto.OverallResultRequest
import site.geonest.qa.core.network.dto.PatchStatusRequest
import site.geonest.qa.core.network.dto.PatchStatusResponse
import site.geonest.qa.core.network.dto.RetestRequirementDto
import site.geonest.qa.core.network.dto.RetestResultResponse
import site.geonest.qa.core.network.dto.RetestSubmitRequest
import site.geonest.qa.core.network.dto.SoftwareDto
import site.geonest.qa.core.network.dto.TestExecutionRequest
import site.geonest.qa.core.network.dto.TestNotesRequest
import site.geonest.qa.core.network.dto.TokenResponse
import site.geonest.qa.core.network.dto.VersionDto
import site.geonest.qa.core.network.dto.WorkbenchRequirementDto
import site.geonest.qa.core.network.dto.ZentaoCloseRequest

/**
 * 后端 REST 接口声明。对接现有 FastAPI：
 *  - POST /auth/token  : OAuth2 密码模式，application/x-www-form-urlencoded
 *  - GET  /auth/me     : 取当前用户与权限
 *
 * 后续按里程碑逐步补充 my-workbench / retest / task-board 等接口。
 */
interface QaApi {

    @FormUrlEncoded
    @POST("auth/token")
    suspend fun login(
        @Field("username") username: String,
        @Field("password") password: String,
    ): TokenResponse

    @GET("auth/me")
    suspend fun me(): MeResponse

    // ---- 目录：软件 / 版本 ----
    @GET("softwares")
    suspend fun softwares(): List<SoftwareDto>

    @GET("versions")
    suspend fun versions(@Query("software_id") softwareId: Int? = null): List<VersionDto>

    /** 需求工作台（与网页一致）。mode=version 需 major_version_id；mode=all_pending 跨版本待办。 */
    @GET("workbench/mine")
    suspend fun workbenchMine(
        @Query("mode") mode: String = "version",
        @Query("major_version_id") majorVersionId: Int? = null,
        @Query("software_id") softwareId: Int? = null,
    ): List<WorkbenchRequirementDto>

    /** 指派给我的 Bug（按大版本）。 */
    @GET("bugs/dispatched-to-me")
    suspend fun dispatchedToMe(
        @Query("major_version_id") majorVersionId: Int,
    ): List<DispatchedBugDto>

    /** 勾选"用例完成 / 测试完成（仅用于取消）"。 */
    @PATCH("requirements/{id}/status")
    suspend fun patchRequirementStatus(
        @Path("id") requirementId: Int,
        @Body body: PatchStatusRequest,
    ): PatchStatusResponse

    /** 提交测试执行——即标记需求测试完成（需小版本 + 结果 + 要点）。 */
    @PUT("requirements/{id}/test-execution")
    suspend fun submitTestExecution(
        @Path("id") requirementId: Int,
        @Body body: TestExecutionRequest,
    ): AckResponse

    /** 保存需求测试要点。 */
    @PUT("requirements/{id}/test-notes")
    suspend fun updateTestNotes(
        @Path("id") requirementId: Int,
        @Body body: TestNotesRequest,
    ): AckResponse

    // ---- 测试工作台（全盘） ----
    @GET("overall-test/overview")
    suspend fun overallOverview(
        @Query("major_version_id") majorVersionId: Int,
        @Query("software_id") softwareId: Int? = null,
        @Query("statuses") statuses: String? = null,
        @Query("keyword") keyword: String? = null,
    ): OverallOverviewDto

    @PUT("overall-test/bugs/{id}/result")
    suspend fun submitOverallResult(
        @Path("id") bugTrackId: Int,
        @Body body: OverallResultRequest,
    ): AckResponse

    @POST("zentao/bugs/{id}/close")
    suspend fun closeZentaoBug(
        @Path("id") zentaoBugId: Int,
        @Body body: ZentaoCloseRequest,
    ): AckResponse

    /** 复测工作台。mode=all_pending 跨版本列出所有待复测（非本人负责、已测试完成、未复测）的需求。 */
    @GET("retest/workbench")
    suspend fun retestWorkbench(
        @Query("mode") mode: String = "all_pending",
        @Query("major_version_id") majorVersionId: Int? = null,
        @Query("software_id") softwareId: Int? = null,
    ): List<RetestRequirementDto>

    /** 提交复测结论（通过 / 打回 / 撤销）。 */
    @PUT("requirements/{id}/retest")
    suspend fun submitRetest(
        @Path("id") requirementId: Int,
        @Body body: RetestSubmitRequest,
    ): RetestResultResponse

    // ---- 禅道预览 / 状态 ----
    // 禅道字段类型多变（数字/字符串混用），统一按 JsonObject 容错解析，避免反序列化报错。
    @GET("zentao/story/{id}/detail")
    suspend fun storyDetail(@Path("id") storyId: Int): JsonObject

    @GET("zentao/bugs/{id}/preview")
    suspend fun bugPreview(@Path("id") zentaoBugId: Int): JsonObject

    @GET("zentao/testcase/{id}/detail")
    suspend fun testcaseDetail(@Path("id") caseId: Int): JsonObject

    /** 批量拉取需求实时状态。返回 { "<id>": {status_zh, stage_zh}, "__fetch_errors__": [...] }。 */
    @GET("zentao/hydrate/stories")
    suspend fun hydrateStories(@Query("ids") ids: String): JsonObject

    // ---- 任务分配台（管理员，需 allowed_tabs 含 assign）----
    @GET("users")
    suspend fun users(): List<UserDto>

    @GET("requirements/admin/list")
    suspend fun adminReqList(
        @Query("major_version_id") majorVersionId: Int? = null,
        @Query("software_id") softwareId: Int? = null,
    ): List<AdminReqDto>

    @GET("requirements/admin/progress")
    suspend fun adminProgress(
        @Query("major_version_id") majorVersionId: Int? = null,
        @Query("software_id") softwareId: Int? = null,
    ): ProgressDto

    @POST("requirements/assign-and-publish")
    suspend fun assignAndPublish(@Body body: AssignPublishRequest): AckResponse

    @POST("requirements/admin/sync-zentao")
    suspend fun syncZentaoRequirements(@Query("major_version_id") majorVersionId: Int): SyncReqResponse

    @GET("requirements/admin/link-options")
    suspend fun linkOptions(
        @Query("source_major_version_id") sourceMajorVersionId: Int,
        @Query("target_major_version_id") targetMajorVersionId: Int,
    ): List<LinkOptionDto>

    @POST("requirements/admin/link-major")
    suspend fun linkMajor(@Body body: LinkMajorRequest): LinkMajorResponse

    // ---- CAD 测试统计 ----
    @GET("api/cad/boards")
    suspend fun cadBoards(): List<CadBoardDto>

    @POST("api/cad/boards")
    suspend fun cadCreateBoard(@Body body: CadBoardRequest): CadCreatedDto

    @POST("api/cad/boards/{id}/versions")
    suspend fun cadCreateVersion(@Path("id") boardId: Int, @Body body: CadVersionRequest): CadCreatedDto

    @GET("api/cad/boards/{id}")
    suspend fun cadBoard(@Path("id") boardId: Int): CadBoardDetailDto

    @POST("api/cad/records")
    suspend fun cadSaveRecord(@Body body: CadRecordRequest): AckResponse

    @POST("api/cad/boards/{id}/items")
    suspend fun cadCreateItem(@Path("id") boardId: Int, @Body body: CadItemRequest): AckResponse

    @Multipart
    @POST("api/cad/records/attachment")
    suspend fun cadUploadAttachment(
        @Query("item_id") itemId: Int,
        @Query("version_id") versionId: Int,
        @Query("kind") kind: String,
        @Part file: MultipartBody.Part,
    ): AckResponse

    @DELETE("api/cad/attachments/{id}")
    suspend fun cadDeleteAttachment(@Path("id") attachmentId: Int): AckResponse

    // ---- 报表中心 ----
    @GET("reports/summary")
    suspend fun reportsSummary(
        @Query("start_date") startDate: String,
        @Query("end_date") endDate: String,
        @Query("user_id") userId: Int? = null,
        @Query("major_version_id") majorVersionId: Int? = null,
        @Query("software_id") softwareId: Int? = null,
    ): ReportSummaryDto

    @GET("reports/governance")
    suspend fun reportsGovernance(
        @Query("start_date") startDate: String,
        @Query("end_date") endDate: String,
        @Query("major_version_id") majorVersionId: Int? = null,
        @Query("software_id") softwareId: Int? = null,
    ): GovernanceDto

    @GET("reports/version-bugs")
    suspend fun reportsVersionBugs(
        @Query("major_version_id") majorVersionId: Int? = null,
    ): List<VersionBugDto>

    // ---- 数据管理台（admin / allowed_tabs 含 data）----
    @GET("admin/data-overview")
    suspend fun dataOverview(): DataOverviewDto

    @POST("users")
    suspend fun createUser(@Body body: UserCreateRequest): AckResponse

    @PUT("users/{id}/role")
    suspend fun updateUserRole(@Path("id") userId: Int, @Body body: RoleUpdateRequest): AckResponse

    @PUT("users/{id}/team-status")
    suspend fun updateUserTeamStatus(@Path("id") userId: Int, @Body body: TeamStatusRequest): AckResponse

    @PUT("users/{id}/tab-permissions")
    suspend fun updateUserTabPermissions(@Path("id") userId: Int, @Body body: TabPermissionsRequest): AckResponse

    @PUT("users/{id}/display-name")
    suspend fun updateUserDisplayName(@Path("id") userId: Int, @Body body: DisplayNameRequest): AckResponse

    @DELETE("users/{id}")
    suspend fun deleteUser(@Path("id") userId: Int): AckResponse

    @POST("requirements")
    suspend fun createRequirement(@Body body: RequirementUpsertRequest): AckResponse

    @PUT("requirements/{id}")
    suspend fun updateRequirement(@Path("id") requirementId: Int, @Body body: RequirementUpsertRequest): AckResponse

    @DELETE("requirements/{id}")
    suspend fun deleteRequirement(@Path("id") requirementId: Int): AckResponse

    // ---- Jenkins ----
    @GET("jenkins/binding/me")
    suspend fun jenkinsBinding(): JenkinsBindingResponse

    @PUT("jenkins/binding/me")
    suspend fun jenkinsSaveBinding(@Body body: JenkinsBindingRequest): JenkinsSaveResponse

    @POST("jenkins/binding/me/test")
    suspend fun jenkinsTestBinding(@Body body: JenkinsBindingRequest): JenkinsTestResponse

    @DELETE("jenkins/binding/me")
    suspend fun jenkinsDeleteBinding(): AckResponse

    @GET("jenkins/views")
    suspend fun jenkinsViews(): JenkinsViewsResponse

    @GET("jenkins/jobs")
    suspend fun jenkinsJobs(@Query("view") view: String? = null): JenkinsJobsResponse

    @GET("jenkins/jobs/{name}")
    suspend fun jenkinsJob(@Path("name") name: String): JenkinsJobDetailDto

    @POST("jenkins/jobs/{name}/build")
    suspend fun jenkinsTriggerBuild(@Path("name") name: String, @Body body: JenkinsBuildRequest): JenkinsBuildTriggerResponse

    @GET("jenkins/queue")
    suspend fun jenkinsQueue(@Query("url") url: String): JenkinsQueueResponse

    @GET("jenkins/build")
    suspend fun jenkinsBuild(@Query("job") job: String, @Query("number") number: Int): JenkinsBuildDetailDto
}
