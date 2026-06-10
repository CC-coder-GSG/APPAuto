package site.geonest.qa.core.designsystem

import androidx.compose.ui.graphics.Color

/**
 * 颜色令牌，源自 mobile/shared-design/design-tokens.json，与 Web 端视觉对齐。
 * iOS 端将映射同一套语义到 SwiftUI，确保两端一致。
 */
object QaColors {
    val Primary = Color(0xFF0F766E)
    val PrimaryDark = Color(0xFF115E59)
    val PrimaryContainer = Color(0xFFE6FFFA)
    val OnPrimaryContainer = Color(0xFF0F766E)
    val Accent = Color(0xFF2563EB)
    val AccentContainer = Color(0xFFEFF6FF)

    val TextStrong = Color(0xFF0F172A)
    val TextDefault = Color(0xFF1E293B)
    val TextMuted = Color(0xFF64748B)
    val TextDisabled = Color(0xFF94A3B8)

    val Background = Color(0xFFF5F7FB)
    val SurfaceSubtle = Color(0xFFF8FAFC)
    val Card = Color(0xFFFFFFFF)
    val CardPressed = Color(0xFFF1F5F9)
    val Border = Color(0xFFE2E8F0)
    val BorderStrong = Color(0xFFCBD5E1)

    val Success = Color(0xFF16A34A)
    val SuccessContainer = Color(0xFFF0FDF4)
    val SuccessBorder = Color(0xFFBBF7D0)
    val Warning = Color(0xFFB45309)
    val WarningContainer = Color(0xFFFFF7ED)
    val WarningBorder = Color(0xFFFED7AA)
    val Danger = Color(0xFFE11D48)
    val DangerContainer = Color(0xFFFFF1F2)
    val DangerBorder = Color(0xFFFFCCD5)
    val Info = Accent
}
