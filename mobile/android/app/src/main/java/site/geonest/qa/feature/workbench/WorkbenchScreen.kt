package site.geonest.qa.feature.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import site.geonest.qa.core.data.WorkbenchContextState
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaRadius
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.DispatchedBug
import site.geonest.qa.core.domain.model.PreviewKind
import site.geonest.qa.core.domain.model.StoryStatus
import site.geonest.qa.core.domain.model.WbBug
import site.geonest.qa.core.domain.model.WorkbenchMode
import site.geonest.qa.core.domain.model.WorkbenchRequirement
import site.geonest.qa.feature.common.LabeledDropdown
import site.geonest.qa.feature.common.WorkbenchFilterBar
import site.geonest.qa.feature.preview.LocalPreviewOpen
import site.geonest.qa.feature.preview.zentaoNumericId

private val RESULT_OPTIONS = listOf(
    "passed" to "通过", "failed" to "失败", "blocked" to "阻塞", "partial" to "部分完成", "untested" to "未测试",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WorkbenchScreen(
    onMessage: (String) -> Unit,
    viewModel: WorkbenchViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val ctx by viewModel.ctx.collectAsStateWithLifecycle()

    var execFor by remember { mutableStateOf<WorkbenchRequirement?>(null) }
    var notesFor by remember { mutableStateOf<WorkbenchRequirement?>(null) }
    var clearFor by remember { mutableStateOf<WorkbenchRequirement?>(null) }

    LaunchedEffect(ui.toast) {
        ui.toast?.let { onMessage(it); viewModel.consumeToast() }
    }

    Column(Modifier.fillMaxSize()) {
        WorkbenchFilterBar(
            ctx = ctx,
            showMode = true,
            onSelectSoftware = viewModel::selectSoftware,
            onSelectMode = viewModel::setMode,
            onSelectMajor = viewModel::selectMajor,
        )

        if (ctx.ready && ui.error == null && !ui.loading) {
            val pending = ui.items.count { !it.fullyCompleted }
            Text(
                "剩余 $pending 个需求待完成 · 共 ${ui.items.size}",
                style = MaterialTheme.typography.labelMedium,
                color = QaColors.TextMuted,
                modifier = Modifier.padding(horizontal = QaSpacing.lg, vertical = QaSpacing.xxs),
            )
        }

        Box(Modifier.fillMaxWidth().weight(1f)) {
            when {
                ctx.loading || (ui.loading && ctx.ready) -> CircularProgressIndicator(Modifier.align(Alignment.Center))

                ctx.error != null -> ErrorRetry(ctx.error!!) { viewModel.retryContext() }

                !ctx.ready -> Text(
                    if (ctx.mode == WorkbenchMode.VERSION) "请选择软件与大版本" else "请选择软件",
                    color = QaColors.TextMuted,
                    modifier = Modifier.align(Alignment.Center),
                )

                ui.error != null -> ErrorRetry(ui.error!!) { viewModel.reload() }

                ui.isEmpty && ui.dispatched.isEmpty() -> Text(
                    "暂无负责的需求",
                    color = QaColors.TextMuted,
                    modifier = Modifier.align(Alignment.Center),
                )

                else -> PullToRefreshBox(
                    isRefreshing = ui.refreshing,
                    onRefresh = { viewModel.reload(refresh = true) },
                ) {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(QaSpacing.lg),
                        verticalArrangement = Arrangement.spacedBy(QaSpacing.md),
                    ) {
                        if (ui.dispatched.isNotEmpty()) {
                            item(key = "dispatched") { DispatchedSection(ui.dispatched) }
                        }
                        items(ui.items, key = { it.id }) { req ->
                            RequirementCard(
                                req = req,
                                status = ui.statuses[zentaoNumericId(req.zentaoReqId)],
                                showVersionTag = ctx.mode == WorkbenchMode.ALL_PENDING,
                                onCaseToggle = { viewModel.toggleCaseCompleted(req.id, it) },
                                onTestToggle = { checked -> if (checked) execFor = req else clearFor = req },
                                onNotes = { notesFor = req },
                            )
                        }
                    }
                }
            }
        }
    }

    execFor?.let { req ->
        TestExecutionDialog(
            req = req,
            ctx = ctx,
            onConfirm = { minorId, result, notes ->
                viewModel.submitTestExecution(req.id, minorId, result, notes)
                execFor = null
            },
            onDismiss = { execFor = null },
        )
    }
    notesFor?.let { req ->
        NotesDialog(
            req = req,
            onSave = { viewModel.saveTestNotes(req.id, it); notesFor = null },
            onDismiss = { notesFor = null },
        )
    }
    clearFor?.let { req ->
        AlertDialog(
            onDismissRequest = { clearFor = null },
            title = { Text("取消测试完成") },
            text = { Text("确认取消「${req.title}」的测试完成状态？") },
            confirmButton = {
                TextButton(onClick = { viewModel.clearTestCompleted(req.id); clearFor = null }) {
                    Text("确认取消", color = QaColors.Danger)
                }
            },
            dismissButton = { TextButton(onClick = { clearFor = null }) { Text("返回") } },
        )
    }
}

