package site.geonest.qa.feature.main

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AssignmentInd
import androidx.compose.material.icons.outlined.BugReport
import androidx.compose.material.icons.outlined.Dashboard
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Replay
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.feature.assign.AssignScreen
import site.geonest.qa.feature.cad.CadScreen
import site.geonest.qa.feature.datamanage.DataManageScreen
import site.geonest.qa.feature.jenkins.JenkinsScreen
import site.geonest.qa.feature.overalltest.OverallTestScreen
import site.geonest.qa.feature.report.ReportScreen
import site.geonest.qa.feature.preview.LocalPreviewOpen
import site.geonest.qa.feature.preview.PreviewHost
import site.geonest.qa.feature.preview.PreviewViewModel
import site.geonest.qa.feature.profile.ProfileScreen
import site.geonest.qa.feature.profile.ProfileViewModel
import site.geonest.qa.feature.retest.RetestScreen
import site.geonest.qa.feature.workbench.WorkbenchScreen

/** “我的”页里全屏打开的子功能（覆盖在主框架之上）。 */
private enum class ProfileOverlay { CAD, REPORT, DATA, JENKINS }

private enum class MainTab(val label: String, val icon: ImageVector) {
    Workbench("工作台", Icons.Outlined.Dashboard),
    Retest("复测", Icons.Outlined.Replay),
    OverallTest("测试", Icons.Outlined.BugReport),
    Assign("分配", Icons.Outlined.AssignmentInd),
    Profile("我的", Icons.Outlined.Person),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen() {
    var selected by remember { mutableIntStateOf(0) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val previewViewModel: PreviewViewModel = hiltViewModel()
    // 复用 ProfileViewModel（同一 ViewModelStoreOwner）拿 allowed_tabs，按权限显示「分配」。
    val profileViewModel: ProfileViewModel = hiltViewModel()
    val profile by profileViewModel.state.collectAsStateWithLifecycle()
    val showAssign = profile.user?.allowedTabs?.contains("assign") == true
    val tabs = MainTab.entries.filter { it != MainTab.Assign || showAssign }
    val current = tabs[selected.coerceIn(0, tabs.lastIndex)]
    var overlay by remember { mutableStateOf<ProfileOverlay?>(null) }

    CompositionLocalProvider(LocalPreviewOpen provides previewViewModel::open) {
        if (overlay != null) {
            when (overlay) {
                ProfileOverlay.CAD -> CadScreen(onBack = { overlay = null })
                ProfileOverlay.REPORT -> ReportScreen(onBack = { overlay = null })
                ProfileOverlay.DATA -> DataManageScreen(onBack = { overlay = null })
                ProfileOverlay.JENKINS -> JenkinsScreen(onBack = { overlay = null })
                null -> Unit
            }
        } else {
        Scaffold(
            containerColor = QaColors.Background,
            topBar = {
                TopAppBar(
                    title = { Text(current.label) },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = QaColors.Background,
                        titleContentColor = QaColors.TextStrong,
                    ),
                )
            },
            snackbarHost = { SnackbarHost(snackbar) },
            bottomBar = {
                NavigationBar(
                    containerColor = QaColors.Card,
                    tonalElevation = 8.dp,
                ) {
                    tabs.forEachIndexed { index, tab ->
                        NavigationBarItem(
                            selected = current == tab,
                            onClick = { selected = index },
                            icon = { Icon(tab.icon, contentDescription = tab.label) },
                            label = { Text(tab.label) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = QaColors.Primary,
                                selectedTextColor = QaColors.Primary,
                                indicatorColor = QaColors.PrimaryContainer,
                                unselectedIconColor = QaColors.TextMuted,
                                unselectedTextColor = QaColors.TextMuted,
                            ),
                        )
                    }
                }
            },
        ) { padding ->
            Box(Modifier.fillMaxSize().padding(padding)) {
                when (current) {
                    MainTab.Workbench -> WorkbenchScreen(
                        onMessage = { msg -> scope.launch { snackbar.showMessage(msg) } },
                    )
                    MainTab.Retest -> RetestScreen(
                        onMessage = { msg -> scope.launch { snackbar.showMessage(msg) } },
                    )
                    MainTab.OverallTest -> OverallTestScreen(
                        onMessage = { msg -> scope.launch { snackbar.showMessage(msg) } },
                    )
                    MainTab.Assign -> AssignScreen(
                        onMessage = { msg -> scope.launch { snackbar.showMessage(msg) } },
                    )
                    MainTab.Profile -> ProfileScreen(
                        onOpenCad = { overlay = ProfileOverlay.CAD },
                        onOpenReport = { overlay = ProfileOverlay.REPORT },
                        onOpenData = { overlay = ProfileOverlay.DATA },
                        onOpenJenkins = { overlay = ProfileOverlay.JENKINS },
                    )
                }
            }
        }
        }
        // 全屏预览弹窗（监听同一个 PreviewViewModel）。
        PreviewHost(previewViewModel)
    }
}

private suspend fun SnackbarHostState.showMessage(message: String) {
    currentSnackbarData?.dismiss()
    showSnackbar(message)
}
