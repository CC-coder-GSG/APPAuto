package site.geonest.qa.feature.assign

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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaLinkLabel
import site.geonest.qa.core.designsystem.QaRadius
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.AssignRequirement
import site.geonest.qa.core.domain.model.LinkOption
import site.geonest.qa.core.domain.model.OwnerProgress
import site.geonest.qa.core.domain.model.PreviewKind
import site.geonest.qa.core.domain.model.ProgressSummary
import site.geonest.qa.feature.common.LabeledDropdown
import site.geonest.qa.feature.common.WorkbenchFilterBar
import site.geonest.qa.feature.preview.LocalPreviewOpen
import site.geonest.qa.feature.preview.zentaoNumericId

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AssignScreen(
    onMessage: (String) -> Unit,
    viewModel: AssignViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val ctx by viewModel.ctx.collectAsStateWithLifecycle()

    LaunchedEffect(ui.toast) { ui.toast?.let { onMessage(it); viewModel.consumeToast() } }

    Column(Modifier.fillMaxSize()) {
        WorkbenchFilterBar(
            ctx = ctx,
            showMode = false,
            onSelectSoftware = viewModel::selectSoftware,
            onSelectMode = {},
            onSelectMajor = viewModel::selectMajor,
        )
        // 子页签
        FlowRow(
            Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg, vertical = QaSpacing.xs),
            horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm),
        ) {
            AssignSubtab.entries.forEach { t ->
                FilterChip(selected = ui.subtab == t, onClick = { viewModel.setSubtab(t) }, label = { Text(t.label) })
            }
        }

        Box(Modifier.fillMaxWidth().weight(1f)) {
            when {
                ctx.error != null -> CenterText(ctx.error!!, QaColors.Danger)
                ctx.softwareId == null -> CenterText("请选择软件", QaColors.TextMuted)
                else -> when (ui.subtab) {
                    AssignSubtab.ASSIGN -> AssignSection(ui, viewModel)
                    AssignSubtab.PROGRESS -> ProgressSection(ui, viewModel)
                    AssignSubtab.LINK -> LinkSection(ui, ctx.majors.filter { it.id != ctx.majorId }.map { it.id to it.versionNo }, ctx.majorId != null, viewModel)
                }
            }
        }
    }
}

@Composable
private fun CenterText(text: String, color: androidx.compose.ui.graphics.Color) {
    Box(Modifier.fillMaxSize()) {
        Text(text, color = color, modifier = Modifier.align(Alignment.Center).padding(QaSpacing.xl))
    }
}

// ─────────────────────────── 分配 ───────────────────────────
@Composable
private fun AssignSection(ui: AssignUiState, vm: AssignViewModel) {
    val ownerOptions = listOf(0 to "未分配") + ui.users.map { it.id to it.name }
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg, vertical = QaSpacing.xs),
            horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("共 ${ui.reqs.size} 个需求", style = MaterialTheme.typography.labelMedium, color = QaColors.TextMuted)
            Spacer(Modifier.weight(1f))
            if (ui.busy) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
            } else {
                OutlinedButton(onClick = vm::syncZentao) { Text("同步禅道") }
                Button(onClick = vm::publish) { Text("一键发布") }
            }
        }
        Box(Modifier.fillMaxWidth().weight(1f)) {
            when {
                ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                ui.error != null -> CenterText(ui.error, QaColors.Danger)
                ui.reqs.isEmpty() -> CenterText("该版本暂无需求", QaColors.TextMuted)
                else -> LazyColumn(
                    Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(QaSpacing.lg),
                    verticalArrangement = Arrangement.spacedBy(QaSpacing.sm),
                ) {
                    items(ui.reqs, key = { it.id }) { req ->
                        AssignReqCard(req, ui.owners[req.id] ?: 0, ownerOptions) { vm.setOwner(req.id, it) }
                    }
                }
            }
        }
    }
}

