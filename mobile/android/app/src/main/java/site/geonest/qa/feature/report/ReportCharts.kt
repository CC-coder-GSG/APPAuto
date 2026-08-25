package site.geonest.qa.feature.report

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaRadius
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.TeamMember
import site.geonest.qa.core.domain.model.TrendPoint
import site.geonest.qa.core.domain.model.VersionBug

/** 图表外壳：白底标题卡（顶层使用，不嵌套卡片）。 */
@Composable
fun ChartCard(title: String, modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier
            .fillMaxWidth()
            .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
    ) {
        Text(title, style = MaterialTheme.typography.titleMedium, color = QaColors.TextStrong)
        Spacer(Modifier.height(QaSpacing.sm))
        content()
    }
}

private data class Series(val name: String, val color: Color, val values: List<Int>)

// ─────────────────────── 趋势折线图 ───────────────────────
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TrendLineChart(trend: List<TrendPoint>) {
    if (trend.isEmpty()) {
        EmptyHint("该区间暂无趋势数据"); return
    }
    val allSeries = listOf(
        Series("执行需求", QaColors.Primary, trend.map { it.executedRequirements }),
        Series("创建用例", QaColors.Accent, trend.map { it.createdCases }),
        Series("创建 Bug", QaColors.Warning, trend.map { it.createdBugs }),
        Series("复测需求", Color(0xFF7C3AED), trend.map { it.retestedReqs }),
        Series("闭环 Bug", QaColors.Success, trend.map { it.closedBugs }),
    )
    // 图例可点选切换，避免多线拥挤。
    val hidden = remember { androidx.compose.runtime.mutableStateMapOf<String, Boolean>() }
    val visible = allSeries.filter { hidden[it.name] != true }
    val maxV = (visible.flatMap { it.values }.maxOrNull() ?: 0).coerceAtLeast(1)

    Text("峰值 $maxV", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
    Spacer(Modifier.height(QaSpacing.xs))
    Canvas(
        Modifier
            .fillMaxWidth()
            .height(180.dp)
            .padding(vertical = QaSpacing.xs),
    ) {
        val w = size.width
        val h = size.height
        val topPad = 8f
        val n = trend.size
        val stepX = if (n > 1) w / (n - 1) else 0f
        // 基线
        drawLine(QaColors.Border, Offset(0f, h), Offset(w, h), strokeWidth = 2f)
        visible.forEach { s ->
            val pts = s.values.mapIndexed { i, v ->
                val x = if (n > 1) i * stepX else w / 2f
                val y = h - (v.toFloat() / maxV) * (h - topPad)
                Offset(x, y)
            }
            for (i in 1 until pts.size) {
                drawLine(s.color, pts[i - 1], pts[i], strokeWidth = 4f)
            }
            if (n <= 31) pts.forEach { drawCircle(s.color, 4f, it) }
        }
    }
    // X 轴首尾日期
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(shortDate(trend.first().date), style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
        Text(shortDate(trend.last().date), style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
    }
    Spacer(Modifier.height(QaSpacing.sm))
    FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm), verticalArrangement = Arrangement.spacedBy(QaSpacing.xs)) {
        allSeries.forEach { s ->
            val on = hidden[s.name] != true
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.clickable { hidden[s.name] = on },
            ) {
                Box(Modifier.size(10.dp).background(if (on) s.color else QaColors.TextDisabled, RoundedCornerShape(QaRadius.sm)))
                Text(
                    s.name,
                    style = MaterialTheme.typography.labelSmall,
                    color = if (on) QaColors.TextDefault else QaColors.TextDisabled,
                    modifier = Modifier.padding(start = QaSpacing.xxs),
                )
            }
        }
    }
}

