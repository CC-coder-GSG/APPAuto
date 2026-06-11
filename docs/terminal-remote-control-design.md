# 终端远程查看与操控 — 详细设计文档

> 在 OmniQA 测试管理系统中，远程查看并操作连接在服务器上的安卓终端（Jenkins +
> RobotFramework + Appium 自动化所用的真机），与自动化测试**互不冲突**，并支持
> 未来多终端、多屏幕，以及 Web / Android App / iOS App 多端接入。

版本：v0.1（设计稿）　最后更新：2026-06-11

---

## 0. 背景与目标

- **现状**：服务器上跑 Jenkins 流水线，调用 RobotFramework + Appium 在 USB 直连的
  安卓真机上做自动化测试。人离服务器有距离，看不到也摸不到终端屏幕。
- **目标**：
  1. 在 OmniQA 里**实时看到**每台终端的屏幕（即使自动化正在跑）。
  2. 在终端**空闲时远程操作**（点击/滑动/输入/按键）。
  3. 与 Appium 自动化**不冲突**：自动化运行时操作被排他，只允许只读观看。
  4. 支持**多终端、多屏幕**，可平滑扩展。
  5. 支持 **Web + Android App + iOS App** 三端接入。

### 核心技术判断

- 安卓远程控制用 **scrcpy**：镜像 + 操作，走 ADB，编码在设备端完成。
- 浏览器/多端交付用 **ws-scrcpy**（scrcpy 的 Web 化封装，WebSocket 推 H.264 +
  控制协议，MIT）。
- **不冲突的依据**：
  - ADB 多路复用 → scrcpy 与 Appium 可同时连同一设备（底层不冲突）。
  - 画面镜像是只读，自动化运行时也能看（**零冲突**）。
  - 唯一真冲突是 **input 注入**（人和 Appium 同时操作 UI）→ 用应用层「设备状态锁」
    做排他即可。

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│  客户端                                                       │
│  ├─ Web（OmniQA「终端」标签页）                                │
│  ├─ Android App（KMP 共享层 + MediaCodec 硬解）               │
│  └─ iOS App（KMP 共享层 + VideoToolbox 硬解）                 │
└───────────────┬──────────────────────────┬──────────────────┘
                │ ① REST（设备列表/锁/预约） │ ② WebSocket（画面+操作）
                ▼                          ▼
