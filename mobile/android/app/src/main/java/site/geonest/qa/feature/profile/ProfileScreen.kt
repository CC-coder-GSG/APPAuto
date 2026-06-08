package site.geonest.qa.feature.profile

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaSpacing

@Composable
fun ProfileScreen(
    onLoggedOut: () -> Unit,
    viewModel: ProfileViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Column(
        modifier = Modifier.fillMaxSize().padding(QaSpacing.xl),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        when {
            state.loading -> CircularProgressIndicator()

            state.error != null -> {
                Text(state.error!!, color = QaColors.Danger)
                Spacer(Modifier.height(QaSpacing.lg))
                Button(onClick = viewModel::refresh) { Text("重试") }
            }

            else -> {
                val user = state.user
                Text(
                    user?.displayName ?: "—",
                    style = MaterialTheme.typography.titleLarge,
                    color = QaColors.TextStrong,
                )
                Spacer(Modifier.height(QaSpacing.sm))
                Text(
                    "用户名：${user?.username ?: "—"}　|　角色：${user?.role ?: "—"}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = QaColors.TextMuted,
                )
                Spacer(Modifier.height(QaSpacing.xxl))
                OutlinedButton(
                    onClick = {
                        viewModel.logout()
                        onLoggedOut()
                    },
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                ) { Text("退出登录") }
            }
        }
    }
}
