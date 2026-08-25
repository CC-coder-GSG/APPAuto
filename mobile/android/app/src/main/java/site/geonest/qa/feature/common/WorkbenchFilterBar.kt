package site.geonest.qa.feature.common

import androidx.compose.foundation.basicMarquee
import androidx.compose.foundation.clickable
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.unit.dp
import site.geonest.qa.core.data.WorkbenchContextState
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaRadius
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.WorkbenchMode

/**
 * 三个工作台共用的顶部筛选条：软件 + 模式（可选） + 大版本。
 * 选择回写共享 [WorkbenchContext]，切一处其余同步。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun WorkbenchFilterBar(
    ctx: WorkbenchContextState,
    showMode: Boolean,
    onSelectSoftware: (Int) -> Unit,
    onSelectMode: (WorkbenchMode) -> Unit,
    onSelectMajor: (Int) -> Unit,
) {
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
                onSelect = onSelectSoftware,
                modifier = Modifier.weight(1f),
            )
            // 需求工作台仅在「按版本」模式下需要大版本；测试工作台(showMode=false)始终需要。
            if (!showMode || ctx.mode == WorkbenchMode.VERSION) {
                LabeledDropdown(
                    label = "大版本",
                    selectedText = ctx.majors.firstOrNull { it.id == ctx.majorId }?.versionNo ?: "未选择",
                    options = ctx.majors.map { it.id to it.versionNo },
                    onSelect = onSelectMajor,
                    enabled = !showMode || ctx.mode == WorkbenchMode.VERSION,
                    modifier = Modifier.weight(1f),
                )
            }
        }
        if (showMode) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                WorkbenchMode.entries.forEach { m ->
                    FilterChip(
                        selected = ctx.mode == m,
                        onClick = { onSelectMode(m) },
                        label = { Text(m.label) },
                    )
                }
            }
        }
    }
}

/**
 * 只读下拉选择器：自绘固定高度字段（标签 + 单行值 + 下拉箭头），
 * 值过长时单行跑马灯滚动，避免换行撑高、两个下拉高度不齐（见 6-10 需求 1）。
 * 用稳定的 [DropdownMenu] + 透明遮罩，避开版本差异大的 ExposedDropdownMenu。
 */
@Composable
fun LabeledDropdown(
    label: String,
    selectedText: String,
    options: List<Pair<Int, String>>,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    var expanded by remember { mutableStateOf(false) }
    Box(modifier) {
        Column(
            Modifier
                .fillMaxWidth()
                .height(56.dp)
                .background(
                    if (enabled) QaColors.Card else QaColors.SurfaceSubtle,
                    RoundedCornerShape(QaRadius.md),
                )
                .border(
                    1.dp,
                    if (enabled) QaColors.BorderStrong else QaColors.Border,
                    RoundedCornerShape(QaRadius.md),
                )
                .padding(horizontal = QaSpacing.md, vertical = QaSpacing.sm),
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                label,
                style = MaterialTheme.typography.labelSmall,
                color = QaColors.TextMuted,
                maxLines = 1,
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    selectedText,
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (enabled) QaColors.TextStrong else QaColors.TextDisabled,
                    maxLines = 1,
                    softWrap = false,
                    modifier = Modifier.weight(1f).basicMarquee(),
                )
                Icon(Icons.Filled.ArrowDropDown, contentDescription = null, tint = QaColors.TextMuted)
            }
        }
        // 透明遮罩层捕获点击。
        Box(
            Modifier
                .matchParentSize()
                .clickable(enabled = enabled) { expanded = true },
        )
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { (id, text) ->
                DropdownMenuItem(
                    text = { Text(text) },
                    onClick = { expanded = false; onSelect(id) },
                )
            }
        }
    }
}
