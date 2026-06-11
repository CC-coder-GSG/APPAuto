# 终端远程查看/操控 — 操作 · 调试 · 部署手册

> 面向：在服务器上启用并维护「终端控制台」的人。
> 配套设计见 [terminal-remote-control-design.md](./terminal-remote-control-design.md)。
> 适用版本：commit `f320907` 之后（Phase 1+2 已落地）。

---

## 0. 这套东西由哪几块组成

```
你的浏览器 / 手机App
      │ ①看画面：iframe → ws-scrcpy 播放器
      │ ②控制面：HTTPS → OmniQA /api/terminals/*
      ▼
   OmniQA 后端 (FastAPI)  ──③ adb devices 扫描
      │                   ──④ 设备状态/锁（SQLite）
      ▼
   ws-scrcpy (旁路服务) ──⑤ adb→scrcpy-server→ 安卓真机(USB)
      ▲
      └──⑥ Jenkins 测试开始/结束 → OmniQA lock/unlock
```

要让它真正能用，**三样东西必须就位**：
1. 服务器能 `adb devices` 看到真机；
2. 服务器上跑着 **ws-scrcpy**（负责画面与操作）；
3. OmniQA 配好环境变量并重启。

---

## 1. 服务器上要做什么

### 1.1 adb 与设备

```bash
# 确认 adb 可用、能看到设备（state 必须是 device，不是 offline/unauthorized）
adb version
adb devices -l
# 期望输出类似：
# List of devices attached
# 1234abcd   device  product:xxx model:Pixel_6 transport_id:3
```

- 设备首次连接要在手机上**允许 USB 调试授权**（弹窗打勾「一律允许」）。出现 `unauthorized` 就是没授权。
- 多台设备建议用**带独立供电的 USB Hub**，否则供电不足会频繁掉线变 `offline`。
- `adb` 必须和 OmniQA、ws-scrcpy 用**同一个 adb server**（同一台机、同一用户）。不要同时跑多个不同版本的 adb，否则会互相 `kill-server`。

### 1.2 部署 ws-scrcpy

ws-scrcpy 是开源项目（github: NetrisTV/ws-scrcpy）。在服务器上：

```bash
git clone https://github.com/NetrisTV/ws-scrcpy.git
cd ws-scrcpy
npm install
npm start            # 默认监听 0.0.0.0:8000
```

- 验证：浏览器开 `http://<服务器IP>:8000`，应能看到设备列表并能点开实时画面。
  **这一步能单独跑通，是后面一切的前提。** 跑不通先解决 ws-scrcpy 本身。
- 生产建议用进程守护（pm2 / systemd / nssm(Windows)）常驻，并放在内网。

### 1.3 让 ws-scrcpy 常驻（示例）