┌─────────────────────────────────────────────────────────────┐
│  OmniQA 后端（FastAPI）                                        │
│  ├─ /api/terminals/*        设备/锁/会话 REST                  │
│  ├─ /api/terminals/{id}/ws  WS 反向代理（套 JWT 票据）         │
│  ├─ /api/integrations/devices/*  Jenkins lock/unlock 回调     │
│  ├─ DeviceRegistryService   adb 扫描 + 状态机                  │
│  ├─ DeviceLockService       排他 / 抢占 / 超时释放            │
│  └─ SSE：device.* 事件推送（复用现有 SSE）                    │
└───────────────┬──────────────────────────┬──────────────────┘
                │ 内网代理                   │ adb
                ▼                          ▼
        ws-scrcpy（旁路服务，仅内网端口）   adb server
                                            │
                                  ┌─────────┴─────────┐
                                  ▼         ▼         ▼
                              终端#1     终端#2  …   终端#N（USB）
                                  ▲
                                  │ Appium / UiAutomator2（自动化）
                              Jenkins Pipeline
```

要点：

- **ws-scrcpy 不直接对外**，只监听内网/本机端口；浏览器/App 一律连 FastAPI 的 WS
  代理，由后端校验 JWT 票据后再转发到 ws-scrcpy。adb、scrcpy 不裸露。
- **scrcpy 编码在设备端**，服务器只转发字节 → 服务器负载随终端数近似线性、很轻。
- **设备状态机**集中在后端，是「不冲突」的唯一权威来源。

---

## 2. 设备状态机（核心）

每台设备在任一时刻处于下列状态之一：

| 状态 | 含义 | 画面镜像 | 远程操作 |
|---|---|---|---|
| `offline` | adb 不可见 / 掉线 | ✗ | ✗ |
| `idle` | 在线且无人占用 | ✓ | ✓（可申请控制权） |
| `automation` | Appium/Jenkins 自动化占用 | ✓（只读） | ✗（除非抢占） |
| `manual` | 某用户持有手动控制权 | ✓（他人只读） | 仅持有者可操作 |

状态转移：

```
            adb 扫到设备
 offline ───────────────▶ idle
    ▲                      │  │
    │ adb 掉线              │  │ 用户 acquire（仅 idle 成功）
    └──────────────────────┘  ▼
                            manual ──release/超时──▶ idle

 idle ──Jenkins lock──▶ automation ──Jenkins unlock──▶ idle
 automation ──用户 preempt(中断自动化,需权限+确认)──▶ manual
```

排他规则（DeviceLockService 强制）：

- 同一设备**同时最多一个活动锁**（`released_at IS NULL`）。
- `automation` 锁存在时，`acquire`（手动控制）返回 **409**，并提示是否允许 `preempt`。
- `manual` 锁存在时，其他用户只能只读观看；`acquire` 返回 409（占用者是谁）。
- 锁带 **心跳/TTL**：手动锁默认 5 分钟无心跳自动释放；automation 锁靠 unlock 回调，
  另设兜底超时（如 2 小时）防 Jenkins 异常退出不释放。

---

## 3. 数据模型

新增 4 张表（SQLAlchemy / SQLite，沿用现有 `app/models` 风格）。审计优先复用现有
`ActivityService`，仅在需要结构化查询时落 `terminal_control_sessions`。

### 3.1 `terminal_devices` — 终端注册表

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| serial | str(120) unique | adb serial / udid，主键级标识 |
| name | str(120) | 人类可读名（「小米12-工位A」），可编辑 |
| platform | enum(android/ios) | 预留；当前仅 android 走 scrcpy |
| status | enum(offline/idle/automation/manual) | 由状态机维护 |
| connection | enum(usb/tcp) | 连接方式 |
| model | str(120) null | 设备型号缓存 |
| os_version | str(40) null | 安卓版本缓存 |
| enabled | bool default true | 禁用后不出现在可操作列表 |
| last_seen_at | datetime null | 最近一次 adb 扫到 |
| created_at / updated_at | datetime | |

> `status` 也可不落库、由 lock 实时推导；这里冗余一份是为了列表查询快和 SSE 推送方便，
> 以 lock 表为权威、status 为投影。

### 3.2 `terminal_device_locks` — 占用/排他记录

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| device_id | int FK→terminal_devices | |
| lock_type | enum(automation/manual) | |
| holder_kind | enum(jenkins/user) | |
| holder_user_id | int FK→users null | manual 时为操作者 |
| jenkins_job | str(255) null | automation 时的 job 名 |
| jenkins_build | str(64) null | automation 时的 build 号 |
| acquired_at | datetime | |
| heartbeat_at | datetime | 心跳续约时间 |
| released_at | datetime null | NULL = 仍持有 |
| release_reason | enum(normal/timeout/preempted/forced) null | |

约束：对 `(device_id) WHERE released_at IS NULL` 建**部分唯一索引**，保证一设备只有一个
活动锁（SQLite 支持 partial index）。

### 3.3 `terminal_control_sessions` — 手动操作会话（审计/时长）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| device_id | int FK | |
| user_id | int FK | |
| lock_id | int FK→terminal_device_locks null | |
| mode | enum(view/control) | 只看 / 操作 |
| client | enum(web/android/ios) | 接入端 |
| ip | str(64) null | |
| started_at / ended_at | datetime | |

### 3.4 `terminal_stream_tickets` — WS 一次性票据

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| token | str(64) unique | 随机串，连 WS 时带 |
| device_id | int FK | |
| user_id | int FK | |
| mode | enum(view/control) | 票据授予的权限 |
| expires_at | datetime | 短期（如 60s），用完即焚 |
| used_at | datetime null | |

> 票据机制：WS 握手无法方便地带 Authorization header（尤其浏览器/原生），故用「先 REST
> 换一次性票据，再用票据连 WS」的模式，避免把长效 JWT 暴露在 URL 上。

---

## 4. API 设计

全部位于 `/api`，沿用现有 JWT（`get_current_user`）与权限（`ensure_tab_access`）。
Jenkins 回调单独用共享密钥头鉴权（仿照现有 `X-Zentao-Sync-Key`）。

### 4.1 设备查询与管理

```
GET  /api/terminals
  → [{id, serial, name, platform, status, model, os_version,
      lock:{type, holder, jenkins_build} | null, can_control: bool}]

GET  /api/terminals/{id}
  → 设备详情 + 当前活动锁 + 最近会话

POST /api/terminals/discover            (admin)   触发一次 adb 扫描，upsert 设备
POST /api/terminals                     (admin)   手动登记/命名设备 {serial, name}
PUT  /api/terminals/{id}                (admin)   改名/启用禁用 {name?, enabled?}
```

### 4.2 控制权（锁）

```
POST /api/terminals/{id}/control/acquire
  - 仅当 status=idle 成功 → 建 manual 锁，status→manual，返回 {lock_id, ticket(control)}
  - status=automation → 409 {reason:"automation", preemptable:bool, jenkins_build}
  - status=manual(他人) → 409 {reason:"busy", holder}

POST /api/terminals/{id}/control/heartbeat   续约（刷新 heartbeat_at）
POST /api/terminals/{id}/control/release      释放自己的 manual 锁 → status→idle
POST /api/terminals/{id}/control/preempt      (需特权) 中断 automation 并接管
                                              → 通知 Jenkins/Appium 侧（见 4.4），
                                                 automation 锁 release_reason=preempted，
                                                 建 manual 锁
```

### 4.3 视频/操作通道

```
POST /api/terminals/{id}/stream-ticket  body:{mode: view|control}
  - view：idle/automation/manual 都可领（只读观看人人可看）
  - control：仅当调用者持有该设备 manual 锁才发 control 票据
  → {token, ws_url, expires_at}

WS   /api/terminals/{id}/ws?ticket=<token>
  - 后端校验票据（未用、未过期、设备匹配、mode 匹配）
  - 校验通过 → 在内网与 ws-scrcpy 建立上游 WS，双向透传
  - control 模式：透传输入帧；view 模式：丢弃/拒绝任何输入帧（服务端兜底排他）
  - 断开即结束 terminal_control_sessions
```

### 4.4 Jenkins / 自动化联动（共享密钥鉴权）

```
POST /api/integrations/devices/lock     header: X-Device-Sync-Key
  body:{serial, jenkins_job, jenkins_build}
  → 建 automation 锁，status→automation；若已被 manual 占用则返回冲突由流水线决定等待/跳过

POST /api/integrations/devices/unlock   header: X-Device-Sync-Key
  body:{serial, jenkins_build}
  → 释放 automation 锁，status→idle

POST /api/integrations/devices/heartbeat（可选）  长测试续约防兜底超时误释放
```

Jenkinsfile 集成（两行级改动，示意）：

```groovy
stage('Reserve device') {
  sh """curl -s -X POST $OMNIQA/api/integrations/devices/lock \
        -H 'X-Device-Sync-Key: $KEY' -H 'Content-Type: application/json' \
        -d '{"serial":"$UDID","jenkins_job":"$JOB_NAME","jenkins_build":"$BUILD_NUMBER"}'"""
}
// ... robot/appium 测试 ...
post { always {
  sh """curl -s -X POST $OMNIQA/api/integrations/devices/unlock \
        -H 'X-Device-Sync-Key: $KEY' -H 'Content-Type: application/json' \
        -d '{"serial":"$UDID","jenkins_build":"$BUILD_NUMBER"}'"""
}}
```

### 4.5 SSE 事件（复用现有 `/api/sse`）

- `device.status_changed` → {device_id, status}
- `device.lock_acquired` / `device.lock_released` → {device_id, holder, type}

前端「终端」标签页订阅后实时刷新徽标/可操作态。

---

## 5. Phase 1 — MVP：看 + 空闲操作

**目标**：最快让人能在 OmniQA 里看屏幕、空闲时操作。先不接 Jenkins、不做与自动化的精细排他。

后端：
- 部署 **ws-scrcpy** 旁路服务（systemd/docker，仅内网端口）。
- 新增 `terminal_devices` 表 + `DeviceRegistryService`：定时/按需 `adb devices -l` 扫描，
  upsert 设备，置 `offline/idle`。
- `GET /api/terminals`、`POST /api/terminals/discover`。
- `POST /api/terminals/{id}/stream-ticket` + `WS /api/terminals/{id}/ws` 代理到 ws-scrcpy。
- 简化锁：有人开始操作即置 `manual`（仅防多人互抢），断开即回 `idle`。

前端：
- 新增 **「终端」标签页**（`frontend/js/tabs/terminal.js`），设备网格 + 实时画面卡片 +
  点开放大操作。复用现有 `api.js` / SSE / toast。

验收：测试运行中能看到画面；空闲设备能点击操作；多台设备能同时显示。

工作量：约数天。

---

## 6. Phase 2 — 与自动化排他 + Jenkins 联动（核心价值）

后端：
- 新增 `terminal_device_locks` 表 + `DeviceLockService`（状态机、部分唯一索引、TTL 心跳）。
- 完整 `acquire/release/heartbeat/preempt`，落实「automation 中只读、idle 可操作」。
- Jenkins 回调 `/api/integrations/devices/lock|unlock`（共享密钥）。
- **Appium 兜底检测**：后台轮询 Appium `GET /sessions`，按占用 udid 校正状态，防止
  Jenkinsfile 漏调；再加 `adb shell` 查 UiAutomator2 进程作为第三道保险。
- SSE 推 `device.*` 事件。

前端：
- 设备卡片显示 `空闲/自动化中/手动操作中` 徽标，自动化中禁用操作按钮、仅观看。
- 「申请操作权 / 释放 / 抢占（二次确认）」交互。

验收：测试运行中无法误操作、只能看；测试结束自动转 idle 可操作；抢占需权限且会中断/告警。

工作量：约 1~2 周。

---

## 7. Phase 3 — 打磨与规模化

- `terminal_control_sessions` 审计落库 + 接入现有活动日志（谁/何时/哪台/看或操作）。
- **操作权预约/排队**：设备忙时排队，空闲自动通知队首。
- **多观看者**：1 个操作者 + N 个只读观看（ws-scrcpy 共享流验证；必要时后端做 fan-out）。
- 设备**标签/分组**（按工位、机型、项目）、批量管理、离线告警。
- 手动锁**超时自动释放** + 前台倒计时提醒；automation 锁兜底超时清理。
- 画质/码率/分辨率档位选择（弱网降码率）。

工作量：按需迭代。

---

## 8. 移动端（Android / iOS App）接入

**可行**：交付层是 H.264 视频流 + 定义化的控制协议，全走 HTTP/WebSocket，与客户端平台无关。

两条路径：

| 路径 | 做法 | 优点 | 代价 |
|---|---|---|---|
| **WebView 内嵌** | App 里用 WebView 打开 OmniQA「终端」页 | 两端零额外开发，随 Web 升级 | iOS WKWebView 对 WebCodecs 支持弱，软解耗 CPU、延迟略高 |
| **原生客户端**（推荐体验） | App 直连 FastAPI WS 代理，自己硬解渲染、捕获手势发控制帧 | 原生流畅、低延迟、可后台保活 | 各平台解码/渲染需各写一份 |

原生路径与你现有架构的契合点：
- **鉴权、设备列表、锁状态、控制协议编解码这层放进 KMP `core/data` + `core/domain` 共享层**，
  Android/iOS 共用一套业务逻辑。
- 仅 **解码（Android MediaCodec / iOS VideoToolbox）+ 渲染（SurfaceView / Metal）+ 触摸采集**
  是平台专属 `actual` 实现。
- 控制协议建议直接复用 scrcpy 的输入帧格式（坐标按设备分辨率归一化后回传），减少自造轮子。

**边界提醒（重要）**：
- 「在 Android/iOS App 上操控**服务器上的安卓终端**」= 客户端只是网络消费者，**100% 可行**。
- scrcpy **只镜像安卓**。若将来服务器侧被测终端里出现 **iOS 真机**需要被镜像，需另接一套
  iOS 镜像链（WebDriverAgent / idb / QuickTime over usbmuxd），与本方案的设备状态机/锁可
  共用，但视频/控制通道要单独适配。本文档第 1~7 阶段聚焦安卓终端。

---

## 9. 风险与注意点

- **安卓 ROM 差异**：个别厂商 ROM 限制 input 注入；上线前用真机逐型号验证。
- **延迟**：局域网内 scrcpy 亚 100ms；浏览器优先 WebCodecs 硬解，原生端用平台硬解。
- **同设备多人观看**：scrcpy 基本一设备一流；多观看建议「1 控 N 看」由后端 fan-out 或
  ws-scrcpy 共享流实现（需实测）。
- **安全**：Web/App 能操作真机权限大 → JWT + 一次性票据 + 审计日志 + 抢占特权控制必备。
- **运维**：多终端常连需带供电 USB hub；adb server 稳定性、设备掉线重连要有自愈扫描。
- **不选 STF 的理由**：minicap 在 Android 12+ 易失效；新建议统一走 scrcpy 系。

---

## 10. 落地清单（给实现期）

后端新增：
- `app/models/terminal_device.py`、`terminal_device_lock.py`、`terminal_control_session.py`、
  `terminal_stream_ticket.py`
- `app/services/device_registry_service.py`（adb 扫描）、`device_lock_service.py`（状态机/排他）、
  `device_stream_proxy.py`（WS 代理 ws-scrcpy）
- `app/api/routes/terminals.py`（4.1~4.3）、`device_integrations.py`（4.4 Jenkins 回调）
- 配置项：`APP_WS_SCRCPY_URL`、`APP_DEVICE_SYNC_API_KEY`、`APP_ADB_PATH`、锁 TTL 等
- 权限：新增 tab `terminal` 的访问控制；抢占需管理员/特权角色

前端新增：
- `frontend/js/tabs/terminal.js` + index.html「终端」标签页 + SSE 订阅

外部：
- 服务器部署 ws-scrcpy；Jenkinsfile 加 lock/unlock 两段

---

## 11. 部署与启用（Phase 1/2 已落地）

### 11.1 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_TERMINAL_CONTROL_ENABLED` | `false` | 终端功能总开关。关闭时接口 403、前端隐藏入口 |
| `APP_ADB_PATH` | `adb` | 服务器 adb 可执行文件路径 |
| `APP_WS_SCRCPY_URL` | 空 | 内网 ws-scrcpy 的 **WebSocket** 地址，如 `ws://127.0.0.1:8000`（供后端 WS 代理上游） |
| `APP_TERMINAL_PLAYER_URL` | 空 | ws-scrcpy **Web 播放器**对外地址，如 `http://192.168.2.229:8000`（前端 iframe 内嵌画面） |
| `APP_DEVICE_SYNC_API_KEY` | 空 | Jenkins lock/unlock 回调共享密钥（`X-Device-Sync-Key`） |
| `APP_TERMINAL_MANUAL_LOCK_TTL_SECONDS` | `300` | 人工锁心跳超时（秒） |
| `APP_TERMINAL_AUTOMATION_LOCK_TTL_SECONDS` | `7200` | 自动化锁兜底超时（秒） |
| `APP_TERMINAL_STREAM_TICKET_TTL_SECONDS` | `60` | 流票据有效期（秒） |

### 11.2 启用步骤

1. 服务器装好 `adb` 并能 `adb devices` 看到真机（带供电 USB hub）。
2. 部署 ws-scrcpy（`npm i && npm start`，或 docker），监听内网端口。
3. 设置上表环境变量，至少 `APP_TERMINAL_CONTROL_ENABLED=true` +
   `APP_TERMINAL_PLAYER_URL`（看画面）+ `APP_DEVICE_SYNC_API_KEY`（Jenkins 排他）。
4. 重启 OmniQA，给相关用户在「数据管理台」勾选 `terminal` 标签权限（管理员默认有）。
5. 进「终端控制台」→「扫描设备」→ 即可观看 / 获取操作权。

### 11.3 Jenkins 集成（实现的回调）

```groovy
environment { OMNIQA = 'http://<omniqa-host>:8000'; DKEY = credentials('omniqa-device-key') }
stages {
  stage('Reserve device') {
    steps {
      sh """curl -s -X POST $OMNIQA/api/integrations/devices/lock \
            -H 'X-Device-Sync-Key: $DKEY' -H 'Content-Type: application/json' \
            -d '{"serial":"$UDID","jenkins_job":"$JOB_NAME","jenkins_build":"$BUILD_NUMBER"}' """
    }
  }
  // ... robot/appium ...
}
post { always {
  sh """curl -s -X POST $OMNIQA/api/integrations/devices/unlock \
        -H 'X-Device-Sync-Key: $DKEY' -H 'Content-Type: application/json' \
        -d '{"serial":"$UDID","jenkins_build":"$BUILD_NUMBER"}' """
}}
```

### 11.4 当前实现状态

- ✅ Phase 1：设备扫描/列表、状态机、操作权 acquire/release/heartbeat、流票据、WS 代理、前端「终端控制台」标签页（画面经 ws-scrcpy 播放器 iframe）。
- ✅ Phase 2：自动化排他（automation 锁、Jenkins lock/unlock 回调、抢占、心跳/TTL 超时释放、SSE 状态广播）。
- ⏳ Phase 3（未做）：操作会话审计落库、预约排队、多观看者 fan-out、设备分组、Appium `/sessions` 主动轮询兜底。
- 说明：浏览器内的 scrcpy 解码渲染由 ws-scrcpy 播放器承担（iframe）；后端自带的 WS 票据代理为原生客户端/自定义播放器预留，需 ws-scrcpy 协议对齐后启用。
```
