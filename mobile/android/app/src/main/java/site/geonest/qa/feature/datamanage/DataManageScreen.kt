package site.geonest.qa.feature.datamanage

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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import site.geonest.qa.core.data.WorkbenchContextState
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaRadius
import site.geonest.qa.core.designsystem.QaSpacing
import site.geonest.qa.core.domain.model.AdminReqItem
import site.geonest.qa.core.domain.model.AdminUser
import site.geonest.qa.core.domain.model.DataOverview
import site.geonest.qa.core.domain.model.TAB_PERMISSION_OPTIONS
import site.geonest.qa.feature.common.LabeledDropdown

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun DataManageScreen(
    onBack: () -> Unit,
    viewModel: DataManageViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val ctx by viewModel.ctx.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    var showNewUser by remember { mutableStateOf(false) }
    var permUser by remember { mutableStateOf<AdminUser?>(null) }
    var renameUser by remember { mutableStateOf<AdminUser?>(null) }
    var deleteUser by remember { mutableStateOf<AdminUser?>(null) }
    var reqDialog by remember { mutableStateOf<AdminReqItem?>(null) } // 非空=编辑
    var showNewReq by remember { mutableStateOf(false) }
    var deleteReq by remember { mutableStateOf<AdminReqItem?>(null) }

    LaunchedEffect(ui.toast) {
        ui.toast?.let {
            snackbar.currentSnackbarData?.dismiss()
            scope.launch { snackbar.showSnackbar(it) }
            viewModel.consumeToast()
        }
    }

    Scaffold(
        containerColor = QaColors.Background,
        topBar = {
            TopAppBar(
                title = { Text("数据管理台") },
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
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            FlowRow(
                Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg, vertical = QaSpacing.xs),
                horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm),
            ) {
                DataTab.entries.forEach { t ->
                    FilterChip(selected = ui.tab == t, onClick = { viewModel.setTab(t) }, label = { Text(t.label) })
                }
            }

            Box(Modifier.fillMaxWidth().weight(1f)) {
                when {
                    ui.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                    ui.error != null -> Column(
                        Modifier.align(Alignment.Center).padding(QaSpacing.xl),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text(ui.error!!, color = QaColors.Danger)
                        Spacer(Modifier.padding(QaSpacing.sm))
                        FilterChip(selected = false, onClick = { viewModel.load() }, label = { Text("重试") })
                    }
                    else -> when (ui.tab) {
                        DataTab.OVERVIEW -> OverviewSection(ui.overview)
                        DataTab.USERS -> UsersSection(
                            users = ui.overview.users,
                            onNewUser = { showNewUser = true },
                            onToggleRole = { u -> viewModel.setRole(u.id, if (u.isAdmin) "user" else "admin") },
                            onToggleTeam = { u -> viewModel.setTeamMember(u.id, !u.isTeamMember) },
                            onPerm = { permUser = it },
                            onRename = { renameUser = it },
                            onDelete = { deleteUser = it },
                        )
                        DataTab.REQUIREMENTS -> ReqsSection(
                            reqs = ui.overview.requirements,
                            ctx = ctx,
                            onNewReq = { showNewReq = true },
                            onEdit = { reqDialog = it },
                            onDelete = { deleteReq = it },
                        )
                    }
                }
            }
        }
    }

    // ── 弹窗 ──
    if (showNewUser) {
        NewUserDialog(
            onConfirm = { u, p, role -> viewModel.createUser(u, p, role); showNewUser = false },
            onDismiss = { showNewUser = false },
        )
    }
    permUser?.let { u ->
        PermissionDialog(
            user = u,
            onSave = { tabs -> viewModel.setTabPermissions(u.id, tabs); permUser = null },
            onDismiss = { permUser = null },
        )
    }
    renameUser?.let { u ->
        RenameDialog(
            user = u,
            onSave = { name -> viewModel.setDisplayName(u.id, name); renameUser = null },
            onDismiss = { renameUser = null },
        )
    }
    deleteUser?.let { u ->
        ConfirmDialog(
            title = "删除用户",
            message = "确认删除用户「${u.displayName}」？该操作不可恢复。",
            onConfirm = { viewModel.deleteUser(u.id); deleteUser = null },
            onDismiss = { deleteUser = null },
        )
    }
    if (showNewReq) {
        ReqDialog(
            existing = null,
            ctx = ctx,
            onConfirm = { zid, title, major -> viewModel.createRequirement(zid, title, major); showNewReq = false },
            onDismiss = { showNewReq = false },
        )
    }
    reqDialog?.let { r ->
        ReqDialog(
            existing = r,
            ctx = ctx,
            onConfirm = { zid, title, major -> viewModel.updateRequirement(r.id, zid, title, major); reqDialog = null },
            onDismiss = { reqDialog = null },
        )
    }
    deleteReq?.let { r ->
        ConfirmDialog(
            title = "删除需求",
            message = "确认删除需求「${r.zentaoReqId} ${r.title}」？",
            onConfirm = { viewModel.deleteRequirement(r.id); deleteReq = null },
            onDismiss = { deleteReq = null },
        )
    }
}