@Composable
private fun AssignReqCard(req: AssignRequirement, ownerId: Int, ownerOptions: List<Pair<Int, String>>, onOwner: (Int) -> Unit) {
    val preview = LocalPreviewOpen.current
    Column(
        Modifier.fillMaxWidth()
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (req.zentaoReqId.isNotBlank()) { Badge(req.zentaoReqId); Spacer(Modifier.width(QaSpacing.sm)) }
            Text(req.title, style = MaterialTheme.typography.bodyLarge, color = QaColors.TextStrong, modifier = Modifier.weight(1f))
            QaLinkLabel(
                text = "预览",
                onClick = { preview(PreviewKind.STORY, zentaoNumericId(req.zentaoReqId)) },
            )
        }
        Spacer(Modifier.padding(QaSpacing.xxs))
        LabeledDropdown(
            label = "负责人",
            selectedText = ownerOptions.firstOrNull { it.first == ownerId }?.second ?: "未分配",
            options = ownerOptions,
            onSelect = onOwner,
        )
    }
}

// ─────────────────────────── 进行状态 ───────────────────────────
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ProgressSection(ui: AssignUiState, vm: AssignViewModel) {
    val p = ui.progress
    if (p == null) {
        Box(Modifier.fillMaxSize()) { CircularProgressIndicator(Modifier.align(Alignment.Center)) }
        return
    }
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(QaSpacing.lg),
        verticalArrangement = Arrangement.spacedBy(QaSpacing.sm),
    ) {
        item(key = "summary") { SummaryChips(p.summary, ui.progressPendingOnly, vm::toggleProgressPendingOnly) }
        if (p.retestPending.isNotEmpty()) {
            item(key = "retest") {
                Column(cardMod()) {
                    Text("复测待办（按大版本）", style = MaterialTheme.typography.titleSmall, color = QaColors.TextStrong)
                    p.retestPending.forEach {
                        Row(Modifier.fillMaxWidth().padding(top = QaSpacing.xxs)) {
                            Text(it.majorVersionName, style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault, modifier = Modifier.weight(1f))
                            Text("${it.count}", style = MaterialTheme.typography.bodyMedium, color = QaColors.Danger, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }
        items(p.owners, key = { it.ownerId }) { owner -> OwnerCard(owner, ui.progressPendingOnly) }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SummaryChips(s: ProgressSummary, pendingOnly: Boolean, onToggle: () -> Unit) {
    Column(cardMod()) {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs), verticalArrangement = Arrangement.spacedBy(QaSpacing.xs)) {
            Chip("负责人 ${s.owners}")
            Chip("需求 ${s.requirements}")
            Chip("用例完成 ${s.caseDone}", QaColors.Success)
            Chip("用例待 ${s.casePending}")
            Chip("测试完成 ${s.testDone}", QaColors.Success)
            Chip("测试待 ${s.testPending}")
            Chip("待复测 ${s.retestPendingTotal}", QaColors.Danger)
        }
        Row(Modifier.fillMaxWidth().padding(top = QaSpacing.xs), verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = pendingOnly, onCheckedChange = { onToggle() })
            Text("仅看未完成需求", style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun OwnerCard(owner: OwnerProgress, pendingOnly: Boolean) {
    val reqs = owner.visibleReqs(pendingOnly)
    if (pendingOnly && reqs.isEmpty()) return
    val preview = LocalPreviewOpen.current
    Column(cardMod()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.Person, contentDescription = null, tint = QaColors.TextMuted, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(QaSpacing.xxs))
            Text("${owner.ownerName}（${reqs.size}）", style = MaterialTheme.typography.titleSmall, color = QaColors.TextStrong)
        }
        FlowRow(
            Modifier.padding(top = QaSpacing.xxs),
            horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs),
            verticalArrangement = Arrangement.spacedBy(QaSpacing.xs),
        ) {
            owner.majorSummaries.forEach { m ->
                Chip("${m.majorVersionName}：用例待${m.casePending} 测试待${m.testPending}")
            }
        }
        reqs.forEach { r ->
            Row(Modifier.fillMaxWidth().padding(top = QaSpacing.xxs), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "${r.zentaoReqId} ${r.title}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (r.caseCompleted && r.testCompleted) QaColors.TextDisabled else QaColors.TextDefault,
                    textDecoration = if (r.caseCompleted && r.testCompleted) TextDecoration.LineThrough else null,
                    modifier = Modifier.weight(1f),
                )
                MiniStatus("用例", r.caseCompleted)
                Spacer(Modifier.width(QaSpacing.xs))
                MiniStatus("测试", r.testCompleted)
                Spacer(Modifier.width(QaSpacing.xs))
                QaLinkLabel(
                    text = "预览",
                    onClick = { preview(PreviewKind.STORY, zentaoNumericId(r.zentaoReqId)) },
                    style = MaterialTheme.typography.labelSmall,
                )
            }
        }
    }
}

// ─────────────────────────── 关联需求 ───────────────────────────
@Composable
private fun LinkSection(
    ui: AssignUiState,
    sourceMajorOptions: List<Pair<Int, String>>,
    hasTarget: Boolean,
    vm: AssignViewModel,
) {
    if (!hasTarget) { CenterText("请先选择目标大版本", QaColors.TextMuted); return }
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg, vertical = QaSpacing.xs)) {
            LabeledDropdown(
                label = "来源大版本",
                selectedText = sourceMajorOptions.firstOrNull { it.first == ui.linkSourceMajorId }?.second ?: "请选择",
                options = sourceMajorOptions,
                onSelect = vm::setLinkSource,
            )
            Row(Modifier.fillMaxWidth().padding(top = QaSpacing.xs), verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = ui.linkCopyStatus, onCheckedChange = vm::setLinkCopyStatus)
                Text("同时复制完成状态", style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault, modifier = Modifier.weight(1f))
                if (ui.busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                else Button(onClick = vm::confirmLink, enabled = ui.linkSelected.isNotEmpty()) { Text("关联 (${ui.linkSelected.size})") }
            }
        }
        Box(Modifier.fillMaxWidth().weight(1f)) {
            when {
                ui.linkLoading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                ui.linkSourceMajorId == null -> CenterText("请选择来源大版本", QaColors.TextMuted)
                ui.linkOptions.isEmpty() -> CenterText("来源版本暂无可关联需求", QaColors.TextMuted)
                else -> LazyColumn(
                    Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(QaSpacing.lg),
                    verticalArrangement = Arrangement.spacedBy(QaSpacing.xs),
                ) {
                    items(ui.linkOptions, key = { it.id }) { opt -> LinkRow(opt, opt.id in ui.linkSelected) { vm.toggleLinkSelect(opt.id) } }
                }
            }
        }
    }
}

