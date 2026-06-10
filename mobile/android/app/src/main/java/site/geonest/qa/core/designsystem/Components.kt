package site.geonest.qa.core.designsystem

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

@Composable
fun QaScreenBackground(
    modifier: Modifier = Modifier,
    content: @Composable BoxScope.() -> Unit,
) {
    Box(
        modifier = modifier.background(
            Brush.verticalGradient(
                colors = listOf(Color(0xFFEFFCF8), QaColors.Background, QaColors.SurfaceSubtle),
            ),
        ),
        content = content,
    )
}

@Composable
fun QaPanel(
    modifier: Modifier = Modifier,
    elevated: Boolean = false,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(QaRadius.lg),
        color = QaColors.Card,
        tonalElevation = if (elevated) 2.dp else 0.dp,
        shadowElevation = if (elevated) 2.dp else 0.dp,
        border = androidx.compose.foundation.BorderStroke(1.dp, QaColors.Border),
    ) {
        Column(Modifier.padding(QaSpacing.lg), content = content)
    }
}

@Composable
fun QaGlassPanel(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier
            .background(Color.White.copy(alpha = 0.78f), RoundedCornerShape(QaRadius.xl))
            .border(1.dp, Color.White.copy(alpha = 0.88f), RoundedCornerShape(QaRadius.xl))
            .padding(QaSpacing.lg),
        content = content,
    )
}

@Composable
fun QaPill(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = QaColors.Primary,
    container: Color = QaColors.PrimaryContainer,
) {
    Box(
        modifier
            .background(container, RoundedCornerShape(QaRadius.pill))
            .border(1.dp, color.copy(alpha = 0.18f), RoundedCornerShape(QaRadius.pill))
            .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xs),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.SemiBold),
            color = color,
        )
    }
}

/**
 * 链接/预览/外部跳转的统一样式：系统图标 + 文本，accent（蓝）色。
 * 对应设计语言「accent 用于链接、预览、外部跳转」，替代 emoji 图标。
 */
@Composable
fun QaLinkLabel(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: ImageVector = Icons.Outlined.Visibility,
    color: Color = QaColors.Accent,
    style: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.labelMedium,
) {
    Row(
        modifier = modifier
            .clickable(onClick = onClick)
            .padding(horizontal = QaSpacing.xs, vertical = QaSpacing.xxs),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(15.dp))
        Text(
            text = text,
            style = style.copy(fontWeight = FontWeight.Medium),
            color = color,
            modifier = Modifier.padding(start = 3.dp),
        )
    }
}

@Composable
fun QaMetric(
    label: String,
    value: String,
    color: Color,
    modifier: Modifier = Modifier,
) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        Text(value, style = MaterialTheme.typography.titleMedium, color = color)
        Text(
            text = "  $label",
            style = MaterialTheme.typography.labelSmall,
            color = QaColors.TextMuted,
        )
    }
}