// ─────────────────────── 团队对比 ───────────────────────
private val TEAM_METRICS = listOf<Pair<String, (TeamMember) -> Int>>(
    "执行需求" to { it.executedRequirements },
    "创建用例" to { it.createdCases },
    "创建 Bug" to { it.createdBugs },
    "处理反馈" to { it.processedFeedbacks },
    "复测需求" to { it.retestedReqs },
    "闭环 Bug" to { it.closedBugs },
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TeamCompareChart(team: List<TeamMember>) {
    if (team.isEmpty()) {
        EmptyHint("仅管理员全员视角可见团队对比"); return
    }
    var metricIdx by remember { mutableStateOf(2) } // 默认「创建 Bug」
    val selector = TEAM_METRICS[metricIdx]
    val rows = team.map { it.username to selector.second(it) }.sortedByDescending { it.second }
    val maxV = (rows.maxOfOrNull { it.second } ?: 0).coerceAtLeast(1)

    FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs), verticalArrangement = Arrangement.spacedBy(QaSpacing.xs)) {
        TEAM_METRICS.forEachIndexed { i, (name, _) ->
            val sel = i == metricIdx
            Box(
                Modifier
                    .background(if (sel) QaColors.PrimaryContainer else QaColors.SurfaceSubtle, RoundedCornerShape(QaRadius.pill))
                    .border(1.dp, if (sel) QaColors.Primary else QaColors.Border, RoundedCornerShape(QaRadius.pill))
                    .clickable { metricIdx = i }
                    .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
            ) {
                Text(name, style = MaterialTheme.typography.labelSmall, color = if (sel) QaColors.Primary else QaColors.TextMuted)
            }
        }
    }
    Spacer(Modifier.height(QaSpacing.sm))
    Column(verticalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
        rows.forEach { (name, value) ->
            BarRow(label = name, value = value, maxValue = maxV, color = QaColors.Primary)
        }
    }
}

// ─────────────────────── 各版本 Bug 检出分布 ───────────────────────
@Composable
fun VersionBugBarChart(versionBugs: List<VersionBug>) {
    if (versionBugs.isEmpty()) {
        EmptyHint("该大版本暂无检出 Bug"); return
    }
    val maxV = (versionBugs.maxOfOrNull { it.bugCount } ?: 0).coerceAtLeast(1)
    Column(verticalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
        versionBugs.forEach { vb ->
            BarRow(
                label = vb.minorName.ifBlank { vb.majorName },
                sublabel = vb.majorName.takeIf { it.isNotBlank() && vb.minorName.isNotBlank() },
                value = vb.bugCount,
                maxValue = maxV,
                color = QaColors.Accent,
            )
        }
    }
}

// ─────────────────────── 通用横向条 ───────────────────────
@Composable
private fun BarRow(label: String, value: Int, maxValue: Int, color: Color, sublabel: String? = null) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.width(76.dp)) {
            Text(label, style = MaterialTheme.typography.labelSmall, color = QaColors.TextDefault, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (sublabel != null) {
                Text(sublabel, style = MaterialTheme.typography.labelSmall, color = QaColors.TextDisabled, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        Spacer(Modifier.width(QaSpacing.sm))
        Box(
            Modifier
                .weight(1f)
                .height(18.dp)
                .background(QaColors.SurfaceSubtle, RoundedCornerShape(QaRadius.sm)),
        ) {
            val frac = (value.toFloat() / maxValue).coerceIn(0f, 1f)
            if (frac > 0f) {
                Box(
                    Modifier
                        .fillMaxWidth(frac)
                        .height(18.dp)
                        .background(color, RoundedCornerShape(QaRadius.sm)),
                )
            }
        }
        Spacer(Modifier.width(QaSpacing.sm))
        Text(
            value.toString(),
            style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold),
            color = QaColors.TextStrong,
            modifier = Modifier.width(28.dp),
        )
    }
}

@Composable
private fun EmptyHint(text: String) {
    Box(Modifier.fillMaxWidth().padding(QaSpacing.lg), contentAlignment = Alignment.Center) {
        Text(text, style = MaterialTheme.typography.labelMedium, color = QaColors.TextDisabled)
    }
}

private fun shortDate(d: String): String = if (d.length >= 10) d.substring(5) else d
