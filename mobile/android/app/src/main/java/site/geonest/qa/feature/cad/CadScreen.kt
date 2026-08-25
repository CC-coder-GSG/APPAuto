package site.geonest.qa.feature.cad

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.AttachFile
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.compose.AsyncImage
import kotlinx.coroutines.launch
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaLinkLabel
import site.geonest.qa.core.designsystem.QaRadius
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.CadAttachment
import site.geonest.qa.core.domain.model.CadColumn
import site.geonest.qa.core.domain.model.CadItem
import site.geonest.qa.core.domain.model.CadRecord
import site.geonest.qa.core.domain.model.PreviewKind
import site.geonest.qa.feature.common.LabeledDropdown
import site.geonest.qa.feature.preview.LocalPreviewOpen

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CadScreen(
    onBack: () -> Unit,
    viewModel: CadViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    var editItem by remember { mutableStateOf<CadItem?>(null) }
    var newItem by remember { mutableStateOf(false) }
    var showNewBoard by remember { mutableStateOf(false) }
    var showNewVersion by remember { mutableStateOf(false) }
    var fullImage by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(ui.toast) {
        ui.toast?.let {
            snackbar.currentSnackbarData?.dismiss()
            scope.launch { snackbar.showSnackbar(it) }
            viewModel.consumeToast()
        }
    }

    Scaffold(
        containerColor = QaColors.Background,
        topBar = {
            TopAppBar(
                title = { Text("CAD 测试统计") },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回") }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = QaColors.Background,
                    titleContentColor = QaColors.TextStrong,
                    navigationIconContentColor = QaColors.TextStrong,
                ),
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            // 统计表 + 版本 选择
            Row(
                Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg, vertical = QaSpacing.sm),
                horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm),
            ) {
                LabeledDropdown(
                    label = "统计表",
                    selectedText = ui.boards.firstOrNull { it.id == ui.boardId }?.name ?: "无",
                    options = ui.boards.map { it.id to it.name },
                    onSelect = viewModel::selectBoard,
                    modifier = Modifier.weight(1f),
                )
                val versions = ui.board?.versions ?: emptyList()
                LabeledDropdown(
                    label = "版本",
                    selectedText = versions.firstOrNull { it.id == ui.versionId }?.name ?: "无",
                    options = versions.map { it.id to it.name },
                    onSelect = viewModel::selectVersion,
                    enabled = versions.isNotEmpty(),
                    modifier = Modifier.weight(1f),
                )
            }
            Row(
                Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg),
                horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm),
            ) {
                TextButton(onClick = { showNewBoard = true }) { Text("+ 新建统计表") }
                TextButton(onClick = { showNewVersion = true }, enabled = ui.boardId != null) { Text("+ 新增版本") }
            }
            Row(
                Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("${ui.board?.items?.size ?: 0} 个条目", style = MaterialTheme.typography.labelMedium, color = QaColors.TextMuted)
                Spacer(Modifier.weight(1f))
                if (ui.busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                else Button(onClick = { newItem = true }, enabled = ui.boardId != null) { Text("+ 新增条目") }
            }

            Box(Modifier.fillMaxWidth().weight(1f)) {
                val board = ui.board
                val versionId = ui.versionId
                when {
                    ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                    ui.error != null -> Text(ui.error!!, color = QaColors.Danger, modifier = Modifier.align(Alignment.Center).padding(QaSpacing.xl))
                    board == null || ui.boards.isEmpty() -> Text("暂无统计表", color = QaColors.TextMuted, modifier = Modifier.align(Alignment.Center))
                    versionId == null -> Text("该统计表暂无版本（请在电脑端新增）", color = QaColors.TextMuted, modifier = Modifier.align(Alignment.Center).padding(QaSpacing.xl))
                    board.items.isEmpty() -> Text("该统计表暂无条目，点「+ 新增条目」", color = QaColors.TextMuted, modifier = Modifier.align(Alignment.Center))
                    else -> LazyColumn(
                        Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(QaSpacing.lg),
                        verticalArrangement = Arrangement.spacedBy(QaSpacing.md),
                    ) {
                        items(board.items, key = { it.id }) { item ->
                            CadItemCard(
                                item = item,
                                record = board.record(item.id, versionId),
                                columns = board.columns,
                                viewModel = viewModel,
                                onEdit = { editItem = item },
                                onImage = { fullImage = it },
                            )
                        }
                    }
                }
            }
        }
    }

    // 记录编辑（实时读取最新 record，便于上传/删除截图后刷新）
    val editVersionId = ui.versionId
    editItem?.let { item ->
        if (editVersionId != null) {
            RecordEditDialog(
                item = item,
                versionId = editVersionId,
                record = ui.board?.record(item.id, editVersionId),
                columns = ui.board?.columns ?: emptyList(),
                viewModel = viewModel,
                onImage = { fullImage = it },
                onDismiss = { editItem = null },
            )
        }
    }

    if (newItem) {
        NewItemDialog(onConfirm = { seq, title, bug -> viewModel.createItem(seq, title, bug); newItem = false }, onDismiss = { newItem = false })
    }

    if (showNewBoard) {
        NameDialog(
            title = "新建统计表",
            label = "统计表名称（如：40311版本测试统计表）",
            onConfirm = { viewModel.createBoard(it); showNewBoard = false },
            onDismiss = { showNewBoard = false },
        )
    }
    if (showNewVersion) {
        NameDialog(
            title = "新增版本",
            label = "版本名称（如：4.0.3.11.260603 版本测试）",
            onConfirm = { viewModel.createVersion(it); showNewVersion = false },
            onDismiss = { showNewVersion = false },
        )
    }

    fullImage?.let { url ->
        Dialog(onDismissRequest = { fullImage = null }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
            Box(Modifier.fillMaxSize().background(androidx.compose.ui.graphics.Color(0xCC000000)).clickable { fullImage = null }, contentAlignment = Alignment.Center) {
                AsyncImage(model = url, imageLoader = viewModel.imageLoader, contentDescription = null, modifier = Modifier.fillMaxWidth())
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CadItemCard(
    item: CadItem,
    record: CadRecord?,
    columns: List<CadColumn>,
    viewModel: CadViewModel,
    onEdit: () -> Unit,
    onImage: (String) -> Unit,
) {
    val preview = LocalPreviewOpen.current
    val abnormal = record?.isAbnormal == true
    val cardColor = if (abnormal) androidx.compose.ui.graphics.Color(0xFFFEF2F2) else QaColors.Card
    val borderColor = if (abnormal) androidx.compose.ui.graphics.Color(0xFFFCA5A5) else QaColors.Border
    Column(
        Modifier.fillMaxWidth()
            .border(1.dp, borderColor, RoundedCornerShape(QaRadius.lg))
            .background(cardColor, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("#${item.seq ?: "-"}", style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold), color = QaColors.TextStrong)
            Spacer(Modifier.width(QaSpacing.sm))
            Text(item.title, style = MaterialTheme.typography.bodyLarge, color = QaColors.TextDefault, modifier = Modifier.weight(1f))
            if (item.zentaoBugId != null) {
                QaLinkLabel(
                    text = "Bug ${item.zentaoBugId}",
                    onClick = { preview(PreviewKind.BUG, item.zentaoBugId) },
                    style = MaterialTheme.typography.labelSmall,
                )
            }
        }
        Spacer(Modifier.padding(QaSpacing.xxs))
        StatusBadge(record)

        if (item.cadFiles.isNotEmpty()) {
            Spacer(Modifier.padding(QaSpacing.xxs))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.AttachFile, contentDescription = null, tint = QaColors.Info, modifier = Modifier.size(14.dp))
                Spacer(Modifier.width(QaSpacing.xxs))
                Text("共享 CAD：" + item.cadFiles.joinToString("、") { it.originalName }, style = MaterialTheme.typography.labelSmall, color = QaColors.Info)
            }
        }

        if (record != null && record.description.isNotBlank()) {
            Spacer(Modifier.padding(QaSpacing.xxs))
            Box(
                Modifier.fillMaxWidth()
                    .background(QaColors.Background, RoundedCornerShape(QaRadius.sm))
                    .padding(QaSpacing.sm),
            ) { Text(record.description, style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault) }
        }

        // 自定义列
        if (record != null && columns.isNotEmpty()) {
            columns.forEach { c ->
                val v = record.customValues[c.id.toString()]
                if (!v.isNullOrBlank()) Text("${c.name}：$v", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
            }
        }

        // 截图缩略图 + 视频
        if (record != null && (record.screenshots.isNotEmpty() || record.videos.isNotEmpty())) {
            Spacer(Modifier.padding(QaSpacing.xxs))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs), verticalArrangement = Arrangement.spacedBy(QaSpacing.xs)) {
                record.screenshots.forEach { att ->
                    val url = viewModel.absUrl(att.downloadUrl)
                    AsyncImage(
                        model = url,
                        imageLoader = viewModel.imageLoader,
                        contentDescription = null,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.size(60.dp).clip(RoundedCornerShape(QaRadius.sm)).clickable { if (url != null) onImage(url) },
                    )
                }
                record.videos.forEach { att -> VideoChip(att, viewModel) }
            }
        }

        Spacer(Modifier.padding(QaSpacing.xxs))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Button(onClick = onEdit) { Text("填写/编辑") }
        }
    }
}

