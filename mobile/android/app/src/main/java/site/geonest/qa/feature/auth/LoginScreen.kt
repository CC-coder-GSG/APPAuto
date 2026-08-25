package site.geonest.qa.feature.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import site.geonest.qa.core.designsystem.QaColors
import site.geonest.qa.core.designsystem.QaGlassPanel
import site.geonest.qa.core.designsystem.QaPill
import site.geonest.qa.core.designsystem.QaScreenBackground
import site.geonest.qa.core.designsystem.QaSpacing

@Composable
fun LoginScreen(
    viewModel: LoginViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    QaScreenBackground(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = QaSpacing.xl),
    ) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            QaPill("APPAuto QA", color = QaColors.Accent, container = QaColors.AccentContainer)
            Spacer(Modifier.height(QaSpacing.md))
            Text(
                text = "测量软件测试平台",
                style = androidx.compose.material3.MaterialTheme.typography.headlineMedium,
                color = QaColors.TextStrong,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(QaSpacing.xs))
            Text(
                text = "移动测试管理系统",
                style = androidx.compose.material3.MaterialTheme.typography.bodyMedium,
                color = QaColors.TextMuted,
            )
            Spacer(Modifier.height(QaSpacing.xxl))

            QaGlassPanel(Modifier.fillMaxWidth()) {
                TextField(
                    value = state.username,
                    onValueChange = viewModel::onUsernameChange,
                    label = { Text("用户名") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                )
                Spacer(Modifier.height(QaSpacing.md))
                TextField(
                    value = state.password,
                    onValueChange = viewModel::onPasswordChange,
                    label = { Text("密码") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Password,
                        imeAction = ImeAction.Done,
                    ),
                    keyboardActions = KeyboardActions(onDone = { viewModel.login() }),
                )

                if (state.error != null) {
                    Spacer(Modifier.height(QaSpacing.md))
                    Text(
                        text = state.error!!,
                        color = QaColors.Danger,
                        style = androidx.compose.material3.MaterialTheme.typography.bodyMedium,
                    )
                }

                Spacer(Modifier.height(QaSpacing.xl))
                Button(
                    onClick = viewModel::login,
                    enabled = state.canSubmit,
                    modifier = Modifier.fillMaxWidth().height(50.dp),
                ) {
                    if (state.loading) {
                        CircularProgressIndicator(
                            modifier = Modifier.height(20.dp),
                            strokeWidth = 2.dp,
                            color = QaColors.Card,
                        )
                    } else {
                        Text("登录")
                    }
                }
            }
        }
    }
}
