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
import site.geonest.qa.core.network.dto.MeResponse
import site.geonest.qa.core.network.dto.PatchStatusRequest
import site.geonest.qa.core.network.dto.PatchStatusResponse
import site.geonest.qa.core.network.dto.RetestRequirementDto
import site.geonest.qa.core.network.dto.RetestResultResponse
import site.geonest.qa.core.network.dto.RetestSubmitRequest
import site.geonest.qa.core.network.dto.TokenResponse
import site.geonest.qa.core.network.dto.WorkbenchRequirementDto

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

    /** 我的工作台。不传参 = 我负责的全部需求；mode=all_pending 仅未完成。 */
    @GET("requirements/my-workbench")
    suspend fun myWorkbench(
        @Query("mode") mode: String? = null,
        @Query("major_version_id") majorVersionId: Int? = null,
        @Query("software_id") softwareId: Int? = null,
    ): List<WorkbenchRequirementDto>

    /** 勾选"用例完成 / 测试完成"。 */
    @PATCH("requirements/{id}/status")
    suspend fun patchRequirementStatus(
        @Path("id") requirementId: Int,
        @Body body: PatchStatusRequest,
    ): PatchStatusResponse

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
}
