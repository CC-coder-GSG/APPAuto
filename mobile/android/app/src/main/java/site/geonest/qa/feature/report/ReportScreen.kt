package site.geonest.qa.feature.report

import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaRadius
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.Governance
import site.geonest.qa.core.domain.model.ReportData
import site.geonest.qa.feature.common.LabeledDropdown

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ReportScreen(
    onBack: () -> Unit,
    viewModel: ReportViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val ctx by viewModel.ctx.collectAsStateWithLifecycle()

    Scaffold(
        containerColor = QaColors.Background,
        topBar = {
            TopAppBar(
                title = { Text("报表中心") },
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
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            // 筛选面板：软件 + 大版本 + 时间范围
            Column(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = QaSpacing.lg, vertical = QaSpacing.sm)
                    .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
                    .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
                    .padding(QaSpacing.md),
                verticalArrangement = Arrangement.spacedBy(QaSpacing.sm),
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                    LabeledDropdown(
                        label = "软件",
                        selectedText = ctx.softwares.firstOrNull { it.id == ctx.softwareId }?.name ?: "未选择",
                        options = ctx.softwares.map { it.id to it.name },
                        onSelect = viewModel::selectSoftware,
                        modifier = Modifier.weight(1f),
                    )
                    LabeledDropdown(
                        label = "大版本",
                        selectedText = ctx.majors.firstOrNull { it.id == ui.majorId }?.versionNo ?: "全部",
                        options = listOf(0 to "全部") + ctx.majors.map { it.id to it.versionNo },
                        onSelect = viewModel::selectMajor,
                        modifier = Modifier.weight(1f),
                    )
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                    ReportRange.entries.forEach { r ->
                        FilterChip(
                            selected = ui.range == r,
                            onClick = { viewModel.setRange(r) },
                            label = { Text(r.label) },
                        )
                    }
                }
            }

            Box(Modifier.fillMaxWidth().weight(1f)) {
                when {
                    ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                    ctx.softwareId == null -> Text("请选择软件", color = QaColors.TextMuted, modifier = Modifier.align(Alignment.Center))
                    ui.error != null -> Column(
                        Modifier.align(Alignment.Center).padding(QaSpacing.xl),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text(ui.error!!, color = QaColors.Danger)
                        Spacer(Modifier.padding(QaSpacing.sm))
                        FilterChip(selected = false, onClick = { viewModel.load() }, label = { Text("重试") })
                    }
                    ui.data == null -> Text("暂无数据", color = QaColors.TextMuted, modifier = Modifier.align(Alignment.Center))
                    else -> ReportBody(ui.data!!)
                }
            }
        }
    }
}

@Composable
private fun ReportBody(data: ReportData) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(QaSpacing.lg),
        verticalArrangement = Arrangement.spacedBy(QaSpacing.md),
    ) {
        item("output") {
            SectionTitle("测试产出")
            Spacer(Modifier.padding(QaSpacing.xxs))
            val metrics = listOf(
                Triple("执行需求", data.executedRequirements, QaColors.Primary),
                Triple("新建用例", data.createdCases, QaColors.Accent),
                Triple("提交 Bug", data.createdBugs, QaColors.Warning),
                Triple("复测需求", data.retestedReqs, QaColors.Primary),
                Triple("闭环 Bug", data.closedBugs, QaColors.Success),
            )
            MetricGrid(metrics)
        }
        item("trend") {
            ChartCard("产出趋势") { TrendLineChart(data.trend) }
        }
        if (data.team.isNotEmpty()) {
            item("team") {
                ChartCard("团队对比") { TeamCompareChart(data.team) }
            }
        }
        item("versionBugs") {
            ChartCard("各版本 Bug 检出分布") { VersionBugBarChart(data.versionBugs) }
        }
        item("governance") {
            SectionTitle("治理告警")
            Spacer(Modifier.padding(QaSpacing.xxs))
            GovernanceGrid(data.governance)
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleMedium, color = QaColors.TextStrong)
}

@Composable
private fun MetricGrid(metrics: List<Triple<String, Int, Color>>) {
    Column(verticalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
        metrics.chunked(2).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                row.forEach { (label, value, color) ->
                    MetricCard(label, value.toString(), color, Modifier.weight(1f))
                }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun MetricCard(label: String, value: String, color: Color, modifier: Modifier = Modifier) {
    Column(
        modifier
            .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
    ) {
        Text(value, style = MaterialTheme.typography.headlineMedium.copy(fontWeight = FontWeight.Bold), color = color)
        Spacer(Modifier.padding(QaSpacing.xxs))
        Text(label, style = MaterialTheme.typography.labelMedium, color = QaColors.TextMuted)
    }
}

@Composable
private fun GovernanceGrid(g: Governance) {
    // value -> 非 0 用告警色，0 用静默色，突出需要关注的项。
    val items = listOf(
        Triple("超时未关闭需求", g.overdueRequirements, QaColors.Warning),
        Triple("超时未处理反馈", g.overdueFeedbacks, QaColors.Warning),
        Triple("无人处理 Bug", g.unassignedBugs, QaColors.Danger),
        Triple("超时未关闭 Bug", g.overdueBugs, QaColors.Danger),
        Triple("长期未更新 Bug", g.staleBugs, QaColors.Warning),
        Triple("已派未处理 Bug", g.assignedNoProgressBugs, QaColors.Danger),
    )
    Column(verticalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
        items.chunked(2).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                row.forEach { (label, value, alertColor) ->
                    val color = if (value > 0) alertColor else QaColors.TextDisabled
                    MetricCard(label, value.toString(), color, Modifier.weight(1f))
                }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
        Row(
            Modifier.fillMaxWidth()
                .background(QaColors.SurfaceSubtle, RoundedCornerShape(QaRadius.lg))
                .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
                .padding(QaSpacing.lg),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("反馈转 Bug 率", style = MaterialTheme.typography.labelMedium, color = QaColors.TextMuted, modifier = Modifier.weight(1f))
            Text("${g.feedbackToBugRatio}%", style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold), color = QaColors.Primary)
        }
    }
}