@Composable
private fun ErrorRetry(message: String, onRetry: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(QaSpacing.xl),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(message, color = QaColors.Danger)
        Spacer(Modifier.padding(QaSpacing.sm))
        FilterChip(selected = false, onClick = onRetry, label = { Text("重试") })
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DispatchedSection(bugs: List<DispatchedBug>) {
    Column(
        Modifier
            .fillMaxWidth()
            .border(1.dp, QaColors.Primary, RoundedCornerShape(QaRadius.lg))
            .background(QaColors.PrimaryContainer, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
    ) {
        Text("🪂 指派给我的 Bug（${bugs.size}）", style = MaterialTheme.typography.titleSmall, color = QaColors.PrimaryDark)
        Spacer(Modifier.padding(QaSpacing.xxs))
        bugs.forEach { b ->
            Row(
                Modifier.fillMaxWidth().padding(vertical = QaSpacing.xxs),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    b.bugId.ifBlank { "Bug#${b.id}" },
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (b.testDone) QaColors.TextDisabled else QaColors.TextStrong,
                    textDecoration = if (b.testDone) TextDecoration.LineThrough else null,
                )
                Spacer(Modifier.width(QaSpacing.sm))
                Text(b.reqTitle, style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted, modifier = Modifier.weight(1f))
                if (b.closed) Text("✅ 已闭环", style = MaterialTheme.typography.labelSmall, color = QaColors.Success)
            }
        }
        Text(
            "处理与闭环请到「测试工作台」或电脑端操作",
            style = MaterialTheme.typography.labelSmall,
            color = QaColors.TextMuted,
            modifier = Modifier.padding(top = QaSpacing.xxs),
        )
    }
}

@Composable
private fun RequirementCard(
    req: WorkbenchRequirement,
    status: StoryStatus?,
    showVersionTag: Boolean,
    onCaseToggle: (Boolean) -> Unit,
    onTestToggle: (Boolean) -> Unit,
    onNotes: () -> Unit,
) {
    val preview = LocalPreviewOpen.current
    val fullyDone = req.fullyCompleted
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .background(if (fullyDone) QaColors.Background else QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (showVersionTag && req.majorVersionName.isNotBlank()) {
                Badge(req.majorVersionName)
                Spacer(Modifier.width(QaSpacing.sm))
            }
            if (req.zentaoReqId.isNotBlank()) {
                Badge(req.zentaoReqId)
                Spacer(Modifier.width(QaSpacing.sm))
            }
            Text(
                text = req.title,
                style = MaterialTheme.typography.titleMedium,
                color = if (fullyDone) QaColors.TextDisabled else QaColors.TextStrong,
                textDecoration = if (fullyDone) TextDecoration.LineThrough else null,
                modifier = Modifier.weight(1f),
            )
            Text(
                "🔍预览",
                style = MaterialTheme.typography.labelSmall,
                color = QaColors.Primary,
                modifier = Modifier
                    .clickable { preview(PreviewKind.STORY, zentaoNumericId(req.zentaoReqId)) }
                    .padding(start = QaSpacing.sm, top = QaSpacing.xxs, bottom = QaSpacing.xxs),
            )
        }

        // 禅道实时状态（开发完成 / 激活等）
        if (status != null && (status.statusZh.isNotBlank() || status.stageZh.isNotBlank())) {
            Spacer(Modifier.padding(QaSpacing.xxs))
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (status.statusZh.isNotBlank()) {
                    StatusChip("状态：${status.statusZh}", QaColors.Primary, QaColors.PrimaryContainer)
                    Spacer(Modifier.width(QaSpacing.xs))
                }
                if (status.stageZh.isNotBlank()) {
                    val isDeveloped = status.stageZh.contains("开发完成") || status.stageZh.contains("已发布")
                    StatusChip(
                        "阶段：${status.stageZh}",
                        if (isDeveloped) QaColors.Success else QaColors.Warning,
                        if (isDeveloped) QaColors.SuccessContainer else QaColors.Background,
                    )
                }
            }
        }

        // 状态勾选
        Spacer(Modifier.padding(QaSpacing.xxs))
        Row(verticalAlignment = Alignment.CenterVertically) {
            CheckRow("用例完成", req.caseCompleted, enabled = true, onCaseToggle)
            Spacer(Modifier.width(QaSpacing.lg))
            CheckRow("测试完成", req.testCompleted, enabled = true, onTestToggle)
        }

        // 测试要点
        Spacer(Modifier.padding(QaSpacing.xxs))
        Row(verticalAlignment = Alignment.CenterVertically) {
            val noteColor = if (req.hasNotes) QaColors.Success else QaColors.TextMuted
            Text("测试要点：${if (req.hasNotes) "已填写" else "未填写"}", style = MaterialTheme.typography.labelMedium, color = noteColor)
            Spacer(Modifier.width(QaSpacing.sm))
            TextButton(onClick = onNotes) {
                Text(if (req.hasNotes) "查看/编辑" else "填写", style = MaterialTheme.typography.labelMedium)
            }
        }

        // 用例
        if (req.testCases.isNotEmpty()) {
            Spacer(Modifier.padding(QaSpacing.xxs))
            req.testCases.forEach { case ->
                Column(Modifier.fillMaxWidth().padding(vertical = QaSpacing.xxs)) {
                    Text(
                        "用例 ${case.zentaoCaseId} 🔍",
                        style = MaterialTheme.typography.bodyMedium,
                        color = QaColors.Primary,
                        modifier = Modifier.clickable { preview(PreviewKind.TESTCASE, zentaoNumericId(case.zentaoCaseId)) },
                    )
                    if (case.bugs.isNotEmpty()) BugChips(case.bugs)
                }
            }
        }

        // 自由 Bug
        if (req.freeBugs.isNotEmpty()) {
            Spacer(Modifier.padding(QaSpacing.xxs))
            Text("自由 Bug", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
            BugChips(req.freeBugs)
        }
    }
}

