package site.geonest.qa.feature.workbench

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
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
import site.geonest.qa.core.designsystem.QaRadius
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.WbBug
import site.geonest.qa.core.domain.model.WorkbenchRequirement

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WorkbenchScreen(
    onMessage: (String) -> Unit,
    viewModel: WorkbenchViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    LaunchedEffect(state.toast) {
        state.toast?.let {
            onMessage(it)
            viewModel.consumeToast()
        }
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = QaSpacing.lg, vertical = QaSpacing.sm),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            FilterChip(
                selected = state.pendingOnly,
                onClick = viewModel::togglePendingOnly,
                label = { Text("仅看未完成") },
            )
            Spacer(Modifier.width(QaSpacing.sm))
            Text(
                "共 ${state.items.size} 个需求",
                style = MaterialTheme.typography.labelSmall,
                color = QaColors.TextMuted,
            )
        }

        Box(Modifier.fillMaxSize()) {
            when {
                state.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))

                state.error != null -> Column(
                    Modifier.align(Alignment.Center).padding(QaSpacing.xl),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(state.error!!, color = QaColors.Danger)
                    Spacer(Modifier.padding(QaSpacing.sm))
                    FilterChip(selected = false, onClick = { viewModel.load() }, label = { Text("重试") })
                }

                state.isEmpty -> Text(
                    "暂无负责的需求",
                    color = QaColors.TextMuted,
                    modifier = Modifier.align(Alignment.Center),
                )

                else -> PullToRefreshBox(
                    isRefreshing = state.refreshing,
                    onRefresh = { viewModel.load(refresh = true) },
                ) {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(QaSpacing.lg),
                        verticalArrangement = Arrangement.spacedBy(QaSpacing.md),
                    ) {
                        items(state.items, key = { it.id }) { req ->
                            RequirementCard(
                                req = req,
                                onCaseToggle = { viewModel.toggleCaseCompleted(req.id, it) },
                                onTestToggle = { viewModel.toggleTestCompleted(req.id, it) },
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun RequirementCard(
    req: WorkbenchRequirement,
    onCaseToggle: (Boolean) -> Unit,
    onTestToggle: (Boolean) -> Unit,
) {
    val fullyDone = req.caseCompleted && req.testCompleted
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .background(if (fullyDone) QaColors.Background else QaColors.Card, RoundedCornerShape(QaRadius.lg))
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
                color = if (fullyDone) QaColors.TextDisabled else QaColors.TextStrong,
                textDecoration = if (fullyDone) TextDecoration.LineThrough else null,
                modifier = Modifier.weight(1f),
            )
        }
        Spacer(Modifier.padding(QaSpacing.xxs))
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (req.majorVersionName.isNotBlank()) {
                Text(req.majorVersionName, style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
                Spacer(Modifier.width(QaSpacing.sm))
            }
            Text("Bug ${req.bugCount}", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
        }

        // 状态勾选
        Spacer(Modifier.padding(QaSpacing.xs))
        Row(verticalAlignment = Alignment.CenterVertically) {
            CheckRow("用例完成", req.caseCompleted, onCaseToggle)
            Spacer(Modifier.width(QaSpacing.lg))
            CheckRow("测试完成", req.testCompleted, onTestToggle)
        }

        // 用例
        if (req.testCases.isNotEmpty()) {
            Spacer(Modifier.padding(QaSpacing.xs))
            req.testCases.forEach { case ->
                Column(Modifier.fillMaxWidth().padding(vertical = QaSpacing.xxs)) {
                    Text(
                        "用例 ${case.zentaoCaseId}",
                        style = MaterialTheme.typography.bodyMedium,
                        color = QaColors.TextDefault,
                    )
                    if (case.bugs.isNotEmpty()) BugChips(case.bugs)
                }
            }
        }

        // 自由 Bug
        if (req.freeBugs.isNotEmpty()) {
            Spacer(Modifier.padding(QaSpacing.xs))
            Text("自由 Bug", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
            BugChips(req.freeBugs)
        }
    }
}

@Composable
private fun CheckRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Checkbox(
            checked = checked,
            onCheckedChange = onChange,
            colors = CheckboxDefaults.colors(checkedColor = QaColors.Success),
        )
        Text(label, style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BugChips(bugs: List<WbBug>) {
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
                    .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
            ) {
                Text(text, style = MaterialTheme.typography.labelSmall, color = QaColors.Success)
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
