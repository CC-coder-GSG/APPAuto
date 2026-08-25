# 测量软件测试平台 · 移动端

原生移动客户端 monorepo。Android 先行，iOS 紧随（含 iOS 26 Liquid Glass）。

## 目录结构

```
mobile/
├── android/          # 原生 Android 工程（Kotlin + Jetpack Compose）
├── ios/              # 原生 iOS 工程（SwiftUI，后续启动）
└── shared-design/    # 两端共用的设计令牌（颜色/字阶/间距/圆角…）
```

## 架构原则

- **逻辑与 UI 解耦**：Android 内部按 `core/{network,data,domain,designsystem}` + `feature/*` 分层，
  `core/data` 与 `core/domain` 编写时即满足"可抽取为 Kotlin Multiplatform（KMP）共享模块"的标准，
  待 iOS 启动时提升为 `shared/` 给 SwiftUI 复用。
- **设计统一**：`shared-design/design-tokens.json` 为唯一设计真源，两端各自映射到 Compose Theme / SwiftUI。
  信息架构与流程两端一致；iOS 仅在材质层叠加 Liquid Glass。
- **视觉语言**：`shared-design/VISUAL_LANGUAGE.md` 描述移动端审美、组件规则与 iOS Liquid Glass 映射，
  后续让 AI 生成页面时优先引用该文件和 `design-tokens.json`。

## 后端对接

- 基址：`https://qa.geonest.site`（可在 `core/network/ApiConfig` 切换 dev/prod）
- 鉴权：OAuth2 密码模式 `POST /auth/token` → JWT；`GET /auth/me` 取用户与权限
- 实时：SSE `GET /api/sse/stream?last_event_id=N`（前台）；FCM（后台，待后端 M0 配合）

> ⚠️ 后端当前为"单会话"，移动端与桌面端会互踢。"双端同时在线"需后端 M0 多会话改造，
> 详见 `docs/mobile/2026-06-08-Android端开发计划.md`。登录/基础功能不受影响，可先行开发。

## Android 开发起步

1. 用 Android Studio（建议 Ladybug+）打开 `mobile/android/`。
2. 首次同步会自动下载 Gradle Wrapper / 依赖。若命令行构建，先执行 `gradle wrapper` 生成 wrapper。
3. JDK 17、Android SDK Platform 35。
4. Run 'app' 即可看到登录页，登录后进入主页壳。
