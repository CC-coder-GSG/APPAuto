package site.geonest.qa.feature.jenkins

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaRadius
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.JenkinsBuild
import site.geonest.qa.core.domain.model.JenkinsJobDetail
import site.geonest.qa.feature.common.LabeledDropdown
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun JenkinsScreen(
    onBack: () -> Unit,
    viewModel: JenkinsViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    LaunchedEffect(ui.toast) {
        ui.toast?.let {
            snackbar.currentSnackbarData?.dismiss()
            scope.launch { snackbar.showSnackbar(it) }
            viewModel.consumeToast()
        }
    }

    val inDetail = ui.detail != null || ui.detailLoading

    Scaffold(
        containerColor = QaColors.Background,
        topBar = {
            TopAppBar(
                title = { Text(if (inDetail) (ui.detail?.name ?: "构建详情") else "Jenkins") },
                navigationIcon = {
                    IconButton(onClick = { if (inDetail) viewModel.closeJob() else onBack() }) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = QaColors.Background,
                    titleContentColor = QaColors.TextStrong,
                    navigationIconContentColor = QaColors.TextStrong,
                ),
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                ui.error != null -> Column(
                    Modifier.align(Alignment.Center).padding(QaSpacing.xl),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(ui.error!!, color = QaColors.Danger)
                    Spacer(Modifier.height(QaSpacing.md))
                    Button(onClick = { viewModel.loadBinding() }) { Text("重试") }
                }
                inDetail -> JobDetailContent(ui, viewModel)
                else -> MainContent(ui, viewModel)
            }
        }
    }
}

// ─────────────────────── 主页：绑定 + Job 列表 ───────────────────────
@Composable
private fun MainContent(ui: JenkinsUiState, vm: JenkinsViewModel) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(QaSpacing.lg),
        verticalArrangement = Arrangement.spacedBy(QaSpacing.md),
    ) {
        item("binding") { BindingCard(ui, vm) }
        if (ui.bound) {
            item("jobsHeader") {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    if (ui.views.size > 1) {
                        LabeledDropdown(
                            label = "视图",
                            selectedText = ui.selectedView.ifBlank { "全部" },
                            options = ui.views.mapIndexed { i, name -> i to name },
                            onSelect = { vm.selectView(ui.views[it]) },
                            modifier = Modifier.weight(1f),
                        )
                    } else {
                        Text(
                            "Job 列表" + (ui.selectedView.takeIf { it.isNotBlank() }?.let { " · $it" } ?: ""),
                            style = MaterialTheme.typography.titleMedium,
                            color = QaColors.TextStrong,
                            modifier = Modifier.weight(1f),
                        )
                    }
                    IconButton(onClick = { vm.loadJobs() }) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "刷新", tint = QaColors.Primary)
                    }
                }
            }
            when {
                ui.jobsLoading -> item("jobsLoading") {
                    Box(Modifier.fillMaxWidth().padding(QaSpacing.xl), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator()
                    }
                }
                ui.jobsError != null -> item("jobsError") {
                    Text(ui.jobsError!!, color = QaColors.Danger, modifier = Modifier.padding(QaSpacing.md))
                }
                ui.jobs.isEmpty() -> item("jobsEmpty") {
                    Text("该视图下暂无 Job", color = QaColors.TextMuted, modifier = Modifier.padding(QaSpacing.md))
                }
                else -> items(ui.jobs, key = { it.name }) { job ->
                    JobRow(job.name, job.color, onClick = { vm.openJob(job.name) })
                }
            }
        }
    }
}

