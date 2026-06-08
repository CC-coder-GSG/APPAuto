package site.geonest.qa.navigation

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.StateFlow
import site.geonest.qa.core.data.TokenStore
import site.geonest.qa.feature.auth.LoginScreen
import site.geonest.qa.feature.main.MainScreen
import javax.inject.Inject

object Routes {
    const val LOGIN = "login"
    const val MAIN = "main"
}

@HiltViewModel
class AppViewModel @Inject constructor(tokenStore: TokenStore) : ViewModel() {
    val token: StateFlow<String?> = tokenStore.token
}

@Composable
fun AppNavigation(appViewModel: AppViewModel = hiltViewModel()) {
    val navController = rememberNavController()
    val token by appViewModel.token.collectAsStateWithLifecycle()
    val loggedIn = !token.isNullOrBlank()
    val start = if (loggedIn) Routes.MAIN else Routes.LOGIN

    // 令牌驱动导航：登录成功(token 出现) → 主界面；登出 / 401 会话失效(token 清空) → 登录页。
    // 这样 401 后无需用户手动操作即可回到登录页，避免"重试"反复失败卡死。
    LaunchedEffect(loggedIn) {
        val target = if (loggedIn) Routes.MAIN else Routes.LOGIN
        val current = navController.currentDestination?.route
        if (current != null && current != target) {
            navController.navigate(target) {
                popUpTo(navController.graph.id) { inclusive = true }
            }
        }
    }

    NavHost(navController = navController, startDestination = start) {
        composable(Routes.LOGIN) { LoginScreen() }
        composable(Routes.MAIN) { MainScreen() }
    }
}