// ─────────────────────────── 总览 ───────────────────────────
@Composable
private fun OverviewSection(o: DataOverview) {
    Column(
        Modifier.fillMaxSize().padding(QaSpacing.lg),
        verticalArrangement = Arrangement.spacedBy(QaSpacing.sm),
    ) {
        val cells = listOf(
            Triple("用户", o.userCount, QaColors.Primary),
            Triple("版本", o.versionCount, QaColors.Accent),
            Triple("需求", o.requirementCount, QaColors.Primary),
            Triple("Bug", o.bugCount, QaColors.Warning),
        )
        cells.chunked(2).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                row.forEach { (label, value, color) ->
                    MetricCard(label, value.toString(), color, Modifier.weight(1f))
                }
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

// ─────────────────────────── 用户管理 ───────────────────────────
@Composable
private fun UsersSection(
    users: List<AdminUser>,
    onNewUser: () -> Unit,
    onToggleRole: (AdminUser) -> Unit,
    onToggleTeam: (AdminUser) -> Unit,
    onPerm: (AdminUser) -> Unit,
    onRename: (AdminUser) -> Unit,
    onDelete: (AdminUser) -> Unit,
) {
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg, vertical = QaSpacing.xs),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("共 ${users.size} 个用户", style = MaterialTheme.typography.labelMedium, color = QaColors.TextMuted)
            Spacer(Modifier.weight(1f))
            Button(onClick = onNewUser) { Text("+ 新建用户") }
        }
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(QaSpacing.lg),
            verticalArrangement = Arrangement.spacedBy(QaSpacing.sm),
        ) {
            items(users, key = { it.id }) { u -> UserCard(u, onToggleRole, onToggleTeam, onPerm, onRename, onDelete) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun UserCard(
    u: AdminUser,
    onToggleRole: (AdminUser) -> Unit,
    onToggleTeam: (AdminUser) -> Unit,
    onPerm: (AdminUser) -> Unit,
    onRename: (AdminUser) -> Unit,
    onDelete: (AdminUser) -> Unit,
) {
    Column(
        Modifier.fillMaxWidth()
            .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
            .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
            .padding(QaSpacing.lg),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(u.displayName, style = MaterialTheme.typography.titleSmall, color = QaColors.TextStrong)
            Spacer(Modifier.width(QaSpacing.sm))
            Text("@${u.username}", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted)
        }
        Spacer(Modifier.padding(QaSpacing.xxs))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs), verticalArrangement = Arrangement.spacedBy(QaSpacing.xxs)) {
            Tag(if (u.isAdmin) "管理员" else "普通用户", if (u.isAdmin) QaColors.Primary else QaColors.TextMuted)
            if (u.isTeamMember) Tag("团队成员", QaColors.Success)
            Tag("页面权限 ${u.allowedTabs.size}", QaColors.Accent)
        }
        Spacer(Modifier.padding(QaSpacing.xxs))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(QaSpacing.xs)) {
            ActionChip(if (u.isAdmin) "设为普通" else "设为管理员") { onToggleRole(u) }
            ActionChip(if (u.isTeamMember) "移出团队" else "加入团队") { onToggleTeam(u) }
            ActionChip("页面权限") { onPerm(u) }
            ActionChip("改名") { onRename(u) }
            ActionChip("删除", QaColors.Danger) { onDelete(u) }
        }
    }
}

// ─────────────────────────── 需求管理 ───────────────────────────
@Composable
private fun ReqsSection(
    reqs: List<AdminReqItem>,
    ctx: WorkbenchContextState,
    onNewReq: () -> Unit,
    onEdit: (AdminReqItem) -> Unit,
    onDelete: (AdminReqItem) -> Unit,
) {
    val majorLabel = remember(ctx.versions) { ctx.majors.associate { it.id to it.versionNo } }
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = QaSpacing.lg, vertical = QaSpacing.xs),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("共 ${reqs.size} 个需求", style = MaterialTheme.typography.labelMedium, color = QaColors.TextMuted)
            Spacer(Modifier.weight(1f))
            Button(onClick = onNewReq) { Text("+ 新建需求") }
        }
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(QaSpacing.lg),
            verticalArrangement = Arrangement.spacedBy(QaSpacing.sm),
        ) {
            items(reqs, key = { it.id }) { r ->
                Column(
                    Modifier.fillMaxWidth()
                        .border(1.dp, QaColors.Border, RoundedCornerShape(QaRadius.lg))
                        .background(QaColors.Card, RoundedCornerShape(QaRadius.lg))
                        .padding(QaSpacing.lg),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (r.zentaoReqId.isNotBlank()) { Tag(r.zentaoReqId, QaColors.OnPrimaryContainer, QaColors.PrimaryContainer); Spacer(Modifier.width(QaSpacing.sm)) }
                        Text(r.title, style = MaterialTheme.typography.bodyLarge, color = QaColors.TextStrong, modifier = Modifier.weight(1f))
                    }
                    Spacer(Modifier.padding(QaSpacing.xxs))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        val verLabel = r.majorVersionId?.let { majorLabel[it] } ?: "—"
                        Text("版本：$verLabel", style = MaterialTheme.typography.labelSmall, color = QaColors.TextMuted, modifier = Modifier.weight(1f))
                        ActionChip("编辑") { onEdit(r) }
                        Spacer(Modifier.width(QaSpacing.xs))
                        ActionChip("删除", QaColors.Danger) { onDelete(r) }
                    }
                }
            }
        }
    }
}