@Composable
private fun BindingCard(ui: JenkinsUiState, vm: JenkinsViewModel) {
    val binding = ui.binding
    var expanded by remember(ui.bound) { mutableStateOf(!ui.bound) }
    var editing by remember(ui.bound) { mutableStateOf(false) }

    // 已绑定且未展开：紧凑单行条，点击展开管理。
    if (binding != null && !expanded) {
        Row(
            Modifier.fillMaxWidth()
                .background(QaColors.Card, RoundedCornerShape(QaRadius.md))
                .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.md))
                .clickable { expanded = true; editing = false }
                .padding(horizontal = QaSpacing.md, vertical = QaSpacing.sm),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(Icons.Outlined.Tune, contentDescription = null, tint = QaColors.Primary, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(QaSpacing.xs))
            Text(
                "Jenkins · ${binding.account}",
                style = MaterialTheme.typography.labelMedium,
                color = QaColors.TextDefault,
                modifier = Modifier.weight(1f),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            StatusPill(if (binding.verified) "已验证" else "待验证", if (binding.verified) QaColors.Success else QaColors.Warning)
            Icon(Icons.Outlined.ChevronRight, contentDescription = "展开", tint = QaColors.TextMuted, modifier = Modifier.size(18.dp))
        }
        return
    }

    Column(
        Modifier.fillMaxWidth()
            .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Jenkins 账号绑定", style = MaterialTheme.typography.titleMedium, color = QaColors.TextStrong, modifier = Modifier.weight(1f))
            if (binding != null) {
                TextButton(onClick = { expanded = false; editing = false }) { Text("收起") }
            } else {
                StatusPill("未绑定", QaColors.TextMuted)
            }
        }

        if (binding != null && !editing) {
            Spacer(Modifier.height(QaSpacing.sm))
            Text("账号：${binding.account}", style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault)
            Text(binding.baseUrl, style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
            val err = binding.lastErrorMessage
            if (err != null && !binding.verified) {
                Text(err, style = MaterialTheme.typography.labelSmall, color = QaColors.Danger)
            }
            Spacer(Modifier.height(QaSpacing.sm))
            Row(horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                OutlinedButton(onClick = { editing = true }) { Text("重新配置") }
                OutlinedButton(
                    onClick = { vm.deleteBinding() },
                    colors = androidx.compose.material3.ButtonDefaults.outlinedButtonColors(contentColor = QaColors.Danger),
                ) { Text("删除绑定") }
            }
        } else {
            BindingForm(ui, vm, onDone = { editing = false; expanded = false })
            if (binding != null) {
                TextButton(onClick = { editing = false }) { Text("取消") }
            }
        }
    }
}

@Composable
private fun BindingForm(ui: JenkinsUiState, vm: JenkinsViewModel, onDone: () -> Unit) {
    var baseUrl by remember(ui.binding, ui.defaultBaseUrl) { mutableStateOf(ui.binding?.baseUrl ?: ui.defaultBaseUrl) }
    var account by remember(ui.binding) { mutableStateOf(ui.binding?.account ?: "") }
    var token by remember(ui.binding) { mutableStateOf("") }
    val canSubmit = baseUrl.isNotBlank() && account.isNotBlank() && token.isNotBlank() && !ui.busy

    Spacer(Modifier.height(QaSpacing.sm))
    OutlinedTextField(value = baseUrl, onValueChange = { baseUrl = it }, label = { Text("Jenkins 地址") }, singleLine = true, modifier = Modifier.fillMaxWidth())
    Spacer(Modifier.height(QaSpacing.sm))
    OutlinedTextField(value = account, onValueChange = { account = it }, label = { Text("账号") }, singleLine = true, modifier = Modifier.fillMaxWidth())
    Spacer(Modifier.height(QaSpacing.sm))
    OutlinedTextField(
        value = token,
        onValueChange = { token = it },
        label = { Text("API Token") },
        singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        modifier = Modifier.fillMaxWidth(),
    )
    Text("Token 仅用于服务端加密存储，绑定后无需重复输入。", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted, modifier = Modifier.padding(top = QaSpacing.xxs))
    Spacer(Modifier.height(QaSpacing.sm))
    Row(horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm), verticalAlignment = Alignment.CenterVertically) {
        OutlinedButton(enabled = canSubmit, onClick = { vm.testBinding(baseUrl, account, token) }) { Text("测试") }
        Button(enabled = canSubmit, onClick = { vm.saveBinding(baseUrl, account, token); onDone() }) { Text("保存") }
        if (ui.busy) {
            Spacer(Modifier.width(QaSpacing.xs))
            CircularProgressIndicator(Modifier.height(20.dp).width(20.dp), strokeWidth = 2.dp)
        }
    }
}

