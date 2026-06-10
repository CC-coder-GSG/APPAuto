package site.geonest.qa.feature.retest

import android.content.Intent
import android.net.Uri
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
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.OpenInNew
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaLinkLabel
import site.geonest.qa.core.designsystem.QaRadius
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.PreviewKind
import site.geonest.qa.core.domain.model.RetestRequirement
import site.geonest.qa.core.domain.model.WbBug
import site.geonest.qa.core.domain.model.WorkbenchMode
import site.geonest.qa.feature.common.WorkbenchFilterBar
import site.geonest.qa.feature.preview.LocalPreviewOpen
import site.geonest.qa.feature.preview.zentaoNumericId

/** 待确认的复测动作（用于二次确认弹窗）。 */
private data class PendingAction(val req: RetestRequirement, val passed: Boolean)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RetestScreen(
    onMessage: (String) -> Unit,
    viewModel: RetestViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val ctx by viewModel.ctx.collectAsStateWithLifecycle()
    var pending by remember { mutableStateOf<PendingAction?>(null) }

    LaunchedEffect(state.toast) {
        state.toast?.let {
            onMessage(it)
            viewModel.consumeToast()
        }
    }

    Column(Modifier.fillMaxSize()) {
        WorkbenchFilterBar(
            ctx = ctx,
            showMode = true,
            onSelectSoftware = viewModel::selectSoftware,
            onSelectMode = viewModel::setMode,
            onSelectMajor = viewModel::selectMajor,
        )

        if (ctx.ready && state.error == null && !state.loading) {
            Text(
                "待复测 ${state.items.size} 个",
                style = MaterialTheme.typography.labelMedium,
                color = QaColors.TextMuted,
                modifier = Modifier.padding(horizontal = QaSpacing.lg, vertical = QaSpacing.xxs),
            )
        }

        Box(Modifier.fillMaxWidth().weight(1f)) {
            when {
                ctx.loading || (state.loading && ctx.ready) -> CircularProgressIndicator(Modifier.align(Alignment.Center))

                ctx.error != null -> Column(
                    Modifier.align(Alignment.Center).padding(QaSpacing.xl),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(ctx.error!!, color = QaColors.Danger)
                    Spacer(Modifier.padding(QaSpacing.sm))
                    FilterChip(selected = false, onClick = { viewModel.retryContext() }, label = { Text("重试") })
                }

                !ctx.ready -> Text(
                    if (ctx.mode == WorkbenchMode.VERSION) "请选择软件与大版本" else "请选择软件",
                    color = QaColors.TextMuted,
                    modifier = Modifier.align(Alignment.Center),
                )

                state.error != null -> Column(
                    Modifier.align(Alignment.Center).padding(QaSpacing.xl),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(state.error!!, color = QaColors.Danger)
                    Spacer(Modifier.padding(QaSpacing.sm))
                    FilterChip(selected = false, onClick = { viewModel.load() }, label = { Text("重试") })
                }

                state.isEmpty -> Text(
                    "暂无待复测的需求",
                    color = QaColors.TextMuted,
                    modifier = Modifier.align(Alignment.Center),
                )

                else -> PullToRefreshBox(
                    isRefreshing = state.refreshing,
                    onRefresh = { viewModel.load(refresh = true) },
                ) {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(QaSpacing.lg),
                        verticalArrangement = Arrangement.spacedBy(QaSpacing.md),
                    ) {
                        items(state.items, key = { it.id }) { req ->
                            RetestCard(
                                req = req,
                                submitting = state.submittingId == req.id,
                                onPass = { pending = PendingAction(req, passed = true) },
                                onFail = { pending = PendingAction(req, passed = false) },
                            )
                        }
                    }
                }
            }
        }
    }

    pending?.let { action ->
        ConfirmDialog(
            action = action,
            onConfirm = {
                if (action.passed) viewModel.markPassed(action.req.id) else viewModel.markFailed(action.req.id)
                pending = null
            },
            onDismiss = { pending = null },
        )
    }
}

