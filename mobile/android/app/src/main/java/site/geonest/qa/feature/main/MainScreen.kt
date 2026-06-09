package site.geonest.qa.feature.main

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Assignment
import androidx.compose.material.icons.outlined.Dashboard
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Replay
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import kotlinx.coroutines.launch
import site.geonest.qa.feature.profile.ProfileScreen
import site.geonest.qa.feature.retest.RetestScreen
import site.geonest.qa.feature.workbench.WorkbenchScreen

private enum class MainTab(val label: String, val icon: ImageVector) {
    Workbench("工作台", Icons.Outlined.Dashboard),
    Retest("复测", Icons.Outlined.Replay),
    TaskBoard("任务", Icons.Outlined.Assignment),
    Profile("我的", Icons.Outlined.Person),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen() {
    var selected by remember { mutableIntStateOf(0) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val tabs = MainTab.entries

    Scaffold(
        topBar = { TopAppBar(title = { Text(tabs[selected].label) }) },
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            NavigationBar {
                tabs.forEachIndexed { index, tab ->
                    NavigationBarItem(
                        selected = selected == index,
                        onClick = { selected = index },
                        icon = { Icon(tab.icon, contentDescription = tab.label) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when (tabs[selected]) {
                MainTab.Workbench -> WorkbenchScreen(
                    onMessage = { msg -> scope.launch { snackbar.showMessage(msg) } },
                )
                MainTab.Retest -> RetestScreen(
                    onMessage = { msg -> scope.launch { snackbar.showMessage(msg) } },
                )
                MainTab.TaskBoard -> Placeholder("任务看板开发中")
                MainTab.Profile -> ProfileScreen()
            }
        }
    }
}

@Composable
private fun Placeholder(text: String) {
    Box(Modifier.fillMaxSize()) {
        Text(
            text,
            modifier = Modifier.align(androidx.compose.ui.Alignment.Center),
            color = site.geonest.qa.core.designsystem.QaColors.TextMuted,
        )
    }
}

private suspend fun SnackbarHostState.showMessage(message: String) {
    currentSnackbarData?.dismiss()
    showSnackbar(message)
}
