package site.geonest.qa.core.domain.model

/** CAD 测试统计领域模型。 */
data class CadBoard(val id: Int, val name: String, val versionCount: Int, val itemCount: Int)

data class CadBoardDetail(
    val id: Int,
    val name: String,
    val versions: List<CadVersion>,
    val columns: List<CadColumn>,
    val items: List<CadItem>,
    /** 键 "itemId:versionId"。 */
    val records: Map<String, CadRecord>,
) {
    fun record(itemId: Int, versionId: Int): CadRecord? = records["$itemId:$versionId"]
}

data class CadVersion(val id: Int, val name: String)
data class CadColumn(val id: Int, val name: String)

data class CadItem(
    val id: Int,
    val seq: Int?,
    val title: String,
    val zentaoBugId: Int?,
    val bugUrl: String?,
    val cadFiles: List<CadFile>,
)

data class CadFile(val id: Int, val originalName: String)

data class CadRecord(
    val normalCount: Int,
    val abnormalCount: Int,
    val description: String,
    val customValues: Map<String, String>,
    val attachments: List<CadAttachment>,
) {
    val isNormal: Boolean get() = normalCount > 0
    val isAbnormal: Boolean get() = abnormalCount > 0
    val screenshots: List<CadAttachment> get() = attachments.filter { it.kind == "screenshot" && !it.isVideo }
    val videos: List<CadAttachment> get() = attachments.filter { it.isVideo || it.kind == "video" }
}

data class CadAttachment(
    val id: Int,
    val kind: String,
    val originalName: String,
    val isImage: Boolean,
    val isVideo: Boolean,
    val downloadUrl: String?,
    val streamUrl: String?,
)