@Composable
private fun ConfirmDialog(action: PendingAction, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    val pass = action.passed
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (pass) "标记复测通过" else "打回（复测未通过）") },
        text = {
            Text(
                if (pass) "确认「${action.req.title}」复测通过？通过后将进入已复测，若仍有未闭环 Bug 会被后端拦截。"
                else "确认打回「${action.req.title}」？需至少存在一个未修好 / 未闭环的 Bug 作为证据。",
            )
        },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(if (pass) "确认通过" else "确认打回", color = if (pass) QaColors.Success else QaColors.Danger)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun RetestCard(
    req: RetestRequirement,
    submitting: Boolean,
    onPass: () -> Unit,
    onFail: () -> Unit,
) {
    val preview = LocalPreviewOpen.current
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
    ) {
        // 标题行
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (req.zentaoReqId.isNotBlank()) {
                Badge(req.zentaoReqId)
                Spacer(Modifier.width(QaSpacing.sm))
            }
            Text(
                text = req.title,
                style = MaterialTheme.typography.titleMedium,
                color = QaColors.TextStrong,
                modifier = Modifier.weight(1f),
            )
            QaLinkLabel(
                text = "预览",
                onClick = { preview(PreviewKind.STORY, zentaoNumericId(req.zentaoReqId)) },
            )
        }
        Spacer(Modifier.padding(QaSpacing.xxs))
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (req.owner.isNotBlank()) {
                Text("测试：${req.owner}", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
                Spacer(Modifier.width(QaSpacing.sm))
            }
            if (req.majorVersionName.isNotBlank()) {
                Text(req.majorVersionName, style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
                Spacer(Modifier.width(QaSpacing.sm))
            }
            Text("Bug ${req.bugCount}", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
        }

        // 用例下挂 Bug
        if (req.testCases.any { it.bugs.isNotEmpty() }) {
            Spacer(Modifier.padding(QaSpacing.xs))
            req.testCases.filter { it.bugs.isNotEmpty() }.forEach { case ->
                Column(Modifier.fillMaxWidth().padding(vertical = QaSpacing.xxs)) {
                    Text("用例 ${case.zentaoCaseId}", style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault)
                    BugChips(case.bugs)
                }
            }
        }

        // 自由 Bug
        if (req.freeBugs.isNotEmpty()) {
            Spacer(Modifier.padding(QaSpacing.xs))
            Text("自由 Bug", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
            BugChips(req.freeBugs)
        }

        // 复测 Bug（漏测/复测来源）
        if (req.retestBugs.isNotEmpty()) {
            Spacer(Modifier.padding(QaSpacing.xs))
            Text("复测 Bug", style = MaterialTheme.typography.labelSmall, color = QaColors.Warning)
            BugChips(req.retestBugs, danger = true)
        }

        // 操作区
        Spacer(Modifier.padding(QaSpacing.xs))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
            if (submitting) {
                CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(QaSpacing.xs))
                Text("提交中…", style = MaterialTheme.typography.labelMedium, color = QaColors.TextMuted)
            } else {
                OutlinedButton(
                    onClick = onFail,
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = QaColors.Danger),
                ) {
                    Icon(Icons.Outlined.Close, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(QaSpacing.xs))
                    Text("打回")
                }
                Button(
                    onClick = onPass,
                    colors = ButtonDefaults.buttonColors(containerColor = QaColors.Success),
                ) {
                    Icon(Icons.Outlined.Check, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(QaSpacing.xs))
                    Text("通过")
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BugChips(bugs: List<WbBug>, danger: Boolean = false) {
    val bg = if (danger) QaColors.Background else QaColors.SuccessContainer
    val border = if (danger) QaColors.Border else QaColors.SuccessBorder
    val fg = if (danger) QaColors.Danger else QaColors.Success
    val preview = LocalPreviewOpen.current
    val context = LocalContext.current
    FlowRow(
        modifier = Modifier.fillMaxWidth().padding(top = QaSpacing.xxs),
        horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs),
        verticalArrangement = Arrangement.spacedBy(QaSpacing.xs),
    ) {
        bugs.forEach { bug ->
            val text = listOfNotNull(bug.bugId.takeIf { it.isNotBlank() }, bug.title.takeIf { it.isNotBlank() })
                .joinToString(" ")
                .ifBlank { "Bug#${bug.id}" }
            Row(
                Modifier
                    .border(1.dp, border, RoundedCornerShape(QaRadius.sm))
                    .background(bg, RoundedCornerShape(QaRadius.sm))
                    .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // 点徽标文字 → 预览 Bug
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.clickable { preview(PreviewKind.BUG, zentaoNumericId(bug.bugId)) },
                ) {
                    Text(text, style = MaterialTheme.typography.labelSmall, color = fg)
                    Spacer(Modifier.width(QaSpacing.xxs))
                    Icon(Icons.Outlined.Visibility, contentDescription = "预览", tint = fg, modifier = Modifier.size(13.dp))
                }
                // 跳转禅道（有链接才显示）
                val bugUrl = bug.zentaoBugUrl
                if (!bugUrl.isNullOrBlank()) {
                    Spacer(Modifier.width(QaSpacing.xs))
                    Icon(
                        Icons.Outlined.OpenInNew,
                        contentDescription = "在禅道打开",
                        tint = QaColors.Accent,
                        modifier = Modifier
                            .size(14.dp)
                            .clickable {
                                runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(bugUrl))) }
                            },
                    )
                }
            }
        }
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
