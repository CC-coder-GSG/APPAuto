package site.geonest.qa.core.designsystem

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val QaLightColorScheme = lightColorScheme(
    primary = QaColors.Primary,
    onPrimary = QaColors.Card,
    primaryContainer = QaColors.PrimaryContainer,
    onPrimaryContainer = QaColors.OnPrimaryContainer,
    background = QaColors.Background,
    onBackground = QaColors.TextStrong,
    surface = QaColors.Card,
    onSurface = QaColors.TextDefault,
    surfaceVariant = QaColors.Background,
    onSurfaceVariant = QaColors.TextMuted,
    outline = QaColors.Border,
    error = QaColors.Danger,
)

/**
 * 应用主题。当前仅亮色（与 Web 端一致）；深色模式留待后续按设计令牌补充，
 * 以便与 iOS 端保持统一切换策略。
 */
@Composable
fun QaTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = QaLightColorScheme,
        typography = QaTypography,
        content = content,
    )
}
