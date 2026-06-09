package site.geonest.qa.feature.overalltest

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
import site.geonest.qa.core.domain.model.OverallBug
import site.geonest.qa.core.domain.model.OverallStats
import site.geonest.qa.core.domain.model.PreviewKind
import site.geonest.qa.feature.common.LabeledDropdown
import site.geonest.qa.feature.common.WorkbenchFilterBar
import site.geonest.qa.feature.preview.LocalPreviewOpen
import site.geonest.qa.feature.preview.zentaoNumericId

private val RESOLUTION_OPTIONS = listOf(
    "fixed" to "修复通过", "false_alarm" to "误报", "rejected" to "拒绝修复",
)

private fun statusLabel(code: String): String = when (code) {
    "active" -> "激活"
    "resolved" -> "已解决"
    "closed" -> "已关闭"
    "local" -> "本地"
    else -> code
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun OverallTestScreen(
    onMessage: (String) -> Unit,
    viewModel: OverallTestViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val ctx by viewModel.ctx.collectAsStateWithLifecycle()
    var resultFor by remember { mutableStateOf<OverallBug?>(null) }

    LaunchedEffect(ui.toast) { ui.toast?.let { onMessage(it); viewModel.consumeToast() } }

    Column(Modifier.fillMaxSize()) {
        WorkbenchFilterBar(
            ctx = ctx,
            showMode = false,
            onSelectSoftware = viewModel::selectSoftware,
            onSelectMode = {},
            onSelectMajor = viewModel::selectMajor,
        )

        // 关键词 + 状态筛选
        Row(
            Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = ui.keyword,
                onValueChange = viewModel::setKeyword,
                singleLine = true,
                label = { Text("搜索 Bug 编号") },
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(QaSpacing.sm))
            FilterChip(selected = false, onClick = { viewModel.search() }, label = { Text("搜索") })
        }
        FlowRow(
            Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg, vertical = QaSpacing.xs),
            horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs),
        ) {
            OVERALL_STATUS_OPTIONS.forEach { (code, label) ->
                FilterChip(
                    selected = code in ui.statuses,
                    onClick = { viewModel.toggleStatus(code) },
                    label = { Text(label) },
                )
            }
        }

        Box(Modifier.fillMaxWidth().weight(1f)) {
            when {
                ctx.loading || (ui.loading && ctx.softwareId != null && ctx.majorId != null) ->
                    CircularProgressIndicator(Modifier.align(Alignment.Center))

                ctx.error != null -> ErrorRetry(ctx.error!!) { viewModel.retryContext() }

                ctx.softwareId == null || ctx.majorId == null -> Text(
                    "请选择软件与大版本",
                    color = QaColors.TextMuted,
                    modifier = Modifier.align(Alignment.Center),
                )

                ui.error != null -> ErrorRetry(ui.error!!) { viewModel.reload() }

                ui.isEmpty -> Text("该版本暂无 Bug", color = QaColors.TextMuted, modifier = Modifier.align(Alignment.Center))

                else -> PullToRefreshBox(
                    isRefreshing = ui.refreshing,
                    onRefresh = { viewModel.reload(refresh = true) },
                ) {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(QaSpacing.lg),
                        verticalArrangement = Arrangement.spacedBy(QaSpacing.sm),
                    ) {
                        item(key = "stats") { StatsHeader(ui.stats) }
                        items(ui.bugs, key = { it.id }) { bug ->
                            BugCard(bug = bug, onClick = { resultFor = bug })
                        }
                    }
                }
            }
        }
    }

    resultFor?.let { bug ->
        ResultDialog(
            bug = bug,
            ctx = ctx,
            onConfirm = { minorId, done, resolution, comment ->
                viewModel.submitResult(bug.id, minorId, done, resolution, comment, bug.zentaoBugId)
                resultFor = null
            },
            onDismiss = { resultFor = null },
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

@Composable
private fun StatsHeader(stats: OverallStats) {
    Row(
        Modifier
            .fillMaxWidth()
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        StatCell("就绪率", "${stats.readyRate}%", QaColors.Primary)
        StatCell("已闭环", "${stats.closed}", QaColors.Success)
        StatCell("待处理", "${stats.pending}", QaColors.Warning)
        StatCell("总计", "${stats.total}", QaColors.TextStrong)
    }
}

@Composable
private fun StatCell(label: String, value: String, color: androidx.compose.ui.graphics.Color) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value, style = MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.Bold), color = color)
        Text(label, style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BugCard(bug: OverallBug, onClick: () -> Unit) {
    val preview = LocalPreviewOpen.current
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, if (bug.isRetestFailed) QaColors.Danger else QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .background(if (bug.closed) QaColors.Background else QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .clickable(onClick = onClick)
            .padding(QaSpacing.lg),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                bug.bugId.ifBlank { "Bug#${bug.id}" },
                style = MaterialTheme.typography.titleSmall,
                color = if (bug.closed) QaColors.TextDisabled else QaColors.TextStrong,
                textDecoration = if (bug.closed) TextDecoration.LineThrough else null,
            )
            Spacer(Modifier.width(QaSpacing.sm))
            StatusPill(bug)
            Spacer(Modifier.weight(1f))
            Text(
                "🔍预览",
                style = MaterialTheme.typography.labelSmall,
                color = QaColors.Primary,
                modifier = Modifier.clickable { preview(PreviewKind.BUG, zentaoNumericId(bug.zentaoBugId ?: bug.bugId)) },
            )
        }
        if (bug.zentaoBugTitle.isNotBlank()) {
            Text(
                bug.zentaoBugTitle,
                style = MaterialTheme.typography.bodyMedium,
                color = QaColors.TextDefault,
                modifier = Modifier.padding(top = QaSpacing.xxs),
            )
        }
        FlowRow(
            Modifier.fillMaxWidth().padding(top = QaSpacing.xxs),
            horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm),
        ) {
            if (bug.majorVersionNo.isNotBlank()) Meta("🏷️ ${bug.majorVersionNo}")
            if (bug.zentaoAssignedToName.isNotBlank()) Meta("指派禅道：${bug.zentaoAssignedToName}")
            if (!bug.dispatchedToName.isNullOrBlank()) Meta("🪂 ${bug.dispatchedToName}")
            if (bug.isRetestFailed) Meta("⚠ 未修好", QaColors.Danger)
        }
    }
}

