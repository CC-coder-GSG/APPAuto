package site.geonest.qa.core.network

import retrofit2.http.Body
import retrofit2.http.Field
import retrofit2.http.FormUrlEncoded
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query
import kotlinx.serialization.json.JsonObject
import site.geonest.qa.core.network.dto.AckResponse
import site.geonest.qa.core.network.dto.DispatchedBugDto
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
}
