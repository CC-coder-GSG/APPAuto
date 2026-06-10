package site.geonest.qa.feature.profile

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Assessment
import androidx.compose.material.icons.outlined.Build
import androidx.compose.material.icons.outlined.GridView
import androidx.compose.material.icons.outlined.Logout
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Storage
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
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
import site.geonest.qa.core.designsystem.QaPanel
import site.geonest.qa.core.designsystem.QaPill
import site.geonest.qa.core.designsystem.QaScreenBackground
import site.geonest.qa.core.designsystem.QaSpacing

@Composable
fun ProfileScreen(
    onOpenCad: () -> Unit = {},
    onOpenReport: () -> Unit = {},
    onOpenData: () -> Unit = {},
    onOpenJenkins: () -> Unit = {},
    viewModel: ProfileViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    QaScreenBackground(Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.fillMaxSize().padding(QaSpacing.lg),
            verticalArrangement = Arrangement.spacedBy(QaSpacing.lg),
        ) {
            when {
                state.loading -> Box(Modifier.fillMaxSize()) {
                    CircularProgressIndicator(Modifier.align(Alignment.Center))
                }

                state.error != null -> Box(Modifier.fillMaxSize()) {
                    Column(
                        Modifier.align(Alignment.Center),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text(state.error!!, color = QaColors.Danger)
                        Spacer(Modifier.height(QaSpacing.lg))
                        Button(onClick = viewModel::refresh) { Text("重试") }
                    }
                }

                else -> {
                    val user = state.user
                    QaPanel(Modifier.fillMaxWidth()) {
                        Box(
                            Modifier
                                .size(56.dp)
                                .background(QaColors.PrimaryContainer, CircleShape),
                            contentAlignment = Alignment.Center,
                        ) {
                            Icon(
                                Icons.Outlined.Person,
                                contentDescription = null,
                                tint = QaColors.Primary,
                                modifier = Modifier.size(30.dp),
                            )
                        }
                        Spacer(Modifier.height(QaSpacing.md))
                        Text(
                            user?.displayName ?: "—",
                            style = MaterialTheme.typography.titleLarge,
                            color = QaColors.TextStrong,
                        )
                        Spacer(Modifier.height(QaSpacing.xs))
                        Text(
                            "用户名：${user?.username ?: "—"}",
                            style = MaterialTheme.typography.bodyMedium,
                            color = QaColors.TextMuted,
                        )
                        Spacer(Modifier.height(QaSpacing.sm))
                        QaPill(text = "角色：${user?.role ?: "—"}")
                    }

                    val tabs = user?.allowedTabs ?: emptyList()
                    val isAdmin = user?.isAdmin == true
                    Button(
                        onClick = onOpenCad,
                        modifier = Modifier.fillMaxWidth().height(50.dp),
                    ) {
                        Icon(Icons.Outlined.GridView, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.size(QaSpacing.sm))
                        Text("CAD 测试统计")
                    }
                    OutlinedButton(
                        onClick = onOpenJenkins,
                        modifier = Modifier.fillMaxWidth().height(50.dp),
                    ) {
                        Icon(Icons.Outlined.Build, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.size(QaSpacing.sm))
                        Text("Jenkins 构建 / 下载")
                    }
                    if (isAdmin || "report" in tabs) {
                        OutlinedButton(
                            onClick = onOpenReport,
                            modifier = Modifier.fillMaxWidth().height(50.dp),
                        ) {
                            Icon(Icons.Outlined.Assessment, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.size(QaSpacing.sm))
                            Text("报表中心")
                        }
                    }
                    if (isAdmin || "data" in tabs) {
                        OutlinedButton(
                            onClick = onOpenData,
                            modifier = Modifier.fillMaxWidth().height(50.dp),
                        ) {
                            Icon(Icons.Outlined.Storage, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.size(QaSpacing.sm))
                            Text("数据管理台")
                        }
                    }
                    OutlinedButton(
                        onClick = { viewModel.logout() },
                        modifier = Modifier.fillMaxWidth().height(50.dp),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = QaColors.Danger),
                    ) {
                        Icon(Icons.Outlined.Logout, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.size(QaSpacing.sm))
                        Text("退出登录")
                    }
                }
            }
        }
    }
}
