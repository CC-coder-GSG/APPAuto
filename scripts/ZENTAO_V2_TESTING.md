# 禅道 v2 迁移候选：测试说明

应用 REST 传输只使用 v2，不提供 v1 兜底。官方 v2 没有等价覆盖的页面 JSON、Cookie 登录、表单、工时、需求变更确认等能力继续保留。目标实例为 IPD 5.6，真实兼容性仍需测试实例验收。

2026-10-15 本地最终验证：扩展回归 624 项通过（含迁移相关 370 项），3 项 SSE 流式测试单独排除；独立应用副本启动冒烟通过。没有对真实禅道执行业务写入或部署生产。

2026-10-19 更新：升级后扩展回归 **661 项通过、3 项 SSE 排除**；日期/工时等专项 **189 项通过**，前端表单模块行为检查 **8 项通过**，真实平台日期/工时复验 **7 项通过**。经用户授权在 s4030（执行 1289）及其临时记录上完成真实读写验证。本次新增 Bug 33514、工时 209198 已清理，复用任务 20878 已恢复取消、工时为零。代码已修复必填日期、工时超时核对和最后一条删除确认；正式部署由运维流程执行。

## 部署与 Token 切换

- 前后端一起更新并重启后端，新建 Bug 需要 `deadline`（YYYY-MM-DD）；任务看板创建禅道任务需要完整的预计开始和截止日期。
- v1 Token 需要换成 v2 Token。代码会识别旧的 `last_refresh_status=ok`，在首次需要 Token 时自动调用 `/api.php/v2/users/login` 获取新 Token，并标记 `ok_v2`；无需手工编辑数据库或复制 Token。
- 可在每个人的“禅道账号绑定”中点击“刷新 Token”提前验证。自动换取要求保存的密码可解密、账号密码有效。
- 保持原来的 `APP_ZENTAO_BINDING_SECRET`；未显式配置时保持原来的 `APP_SECRET_KEY`。加密密钥变化会导致旧绑定密码无法解密，此时应核对原部署配置，或由各用户重新保存绑定密码后刷新 Token。
- 部署确认时也应检查后台同步所用账号的绑定；每个用户的 Token 独立，刷新一个人的 Token 不会刷新全部人的绑定。

最新验证与修复记录：`docs/2026-10-19-IPD5.6升级验证报告.md`、`docs/2026-10-19-日期与工时适配修复记录.md`。

## 离线回归

在仓库根目录运行：

```powershell
.venv\Scripts\python.exe scripts/verify_zentao_v2.py
```

工具使用独立 SQLite，关闭通知/禅道同步，禁止真实网络连接。报告输出到打印出的 `.codex-tmp/zentao-v2-*/results.xml`。可以继续传 pytest 参数。

扩展测试目录时，以下三个现有 SSE 流式 TestClient 测试持续等待，本次单独排除，没有算作通过：

```powershell
.venv\Scripts\python.exe scripts/verify_zentao_v2.py tests -q `
  --deselect=tests/test_sse_realtime.py::test_sse_stream_with_valid_token_reads_event `
  --deselect=tests/test_sse_realtime.py::test_sse_stream_last_event_id_only_returns_newer `
  --deselect=tests/test_sse_realtime.py::test_sse_stream_user_channel_filtering
```

## 独立应用副本

```powershell
.venv\Scripts\python.exe scripts/prepare_zentao_v2_test_app.py
.venv\Scripts\python.exe scripts/prepare_zentao_v2_test_app.py --run <上条命令输出的目录> --port 8091
```

准备时复制当前 app/frontend/requirements；不复制生产 `.env`、数据库、账号绑定和上传文件。启动副本清理继承的应用配置，使用独立数据库/上传目录，关闭禅道后台同步、企业微信、n8n、终端控制，Jenkins 默认地址为不可用占位；只监听 127.0.0.1。修复代码后重新准备副本，避免运行旧快照。

在副本中仅绑定隔离的 IPD 5.6 测试实例。应用运行在本地并不能隔离远端写入，绑定生产禅道仍会修改生产数据。

产品存在多个应用时，按实际 ID 设置：

```powershell
$env:ZENTAO_TEST_BUILD_SYSTEM_MAPPING_JSON='{"15":8,"311":20}'
```

副本启动器会转换为 `APP_ZENTAO_BUILD_SYSTEM_MAPPING_JSON`。正常部署可直接配置该 APP 变量。示例 ID 不能直接用于你们公司的数据；客户端查询产品应用列表验证映射归属，单一应用自动选择。

## 只读集成探测

明确设置 `ZENTAO_TEST_BASE_URL`、`ZENTAO_TEST_ACCOUNT`、`ZENTAO_TEST_PASSWORD`，不读取生产 `.env` 或应用绑定。密码不放命令行或清单。

```powershell
.venv\Scripts\python.exe scripts/probe_zentao_v2.py --manifest <测试对象清单.json>
```

清单格式：

```json
{
  "project_id": 1, "execution_id": 3, "product_id": 2,
  "bug_id": 101, "story_id": 102, "case_id": 103,
  "task_id": 104, "build_id": 105, "file_id": 106,
  "expected_ids": {"execution_tasks": [104, 107, 108]}
}
```

未提供对象 ID 的项目记录为 skipped；可选 expected_ids 用于精确 ID 对账。报告含 passed/failed/skipped、v2 与保留路径证据及文件校验值，不记录 Token/密码。除登录/退出会话外无业务写入，不自动确认服务器版本，不能代替写入或端到端验收。

## 验收边界

仍须在隔离 5.6 实例运行 Bug、用例、任务全生命周期与父子挂接、工时增删改和跨天结算、需求变更确认、构建改名/移动、附件、本人身份及权限回读；再验证网页/Android、真实调度和隔离 Jenkins/n8n 工作流。

已知官方资料差异：项目执行列表手册用 status，官方 MCP OpenAPI 用 browseType，候选同时发送相同过滤值；文件上传按专门手册使用 multipart file/objectType/objectID，不按 MCP 文档中的 JSON fileName 写法重传。需要在真实实例核对完整分页与上传。用例 keywords/stage、Bug module/assignedTo 在已知创建 ID 上通过保留表单按需补齐，失败仍保留 ID 和待核实警告。

出现未知写入结果时会停止该次自动重试；未实现跨重启的全局幂等事务日志，须先远端对账再手工处理。仓库外直接调用 v1 的工作流不在代码扫描范围。

详细本地记录：`docs/2026-10-15-禅道v2迁移实施与验证记录.md`；官方资料及哈希：`docs/zentao-v2-source-manifest-2026-10-15.json`。项目现有 `.gitignore` 忽略 docs，这些记录与快照默认不进入 Git。