@Composable
private fun StatusBadge(record: CadRecord?) {
    data class Badge(val text: String, val color: androidx.compose.ui.graphics.Color, val bg: androidx.compose.ui.graphics.Color, val icon: androidx.compose.ui.graphics.vector.ImageVector?)
    val b = when {
        record?.isAbnormal == true -> Badge("异常", QaColors.Danger, androidx.compose.ui.graphics.Color(0xFFFEE2E2), Icons.Outlined.WarningAmber)
        record?.isNormal == true -> Badge("正常", QaColors.Success, QaColors.SuccessContainer, Icons.Outlined.CheckCircle)
        else -> Badge("未测", QaColors.TextMuted, QaColors.Background, null)
    }
    Row(
        Modifier.background(b.bg, RoundedCornerShape(QaRadius.sm)).border(1.dp, b.color, RoundedCornerShape(QaRadius.sm)).padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (b.icon != null) {
            Icon(b.icon, contentDescription = null, tint = b.color, modifier = Modifier.size(13.dp))
            Spacer(Modifier.width(QaSpacing.xxs))
        }
        Text(b.text, style = MaterialTheme.typography.labelSmall, color = b.color)
    }
}

@Composable
private fun VideoChip(att: CadAttachment, viewModel: CadViewModel) {
    val context = LocalContext.current
    Box(
        Modifier.background(QaColors.Background, RoundedCornerShape(QaRadius.sm))
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.sm))
            .clickable {
                val url = viewModel.streamUrlWithToken(att.streamUrl) ?: return@clickable
                runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).setDataAndType(Uri.parse(url), "video/*")) }
            }
            .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xs),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.PlayArrow, contentDescription = null, tint = QaColors.Accent, modifier = Modifier.size(15.dp))
            Spacer(Modifier.width(QaSpacing.xxs))
            Text("视频", style = MaterialTheme.typography.labelSmall, color = QaColors.Accent)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RecordEditDialog(
    item: CadItem,
    versionId: Int,
    record: CadRecord?,
    columns: List<CadColumn>,
    viewModel: CadViewModel,
    onImage: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var normal by remember(item.id) { mutableStateOf(record?.isNormal == true) }
    var abnormal by remember(item.id) { mutableStateOf(record?.isAbnormal == true) }
    var desc by remember(item.id) { mutableStateOf(record?.description.orEmpty()) }
    val custom = remember(item.id) {
        androidx.compose.runtime.mutableStateMapOf<Int, String>().apply {
            columns.forEach { put(it.id, record?.customValues?.get(it.id.toString()).orEmpty()) }
        }
    }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri?.let { viewModel.uploadScreenshot(item.id, versionId, it) }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("条目 #${item.seq ?: "-"} 记录") },
        text = {
            Column(
                Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(QaSpacing.sm),
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(QaSpacing.lg)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = normal, onCheckedChange = { normal = it; if (it) abnormal = false })
                        Text("正常", color = QaColors.Success)
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = abnormal, onCheckedChange = { abnormal = it; if (it) normal = false })
                        Text("异常", color = QaColors.Danger)
                    }
                }
                OutlinedTextField(value = desc, onValueChange = { desc = it }, label = { Text("问题说明 / 异常说明") }, modifier = Modifier.fillMaxWidth(), minLines = 2)
                columns.forEach { c ->
                    OutlinedTextField(
                        value = custom[c.id].orEmpty(),
                        onValueChange = { custom[c.id] = it },
                        label = { Text(c.name) },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                    )
                }
                // 截图
                Text("截图", style = MaterialTheme.typography.labelMedium, color = QaColors.TextMuted)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs), verticalArrangement = Arrangement.spacedBy(QaSpacing.xs)) {
                    record?.screenshots?.forEach { att ->
                        val url = viewModel.absUrl(att.downloadUrl)
                        Box {
                            AsyncImage(
                                model = url,
                                imageLoader = viewModel.imageLoader,
                                contentDescription = null,
                                contentScale = ContentScale.Crop,
                                modifier = Modifier.size(64.dp).clip(RoundedCornerShape(QaRadius.sm)).clickable { if (url != null) onImage(url) },
                            )
                            Icon(
                                Icons.Outlined.Close,
                                contentDescription = "删除",
                                tint = androidx.compose.ui.graphics.Color.White,
                                modifier = Modifier.align(Alignment.TopEnd).size(18.dp).background(QaColors.Danger, RoundedCornerShape(QaRadius.sm)).clickable { viewModel.deleteAttachment(att.id) }.padding(2.dp),
                            )
                        }
                    }
                    OutlinedButton(onClick = { picker.launch("image/*") }) { Text("+ 添加截图") }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { viewModel.saveRecord(item.id, versionId, normal, abnormal, desc, custom.toMap().mapKeys { it.key.toString() }) }) {
                Text("保存")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("关闭") } },
    )
}

@Composable
private fun NewItemDialog(onConfirm: (seq: Int?, title: String?, bug: Int?) -> Unit, onDismiss: () -> Unit) {
    var seq by remember { mutableStateOf("") }
    var title by remember { mutableStateOf("") }
    var bug by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("新增条目") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                OutlinedTextField(value = seq, onValueChange = { seq = it.filter(Char::isDigit) }, label = { Text("序号（留空自动顺延）") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = title, onValueChange = { title = it }, label = { Text("标题 / 图纸主名（可选）") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = bug, onValueChange = { bug = it.filter(Char::isDigit) }, label = { Text("关联禅道 Bug ID（可选）") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = { TextButton(onClick = { onConfirm(seq.toIntOrNull(), title.ifBlank { null }, bug.toIntOrNull()) }) { Text("创建") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun NameDialog(title: String, label: String, onConfirm: (String) -> Unit, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                label = { Text(label) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = { TextButton(onClick = { onConfirm(name.trim()) }, enabled = name.isNotBlank()) { Text("创建") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}