@Composable
private fun JobRow(name: String, color: String?, onClick: () -> Unit) {
    val (label, c) = jobStatus(color)
    Row(
        Modifier.fillMaxWidth()
            .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .clickable(onClick = onClick)
            .padding(QaSpacing.lg),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(name, style = MaterialTheme.typography.bodyLarge, color = QaColors.TextStrong, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        Spacer(Modifier.width(QaSpacing.sm))
        StatusPill(label, c)
    }
}

// ─────────────────────── Job 详情 ───────────────────────
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun JobDetailContent(ui: JenkinsUiState, vm: JenkinsViewModel) {
    val d: JenkinsJobDetail? = ui.detail
    if (ui.detailLoading || d == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(QaSpacing.lg),
        verticalArrangement = Arrangement.spacedBy(QaSpacing.md),
    ) {
        item("head") {
            Column(
                Modifier.fillMaxWidth()
                    .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
                    .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
                    .padding(QaSpacing.lg),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    val (label, c) = jobStatus(d.color)
                    StatusPill(label, c)
                    Spacer(Modifier.width(QaSpacing.sm))
                    if (d.healthScore != null) {
                        Text("健康度 ${d.healthScore}%", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
                    }
                }
                val desc = d.description
                if (!desc.isNullOrBlank()) {
                    Spacer(Modifier.height(QaSpacing.xs))
                    Text(desc, style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault)
                }
                if (d.params.isNotEmpty()) {
                    Spacer(Modifier.height(QaSpacing.sm))
                    Text("参数（${d.params.size}）", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs), verticalArrangement = Arrangement.spacedBy(QaSpacing.xs)) {
                        d.params.forEach { p -> StatusPill(p, QaColors.Accent) }
                    }
                    Text("移动端按 Jenkins 默认参数触发；如需自定义级联参数请用电脑端。", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted, modifier = Modifier.padding(top = QaSpacing.xxs))
                }
                Spacer(Modifier.height(QaSpacing.sm))
                if (d.buildable) {
                    Button(enabled = !ui.busy, onClick = { vm.triggerBuild(d.name) }) {
                        Icon(Icons.Outlined.PlayArrow, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(QaSpacing.xs))
                        Text(if (ui.busy) "处理中…" else "触发构建")
                    }
                } else {
                    Text("该 Job 当前不可构建", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
                }
            }
        }
        item("buildsTitle") {
            Text("构建历史", style = MaterialTheme.typography.titleMedium, color = QaColors.TextStrong)
        }
        if (d.builds.isEmpty()) {
            item("noBuilds") { Text("暂无构建记录", color = QaColors.TextMuted, modifier = Modifier.padding(QaSpacing.sm)) }
        } else {
            items(d.builds, key = { it.number }) { b ->
                BuildRow(
                    build = b,
                    artifacts = ui.artifacts[b.number],
                    loading = ui.artifactsLoadingFor == b.number,
                    onToggleArtifacts = { vm.toggleArtifacts(d.name, b.number) },
                    onDownload = { path, file -> vm.download(d.name, b.number, path, file) },
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BuildRow(
    build: JenkinsBuild,
    artifacts: List<site.geonest.qa.core.domain.model.JenkinsArtifact>?,
    loading: Boolean,
    onToggleArtifacts: () -> Unit,
    onDownload: (path: String, fileName: String) -> Unit,
) {
    Column(
        Modifier.fillMaxWidth()
            .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("#${build.number}", style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold), color = QaColors.TextStrong)
            Spacer(Modifier.width(QaSpacing.sm))
            val (label, c) = buildStatus(build.result, build.building)
            StatusPill(label, c)
            Spacer(Modifier.weight(1f))
            OutlinedButton(onClick = onToggleArtifacts, contentPadding = PaddingValues(horizontal = QaSpacing.md, vertical = QaSpacing.xxs)) {
                Text(if (artifacts != null) "收起产物" else "产物", style = MaterialTheme.typography.labelMedium)
            }
        }
        Spacer(Modifier.height(QaSpacing.xxs))
        Text("${fmtTime(build.timestamp)} · ${fmtDuration(build.durationMs)}", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)

        if (loading) {
            Spacer(Modifier.height(QaSpacing.sm))
            CircularProgressIndicator(Modifier.height(20.dp).width(20.dp), strokeWidth = 2.dp)
        }
        if (artifacts != null) {
            Spacer(Modifier.height(QaSpacing.sm))
            if (artifacts.isEmpty()) {
                Text("该构建无归档产物", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
            } else {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs), verticalArrangement = Arrangement.spacedBy(QaSpacing.xs)) {
                    artifacts.forEach { a ->
                        DownloadChip(a.fileName) { onDownload(a.relativePath, a.fileName) }
                    }
                    DownloadChip("全部(zip)", strong = true) { onDownload("*zip*/archive.zip", "archive.zip") }
                }
            }
        }
    }
}

@Composable
private fun DownloadChip(text: String, strong: Boolean = false, onClick: () -> Unit) {
    val color = if (strong) QaColors.TextDefault else QaColors.Accent
    Row(
        Modifier
            .background(if (strong) QaColors.SurfaceSubtle else QaColors.AccentContainer, RoundedCornerShape(QaRadius.md))
            .border(1.dp, color.copy(alpha = 0.4f), RoundedCornerShape(QaRadius.md))
            .clickable(onClick = onClick)
            .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xs),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Outlined.Download, contentDescription = null, tint = color, modifier = Modifier.size(15.dp))
        Text(text, style = MaterialTheme.typography.labelSmall, color = color, modifier = Modifier.padding(start = QaSpacing.xxs))
    }
}

@Composable
private fun StatusPill(text: String, color: Color) {
    Box(
        Modifier
            .background(color.copy(alpha = 0.12f), RoundedCornerShape(QaRadius.pill))
            .border(1.dp, color.copy(alpha = 0.35f), RoundedCornerShape(QaRadius.pill))
            .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.SemiBold), color = color)
    }
}