- Linux systemd：写一个 service 跑 `npm start`，`Restart=always`。
- Windows：用 [nssm](https://nssm.cc/) 把 `node` + ws-scrcpy 注册成服务，或 `pm2-windows-service`。

---

## 2. OmniQA 后端配置

在 OmniQA 的 `.env`（或环境变量）里加：

```ini
# 必填：总开关
APP_TERMINAL_CONTROL_ENABLED=true

# 看画面：ws-scrcpy 播放器对外地址（浏览器能直接访问的）
APP_TERMINAL_PLAYER_URL=http://192.168.2.229:8000

# 自动化排他：Jenkins 回调用的共享密钥（自己生成一串随机值）
APP_DEVICE_SYNC_API_KEY=请改成一串随机字符串

# 可选
APP_ADB_PATH=adb                       # adb 不在 PATH 时填绝对路径
APP_WS_SCRCPY_URL=ws://127.0.0.1:8000  # 后端 WS 代理上游（原生客户端/自定义播放器用，iframe 方式可不填）
APP_TERMINAL_MANUAL_LOCK_TTL_SECONDS=300
APP_TERMINAL_AUTOMATION_LOCK_TTL_SECONDS=7200
APP_TERMINAL_STREAM_TICKET_TTL_SECONDS=60
```

然后：

```bash
# 新表会在启动时自动建（init_db / create_all），无需手动迁移
python -m app.init_db          # 或直接重启服务
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**给用户授权**：管理员默认有 `terminal` 标签权限。普通用户需在「数据管理台 →
用户标签权限」里勾上「终端控制台」。

---

## 3. 操作步骤（日常使用）

1. 登录 OmniQA → 顶部点 **「终端控制台」**。
2. 点 **「🔄 扫描设备」** —— 从服务器 adb 拉取设备列表（在线显示「空闲」）。
3. 对某台设备：
   - **👁 观看**：只读看画面（自动化运行中也能看，不冲突）。
   - **🖐 操作**：先取得操作权（设备转「操作中」），再进入可操作画面。
   - **释放**：用完点释放，设备回「空闲」。
   - **⚡ 抢占**（管理员）：设备正跑自动化时，强行中断并接管（会打断测试，二次确认）。
4. 状态徽标含义：
   - 🟢 空闲 = 可取操作权；🟡 自动化中 = 仅可观看；🔵 操作中 = 有人在操作；⚪ 离线。

---

## 4. Jenkins 集成（让自动化与人工不打架）

在自动化测试的 Jenkinsfile 里，**测试前 lock、测试后 unlock**：

```groovy
environment {
  OMNIQA = 'http://<omniqa-host>:8000'
  DKEY   = credentials('omniqa-device-key')   // = APP_DEVICE_SYNC_API_KEY
}
stages {
  stage('Reserve device') {
    steps {
      sh """curl -s -X POST $OMNIQA/api/integrations/devices/lock \
            -H 'X-Device-Sync-Key: $DKEY' -H 'Content-Type: application/json' \
            -d '{"serial":"$UDID","jenkins_job":"$JOB_NAME","jenkins_build":"$BUILD_NUMBER"}' """
    }
  }
  // ... robot / appium 测试 ...
}
post {
  always {
    sh """curl -s -X POST $OMNIQA/api/integrations/devices/unlock \
          -H 'X-Device-Sync-Key: $DKEY' -H 'Content-Type: application/json' \
          -d '{"serial":"$UDID","jenkins_build":"$BUILD_NUMBER"}' """
  }
}
```

- `$UDID` = `adb devices` 里的 serial。
- lock 成功后该设备在 OmniQA 变「自动化中」，人工只能观看；unlock 后回「空闲」。
- 即使忘了 unlock，也有 `APP_TERMINAL_AUTOMATION_LOCK_TTL_SECONDS`（默认 2h）兜底自动释放。

---

## 5. 调试手册（按层排查）

排查顺序：**adb 层 → ws-scrcpy 层 → OmniQA 接口层 → 锁/状态层 → 前端层**。
哪一层先断，就先修哪一层。

### 5.1 adb 层

| 症状 | 检查 | 处理 |
|---|---|---|
| 扫描扫不到设备 | `adb devices -l` 在 OmniQA 所在机器上是否有 `device` | USB 线/口、手机授权、`adb kill-server && adb start-server` |
| 设备显示 `unauthorized` | 手机是否弹了调试授权 | 手机上勾「一律允许」 |
| 设备时有时无 / `offline` | 供电、线材 | 换带供电 Hub、换数据线 |

### 5.2 ws-scrcpy 层

| 症状 | 检查 | 处理 |
|---|---|---|
| 画面打不开/黑屏 | 直接开 `http://<服务器>:8000` 能不能看 | 先把 ws-scrcpy 单独跑通 |
| OmniQA 里点观看是空白 | `APP_TERMINAL_PLAYER_URL` 是否配且浏览器能访问 | 配成浏览器可达的地址（不是 127.0.0.1，除非同机） |
| iframe 报跨域/拒绝嵌入 | ws-scrcpy 是否设了 `X-Frame-Options`/CSP | 反代时去掉禁嵌头，或同源反代到 `/scrcpy` |

### 5.3 OmniQA 接口层（用 curl 直接打）

先拿 token（dev 环境 admin/admin）：

```bash
OMNIQA=http://127.0.0.1:8000
TOKEN=$(curl -s -X POST $OMNIQA/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 列设备
curl -s $OMNIQA/api/terminals -H "Authorization: Bearer $TOKEN"
# 触发扫描
curl -s -X POST $OMNIQA/api/terminals/discover -H "Authorization: Bearer $TOKEN"
# 取操作权（设备 id=1）
curl -s -X POST $OMNIQA/api/terminals/1/control/acquire -H "Authorization: Bearer $TOKEN"
# 领票据
curl -s -X POST $OMNIQA/api/terminals/1/stream-ticket -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"mode":"view"}'
# 释放
curl -s -X POST $OMNIQA/api/terminals/1/control/release -H "Authorization: Bearer $TOKEN"
```

> 字段名按实际登录返回调整（若不是 `access_token`）。Windows 用 PowerShell 的
> `Invoke-RestMethod` 等价替换。

| 症状 | 含义 | 处理 |
|---|---|---|
| 所有终端接口 403「未启用」 | `APP_TERMINAL_CONTROL_ENABLED` 没开 | 设 true 并重启 |
| 403「无权限访问该页面」 | 用户没有 `terminal` 标签权限 | 管理员授权 |
| acquire 返回 409 `automation` | 设备在跑自动化 | 只能观看，或管理员抢占 |
| acquire 返回 409 `busy` | 别人在操作 | 等对方释放 |
| acquire 返回 409 `offline` | 设备离线 | 回 5.1 排查 adb |

### 5.4 Jenkins 回调层

```bash
curl -s -X POST $OMNIQA/api/integrations/devices/lock \
  -H "X-Device-Sync-Key: <你的key>" -H 'Content-Type: application/json' \
  -d '{"serial":"1234abcd","jenkins_job":"s40311","jenkins_build":"6"}'
```

| 症状 | 处理 |
|---|---|
| 401「未配置设备同步 API Key」 | 后端没设 `APP_DEVICE_SYNC_API_KEY` |
| 401「X-Device-Sync-Key 无效」 | Jenkins 传的 key 与后端不一致 |
| lock 返回 409 `manual` | 该设备正被人工操作，流水线应等待/跳过 |

### 5.5 锁/状态层（卡住了怎么办）

- 设备一直「操作中」释放不掉：等心跳 TTL（默认 5 分钟）自动释放；或管理员调
  `POST /api/terminals/{id}/control/force-release`。
- 设备一直「自动化中」：Jenkins 漏调 unlock → 等自动化 TTL（默认 2h）兜底，
  或手动 `curl .../unlock`，或管理员抢占。
- 想看锁的真实情况：查 SQLite `terminal_device_locks` 里 `released_at IS NULL` 的行。

```sql
-- 当前所有活动锁
SELECT d.serial, d.status, l.lock_type, l.holder_user_id, l.jenkins_build, l.acquired_at
FROM terminal_device_locks l JOIN terminal_devices d ON d.id=l.device_id
WHERE l.released_at IS NULL;
```

### 5.6 前端层

- 「终端控制台」入口不显示：用户无 `terminal` 权限，或 `terminal.js` 没加载
  （Ctrl+F5 强刷；看控制台 `[OmniQA] tab module load failed`）。
- 状态不实时刷新：SSE 断开（看右上角实时连接徽标）；本页只在可见时随
  `device.status_changed`/`device.lock_changed` 刷新。

### 5.7 看后端日志

```bash
# 关注这些关键词
#  - "adb devices" 相关 warning（扫描失败）
#  - "terminal_ws upstream error"（WS 代理连 ws-scrcpy 失败）
#  - "被用户 ... 抢占"（抢占审计）
```

---

## 6. 验证清单（上线前过一遍）

- [ ] 服务器 `adb devices -l` 有 `device`
- [ ] 浏览器直开 ws-scrcpy 能看画面
- [ ] OmniQA `.env` 三个必填项已配，已重启
- [ ] 「终端控制台」能扫到设备、状态正确
- [ ] 空闲设备：取操作权 → 看到可操作画面 → 释放
- [ ] Jenkins lock 后该设备变「自动化中」、人工只能观看；unlock 后回「空闲」
- [ ] 管理员能抢占（确认会中断测试）

---

## 7. 安全与运维注意

- `APP_DEVICE_SYNC_API_KEY` 当机密管理（Jenkins 用 credentials 注入）。
- ws-scrcpy / adb 端口**只在内网**开放，不要暴露公网；对外只走 OmniQA（带 JWT）。
- 抢占限管理员；操作有 SSE 广播，必要时可在活动日志侧追溯（Phase 3 会落库审计）。
- 多设备常连配供电 Hub；定期 `adb devices` 健康检查，掉线设备会显示「离线」。

---

## 8. 已知边界

- 浏览器画面渲染当前由 ws-scrcpy 播放器（iframe）承担；后端自带的 WS 票据代理
  （`/api/terminals/{id}/ws`）为原生 App/自定义播放器预留，启用前需按你部署的
  ws-scrcpy 版本对齐上游 WS 路径（见 `app/services/device_stream_service.py` 与
  `app/api/routes/terminals.py` 的 `?action=stream&udid=` 段）。
- 仅支持安卓终端（scrcpy）。iOS 被测设备镜像需另一套链路，暂不在范围内。
