package site.geonest.qa.feature.common

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import site.geonest.qa.core.data.WorkbenchContextState
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
            .padding(horizontal = QaSpacing.lg, vertical = QaSpacing.sm),
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
 * 只读下拉选择器：用稳定的 [DropdownMenu] + 透明可点遮罩，避开各版本差异较大的
 * ExposedDropdownMenu 实验 API。
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
        OutlinedTextField(
            value = selectedText,
            onValueChange = {},
            readOnly = true,
            enabled = enabled,
            label = { Text(label, style = MaterialTheme.typography.labelSmall) },
            trailingIcon = { Icon(Icons.Filled.ArrowDropDown, contentDescription = null) },
            modifier = Modifier.fillMaxWidth(),
        )
        // 透明遮罩层捕获点击（只读输入框本身不响应点击）。
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
