package site.geonest.qa.navigation

import androidx.compose.runtime.Composable
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
    val start = if (!token.isNullOrBlank()) Routes.MAIN else Routes.LOGIN

    NavHost(navController = navController, startDestination = start) {
        composable(Routes.LOGIN) {
            LoginScreen(
                onLoggedIn = {
                    navController.navigate(Routes.MAIN) {
                        popUpTo(Routes.LOGIN) { inclusive = true }
                    }
                },
            )
        }
        composable(Routes.MAIN) {
            MainScreen(
                onLoggedOut = {
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(Routes.MAIN) { inclusive = true }
                    }
                },
            )
        }
    }
}