@Composable
private fun StatusPill(bug: OverallBug) {
    val (text, color, container) = when {
        bug.closed -> Triple("已闭环", QaColors.Success, QaColors.SuccessContainer)
        bug.effectiveStatus == "resolved" -> Triple("已解决", QaColors.Primary, QaColors.PrimaryContainer)
        bug.effectiveStatus == "local" -> Triple("本地", QaColors.TextMuted, QaColors.Background)
        else -> Triple(statusLabel(bug.effectiveStatus.ifBlank { "active" }), QaColors.Warning, QaColors.Background)
    }
    Box(
        Modifier
            .background(container, RoundedCornerShape(QaRadius.sm))
            .border(1.dp, color, RoundedCornerShape(QaRadius.sm))
            .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall, color = color)
    }
}

@Composable
private fun Meta(text: String, color: androidx.compose.ui.graphics.Color = QaColors.TextMuted) {
    Text(text, style = MaterialTheme.typography.labelSmall, color = color)
}

@Composable
private fun ResultDialog(
    bug: OverallBug,
    ctx: WorkbenchContextState,
    onConfirm: (minorId: Int, testDone: Boolean, resolution: String, comment: String) -> Unit,
    onDismiss: () -> Unit,
) {
    val minors = ctx.minors
    var minorId by remember(bug.id) {
        mutableStateOf(ctx.minorId?.takeIf { id -> minors.any { it.id == id } } ?: minors.firstOrNull()?.id)
    }
    var resolutionIdx by remember(bug.id) {
        mutableStateOf(RESOLUTION_OPTIONS.indexOfFirst { it.first == bug.myResolution }.coerceAtLeast(0))
    }
    var done by remember(bug.id) { mutableStateOf(bug.closed) }
    var comment by remember(bug.id) { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("修复结果 / 闭环 - ${bug.bugId.ifBlank { "#${bug.id}" }}") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                if (minors.isEmpty()) {
                    Text("当前大版本下暂无小版本，请先在电脑端补充。", color = QaColors.Danger, style = MaterialTheme.typography.bodyMedium)
                } else {
                    LabeledDropdown(
                        label = "复测发包（小版本）",
                        selectedText = minors.firstOrNull { it.id == minorId }?.versionNo ?: "请选择",
                        options = minors.map { it.id to it.versionNo },
                        onSelect = { minorId = it },
                    )
                    LabeledDropdown(
                        label = "修复结果",
                        selectedText = RESOLUTION_OPTIONS[resolutionIdx].second,
                        options = RESOLUTION_OPTIONS.mapIndexed { i, (_, label) -> i to label },
                        onSelect = { resolutionIdx = it },
                    )
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = done, onCheckedChange = { done = it })
                        Text("确认闭环", style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault)
                    }
                    if (done && bug.isZentao) {
                        OutlinedTextField(
                            value = comment,
                            onValueChange = { comment = it },
                            label = { Text("闭环说明（同步写入禅道）") },
                            modifier = Modifier.fillMaxWidth(),
                            minLines = 2,
                        )
                        Text(
                            "勾选闭环保存时会同步关闭禅道 Bug（需禅道中已是「已解决」）。",
                            style = MaterialTheme.typography.labelSmall,
                            color = QaColors.TextMuted,
                        )
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = minorId != null,
                onClick = { minorId?.let { onConfirm(it, done, RESOLUTION_OPTIONS[resolutionIdx].first, comment) } },
            ) { Text("保存记录") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}