// ─────────────────────── 工具 ───────────────────────
private fun jobStatus(color: String?): Pair<String, Color> {
    val c = color.orEmpty()
    return when {
        c.contains("anime") -> "运行中" to QaColors.Warning
        c.startsWith("blue") || c.startsWith("green") -> "成功" to QaColors.Success
        c.startsWith("red") -> "失败" to QaColors.Danger
        c.startsWith("yellow") -> "不稳定" to QaColors.Warning
        c.startsWith("aborted") -> "已中止" to QaColors.TextMuted
        c.contains("disabled") || c.contains("notbuilt") -> "未构建" to QaColors.TextDisabled
        else -> "未知" to QaColors.TextMuted
    }
}

private fun buildStatus(result: String?, building: Boolean): Pair<String, Color> {
    if (building) return "运行中" to QaColors.Warning
    return when (result?.uppercase()) {
        "SUCCESS" -> "成功" to QaColors.Success
        "FAILURE" -> "失败" to QaColors.Danger
        "UNSTABLE" -> "不稳定" to QaColors.Warning
        "ABORTED" -> "已中止" to QaColors.TextMuted
        else -> (result ?: "—") to QaColors.TextMuted
    }
}

private val timeFmt = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())

private fun fmtTime(ts: Long): String = if (ts <= 0) "—" else timeFmt.format(Date(ts))

private fun fmtDuration(ms: Long): String {
    if (ms <= 0) return "—"
    val totalSec = ms / 1000
    val m = totalSec / 60
    val s = totalSec % 60
    return if (m > 0) "${m}分${s}秒" else "${s}秒"
}