// ─────────────────────────── 弹窗 ───────────────────────────
@Composable
private fun NewUserDialog(onConfirm: (String, String, String) -> Unit, onDismiss: () -> Unit) {
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var admin by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("新建用户") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                OutlinedTextField(value = username, onValueChange = { username = it }, label = { Text("用户名（≥3 位）") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = password, onValueChange = { password = it }, label = { Text("密码（≥3 位）") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = admin, onCheckedChange = { admin = it })
                    Text("管理员", style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault)
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = username.trim().length >= 3 && password.length >= 3,
                onClick = { onConfirm(username, password, if (admin) "admin" else "user") },
            ) { Text("创建") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun PermissionDialog(user: AdminUser, onSave: (List<String>) -> Unit, onDismiss: () -> Unit) {
    val selected = remember(user.id) { androidx.compose.runtime.mutableStateMapOf<String, Boolean>().apply {
        TAB_PERMISSION_OPTIONS.forEach { (key, _) -> put(key, key in user.allowedTabs) }
    } }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("页面权限 - ${user.displayName}") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                TAB_PERMISSION_OPTIONS.forEach { (key, label) ->
                    Row(
                        Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Checkbox(checked = selected[key] == true, onCheckedChange = { selected[key] = it })
                        Text(label, style = MaterialTheme.typography.bodyMedium, color = QaColors.TextDefault)
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { onSave(TAB_PERMISSION_OPTIONS.map { it.first }.filter { selected[it] == true }) }) { Text("保存") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun RenameDialog(user: AdminUser, onSave: (String) -> Unit, onDismiss: () -> Unit) {
    var name by remember(user.id) { mutableStateOf(user.displayName) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("修改显示名") },
        text = {
            OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("显示名") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        },
        confirmButton = { TextButton(enabled = name.trim().isNotEmpty(), onClick = { onSave(name) }) { Text("保存") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun ReqDialog(
    existing: AdminReqItem?,
    ctx: WorkbenchContextState,
    onConfirm: (String, String, Int) -> Unit,
    onDismiss: () -> Unit,
) {
    var zid by remember { mutableStateOf(existing?.zentaoReqId.orEmpty()) }
    var title by remember { mutableStateOf(existing?.title.orEmpty()) }
    var majorId by remember { mutableStateOf(existing?.majorVersionId ?: ctx.majorId ?: ctx.majors.firstOrNull()?.id) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (existing == null) "新建需求" else "编辑需求") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(QaSpacing.sm)) {
                OutlinedTextField(value = zid, onValueChange = { zid = it }, label = { Text("禅道需求号（如 r#123 或 123）") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = title, onValueChange = { title = it }, label = { Text("需求标题") }, modifier = Modifier.fillMaxWidth(), minLines = 2)
                LabeledDropdown(
                    label = "大版本",
                    selectedText = ctx.majors.firstOrNull { it.id == majorId }?.versionNo ?: "请选择",
                    options = ctx.majors.map { it.id to it.versionNo },
                    onSelect = { majorId = it },
                )
                if (ctx.majors.isEmpty()) {
                    Text("当前软件暂无大版本，请先在电脑端创建。", style = MaterialTheme.typography.labelSmall, color = QaColors.Danger)
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = zid.trim().isNotEmpty() && title.trim().isNotEmpty() && majorId != null,
                onClick = { majorId?.let { onConfirm(zid, title, it) } },
            ) { Text(if (existing == null) "创建" else "保存") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun ConfirmDialog(title: String, message: String, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(message) },
        confirmButton = { TextButton(onClick = onConfirm) { Text("确认", color = QaColors.Danger) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

// ─────────────────────────── 通用小组件 ───────────────────────────
@Composable
private fun Tag(text: String, color: Color, container: Color = QaColors.SurfaceSubtle) {
    Box(
        Modifier
            .background(container, RoundedCornerShape(QaRadius.sm))
            .border(1.dp, color.copy(alpha = 0.35f), RoundedCornerShape(QaRadius.sm))
            .padding(horizontal = QaSpacing.sm, vertical = QaSpacing.xxs),
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.SemiBold), color = color)
    }
}

@Composable
private fun ActionChip(text: String, color: Color = QaColors.Primary, onClick: () -> Unit) {
    Box(
        Modifier
            .heightIn(min = 32.dp)
            .background(QaColors.SurfaceSubtle, RoundedCornerShape(QaRadius.md))
            .border(1.dp, color.copy(alpha = 0.4f), RoundedCornerShape(QaRadius.md))
            .clickable(onClick = onClick)
            .padding(horizontal = QaSpacing.md, vertical = QaSpacing.xs),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, style = MaterialTheme.typography.labelMedium, color = color)
    }
}