@Composable
private fun LinkRow(opt: LinkOption, checked: Boolean, onToggle: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.sm))
            .background(QaColors.Card, RoundedCornerShape(QaRadius.sm))
            .padding(QaSpacing.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked = checked, onCheckedChange = { onToggle() }, enabled = !opt.alreadyLinked)
        Column(Modifier.weight(1f)) {
            Text("${opt.zentaoReqId} ${opt.title}", style = MaterialTheme.typography.bodyMedium, color = QaColors.TextStrong)
            Text("负责人：${opt.ownerName} ｜ 用例 ${opt.caseCount}", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
        }
        if (opt.alreadyLinked) Text("已在目标", style = MaterialTheme.typography.labelSmall, color = QaColors.Info)
    }
}

// ─────────────────────────── 通用 ───────────────────────────
@Composable
private fun Chip(text: String, color: androidx.compose.ui.graphics.Color = QaColors.TextDefault) {
    Box(
        Modifier
            .background(QaColors.Background, RoundedCornerShape(QaRadius.sm))
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.sm))
            .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
    ) { Text(text, style = MaterialTheme.typography.labelSmall, color = color) }
}

/** 紧凑状态标记：完成显示对勾（绿），未完成显示时钟（灰）。 */
@Composable
private fun MiniStatus(label: String, done: Boolean) {
    val color = if (done) QaColors.Success else QaColors.TextDisabled
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(
            if (done) Icons.Outlined.Check else Icons.Outlined.Schedule,
            contentDescription = null,
            tint = color,
            modifier = Modifier.size(13.dp),
        )
        Text(label, style = MaterialTheme.typography.labelSmall, color = color, modifier = Modifier.padding(start = 1.dp))
    }
}

@Composable
private fun Badge(text: String) {
    Box(
        Modifier.background(QaColors.PrimaryContainer, RoundedCornerShape(QaRadius.sm)).padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
    ) { Text(text, style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.SemiBold), color = QaColors.OnPrimaryContainer) }
}

private fun cardMod(): Modifier = Modifier
    .fillMaxWidth()
    .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
    .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
    .padding(QaSpacing.lg)