@Composable
private fun CheckRow(label: String, checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Checkbox(
            checked = checked,
            onCheckedChange = onChange,
            enabled = enabled,
            colors = CheckboxDefaults.colors(checkedColor = QaColors.Success),
        )
        Text(label, style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BugChips(bugs: List<WbBug>) {
    val preview = LocalPreviewOpen.current
    FlowRow(
        modifier = Modifier.fillMaxWidth().padding(top = QaSpacing.xxs),
        horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs),
        verticalArrangement = Arrangement.spacedBy(QaSpacing.xs),
    ) {
        bugs.forEach { bug ->
            val text = listOfNotNull(bug.bugId.takeIf { it.isNotBlank() }, bug.title.takeIf { it.isNotBlank() })
                .joinToString(" ")
                .ifBlank { "Bug#${bug.id}" }
            Box(
                Modifier
                    .border(1.dp, QaColors.SuccessBorder, RoundedCornerShape(QaRadius.sm))
                    .background(QaColors.SuccessContainer, RoundedCornerShape(QaRadius.sm))
                    .clickable { preview(PreviewKind.BUG, zentaoNumericId(bug.bugId)) }
                    .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
            ) {
                Text("$text 🔍", style = MaterialTheme.typography.labelSmall, color = QaColors.Success)
            }
        }
    }
}

@Composable
private fun StatusChip(text: String, color: androidx.compose.ui.graphics.Color, container: androidx.compose.ui.graphics.Color) {
    Box(
        Modifier
            .border(1.dp, color, RoundedCornerShape(QaRadius.sm))
            .background(container, RoundedCornerShape(QaRadius.sm))
            .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall, color = color)
    }
}

