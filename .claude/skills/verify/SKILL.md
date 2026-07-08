---
name: verify
description: 在隔离环境中启动 OmniQA 并用 Playwright 驱动前端验证改动(不碰真实 app_auto.db 和真实禅道)
---

# OmniQA 运行验证配方

## 隔离启动(勿用真实库/真实禅道)

```bash
export APP_ENV=dev APP_DATABASE_URL='sqlite:///./.tmp/verify_ui.db' PYTHONPATH=/e/APPAUTO/APPAuto
rm -f .tmp/verify_ui.db && .venv/Scripts/python -m app.init_db   # 自动种 admin/admin
.venv/Scripts/python -m uvicorn app.main:app --port 8123          # run_in_background
```

无禅道绑定时,禅道联动操作返回 502「找不到可用的禅道账号绑定」——正好用来驱动错误路径,不会碰真实禅道数据。

## 种数据要点(用 app.models 写脚本,同一套 env)

- `SoftwareProduct` 先建;`Version(MAJOR)` 必须挂 `software_id`,需求工作台还需一个 `Version(MINOR, parent_id=major)` 才能勾「测试完成」(弹窗要选小版本)。
- 需求工作台卡片:`Requirement(owner_id=admin, major_version_id, zentao_task_id, zentao_task_status_cache='wait', zentao_task_assigned_to='admin')`,admin 要设 `zentao_account='admin'`。
- 任务工作台操作按钮(开始/完成/关闭):任务须**不关联需求**才显示(关联本人需求只显示「跳转」),种 `ZentaoTaskMirror(task_id, execution_id=1, status='wait', assigned_to='admin', assignee_user_id=1)`。

## 驱动(Playwright MCP)

- 登录 admin/admin → 顶栏软件选择器按 `localStorage.setItem('currentSoftwareId', '<id>')` + reload 切换。
- 「我的工作台」→ 子页签:需求工作台/复测工作台/测试工作台/任务工作台(`switchWorkbenchSubtab`)。
- 需求工作台要手动 `mineMajorSelect.value=<id>` 后 `window.loadMyWorkbench()`。
- confirm 弹窗用 `window.confirm = () => true` 覆盖。
- 验证加载条/toast 时序:MutationObserver 盯 `#omniqaLoadingOverlay` 显隐 + `#omniqaLoadingMsg` 文案 + `#toastWrap .toast`,记录 performance.now() 时间线。

## 约定

- 禅道同步类按钮的时序约定:加载条挂到工作台/任务列表**重载完成**才消失,toast 与界面更新同帧;中途用 `setLoadingText()` 切换阶段文案(common.js)。
