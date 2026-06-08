package site.geonest.qa.core.designsystem

import androidx.compose.ui.graphics.Color

/**
 * 颜色令牌，源自 mobile/shared-design/design-tokens.json，与 Web 端视觉对齐。
 * iOS 端将映射同一套语义到 SwiftUI，确保两端一致。
 */
object QaColors {
    val Primary = Color(0xFF2563EB)
    val PrimaryDark = Color(0xFF1D4ED8)
    val PrimaryContainer = Color(0xFFEFF6FF)
    val OnPrimaryContainer = Color(0xFF1D4ED8)

    val TextStrong = Color(0xFF0F172A)
    val TextDefault = Color(0xFF1E293B)
    val TextMuted = Color(0xFF64748B)
    val TextDisabled = Color(0xFF94A3B8)

    val Background = Color(0xFFF8FAFC)
    val Card = Color(0xFFFFFFFF)
    val Border = Color(0xFFE2E8F0)

    val Success = Color(0xFF16A34A)
    val SuccessContainer = Color(0xFFF0FDF4)
    val SuccessBorder = Color(0xFFBBF7D0)
    val Warning = Color(0xFFD97706)
    val Danger = Color(0xFFDC2626)
    val Info = Color(0xFF2563EB)
}