@Composable
private fun Badge(text: String) {
    Box(
        Modifier
            .background(QaColors.PrimaryContainer, RoundedCornerShape(QaRadius.sm))
            .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.SemiBold),
            color = QaColors.OnPrimaryContainer,
        )
    }
}

@Composable
private fun TestExecutionDialog(
    req: WorkbenchRequirement,
    ctx: WorkbenchContextState,
    onConfirm: (minorId: Int, resultStatus: String, notes: String) -> Unit,
    onDismiss: () -> Unit,
) {
    val minors = ctx.minorsOfMajor(req.majorVersionId)
    var minorId by remember(req.id) {
        mutableStateOf(ctx.minorId?.takeIf { id -> minors.any { it.id == id } } ?: minors.firstOrNull()?.id)
    }
    var resultIdx by remember(req.id) { mutableStateOf(0) }
    var notes by remember(req.id) { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("提交测试完成") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                Text("「${req.title}」", style = MaterialTheme.typography.bodyMedium, color = QaColors.TextMuted)
                if (minors.isEmpty()) {
                    Text("该需求所属大版本下暂无小版本，请先在电脑端补充小版本。", color = QaColors.Danger, style = MaterialTheme.typography.bodyMedium)
                } else {
                    LabeledDropdown(
                        label = "复测发包（小版本）",
                        selectedText = minors.firstOrNull { it.id == minorId }?.versionNo ?: "请选择",
                        options = minors.map { it.id to it.versionNo },
                        onSelect = { minorId = it },
                    )
                    LabeledDropdown(
                        label = "测试结果",
                        selectedText = RESULT_OPTIONS[resultIdx].second,
                        options = RESULT_OPTIONS.mapIndexed { i, (_, label) -> i to label },
                        onSelect = { resultIdx = it },
                    )
                    OutlinedTextField(
                        value = notes,
                        onValueChange = { notes = it },
                        label = { Text("测试说明（可选）") },
                        modifier = Modifier.fillMaxWidth(),
                        minLines = 2,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = minorId != null,
                onClick = { minorId?.let { onConfirm(it, RESULT_OPTIONS[resultIdx].first, notes) } },
            ) { Text("提交并标记完成", color = QaColors.Success) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun NotesDialog(
    req: WorkbenchRequirement,
    onSave: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var text by remember(req.id) { mutableStateOf(req.testNotes.orEmpty()) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("测试要点") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(QaSpacing.xs)) {
                Text("${req.zentaoReqId} ${req.title}", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
                if (req.testNotesUpdatedByName != null) {
                    Text("最后更新：${req.testNotesUpdatedByName}", style = MaterialTheme.typography.labelSmall, color = QaColors.TextDisabled)
                }
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it },
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 4,
                )
            }
        },
        confirmButton = { TextButton(onClick = { onSave(text) }) { Text("保存") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}
