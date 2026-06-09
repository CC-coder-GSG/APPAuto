package site.geonest.qa.feature.preview

import android.content.Intent
import android.net.Uri
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import okhttp3.OkHttpClient
import okhttp3.Request
import site.geonest.qa.BuildConfig
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.PreviewContent
import site.geonest.qa.core.domain.model.PreviewKind

/** 全局预览入口：卡片通过它打开 bug/需求/用例预览。在 MainScreen 注入实现。 */
val LocalPreviewOpen = staticCompositionLocalOf<(PreviewKind, Int) -> Unit> { { _, _ -> } }

/** 从 "b#29009" / "s#123" / "123" 提取禅道数字 ID。 */
fun zentaoNumericId(raw: String?): Int = (raw ?: "").filter { it.isDigit() }.toIntOrNull() ?: 0

/**
 * 预览宿主：放在 MainScreen 顶层。监听 [PreviewViewModel] 显示全屏预览弹窗，
 * 正文用 WebView 渲染禅道富 HTML（图片经 OkHttp 注入 JWT 拉取）。
 */
@Composable
fun PreviewHost(viewModel: PreviewViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    if (!state.visible) return
    val context = LocalContext.current
    val token = viewModel.tokenStore.token.value

    Dialog(
        onDismissRequest = viewModel::close,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        Surface(
            // 全屏弹窗：避开状态栏/导航栏，否则底部内容会被系统栏遮住、超出可视区。
            modifier = Modifier
                .fillMaxSize()
                .windowInsetsPadding(WindowInsets.systemBars)
                .padding(QaSpacing.sm),
            shape = RoundedCornerShape(14.dp),
            color = QaColors.Card,
        ) {
            Column(Modifier.fillMaxSize()) {
                val content = state.content
                // 头部
                Row(
                    Modifier.fillMaxWidth().padding(QaSpacing.lg),
                    verticalAlignment = Alignment.Top,
                ) {
                    Column(Modifier.weight(1f)) {
                        if (content != null) {
                            Text(
                                "${kindLabel(content.kind)} ${content.idLabel}",
                                style = MaterialTheme.typography.labelMedium,
                                color = QaColors.Primary,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                content.title,
                                style = MaterialTheme.typography.titleMedium,
                                color = QaColors.TextStrong,
                            )
                            if (content.metaLine.isNotBlank()) {
                                Text(content.metaLine, style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
                            }
                        } else {
                            Text("预览", style = MaterialTheme.typography.titleMedium, color = QaColors.TextStrong)
                        }
                    }
                    val zentaoUrl = content?.zentaoUrl
                    if (!zentaoUrl.isNullOrBlank()) {
                        TextButton(onClick = {
                            runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(zentaoUrl))) }
                        }) { Text("禅道打开 ↗") }
                    }
                    TextButton(onClick = viewModel::close) { Text("关闭", color = QaColors.Danger) }
                }
                HorizontalDivider(color = QaColors.Border)

                // 正文占据 header 之后的「剩余」空间——必须用 weight，不能用 fillMaxSize，
                // 否则正文会占满整屏高度，被挤到 header 下方后底部超出屏幕。
                Box(Modifier.fillMaxWidth().weight(1f)) {
                    when {
                        state.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                        state.error != null -> Text(
                            state.error!!,
                            color = QaColors.Danger,
                            modifier = Modifier.align(Alignment.Center).padding(QaSpacing.xl),
                        )
                        content != null -> PreviewWebView(content, token)
                    }
                }
            }
        }
    }
}

@Composable
private fun PreviewWebView(content: PreviewContent, token: String?) {
    val baseUrl = BuildConfig.API_BASE_URL.trimEnd('/') + "/"
    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { ctx ->
            WebView(ctx).apply {
                settings.javaScriptEnabled = false
                settings.useWideViewPort = true
                settings.loadWithOverviewMode = true
                settings.builtInZoomControls = true
                settings.displayZoomControls = false
                webViewClient = AuthImageWebViewClient(token)
                loadDataWithBaseURL(baseUrl, content.bodyHtml, "text/html", "utf-8", null)
            }
        },
        onRelease = { it.destroy() },
    )
}

/** 拦截 /zentao/files/ 图片请求，用独立 OkHttp 客户端手动注入 JWT（避免 401 误清登录态）。 */
private class AuthImageWebViewClient(private val token: String?) : WebViewClient() {
    private val client = OkHttpClient()

    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
        val url = request.url.toString()
        if (!url.contains("/zentao/files/")) return null
        return try {
            val builder = Request.Builder().url(url).header("X-Client-Type", "mobile")
            if (!token.isNullOrBlank()) builder.header("Authorization", "Bearer $token")
            val resp = client.newCall(builder.build()).execute()
            if (!resp.isSuccessful) { resp.close(); return null }
            val stream = resp.body?.byteStream() ?: return null
            val mime = resp.header("Content-Type")?.substringBefore(";")?.trim()?.ifBlank { "image/*" } ?: "image/*"
            WebResourceResponse(mime, "utf-8", stream)
        } catch (e: Exception) {
            null
        }
    }
}

private fun kindLabel(kind: PreviewKind): String = when (kind) {
    PreviewKind.STORY -> "需求"
    PreviewKind.BUG -> "Bug"
    PreviewKind.TESTCASE -> "用例"
}
