# 测量软件测试平台

测量软件测试平台是一个基于 `FastAPI + SQLAlchemy + SQLite + 原生 HTML/CSS/JS` 的轻量测试管理平台。  
系统覆盖需求分配、工作台执行、复测闭环、Stage5 总盘、Bug 特派、报表分析和企业微信推送。

## 技术栈

- 后端：FastAPI、SQLAlchemy、Pydantic、SQLite
- 前端：原生 HTML/CSS/JS、ECharts、html2pdf、SheetJS
- 鉴权：JWT + 单会话互踢
- 集成：企业微信 Webhook、APScheduler

## 主要功能

- 登录鉴权、单设备登录互踢
- 用户管理（角色切换、密码修改、管理员重置密码）
- 大版本 / 小版本管理
- 需求创建、Excel/CSV 批量导入、任务分配发布
- 任务进行状态：按需求展开关联 Bug、复测问题与任务，支持搜索、负责人及进度筛选
- 我的工作台、复测工作台
- 禅道研发需求变更待确认标签、需求版本对比及本人确认
- 用例维护、执行记录、Bug 录入与追踪
- Stage5 整体测试闭环看板
- Bug 特派与专项验证
- 报表中心图表和 PDF 导出
- 企业微信消息推送、每日播报

禅道任务的“研发需求变更”独立于测试完成状态显示。需求工作台的橙色标签在卡片收起时仍可点击，打开最新版需求、验收标准及旧版对比；只有任务当前指派人可以使用本人已绑定的禅道账号确认。确认不自动重开测试或重置工时，需要重新测试时另行重新激活任务。多人任务的确认请在禅道中按成员处理。

更新后重启后端会自动补齐任务变更缓存字段，首次进入版本或待处理工作台时同步禅道状态；前端资源版本已更新。真实任务的确认由用户阅读需求后点击完成。

任务分配台的“任务进行状态”支持多个需求同时展开，明细按需加载、分别分页。关联任务包含当前版本的禅道测试子任务、同需求的其他禅道任务，以及明确关联该需求的本地看板任务；未分配需求单独分组。

“复测问题”包含再次激活、测后归集及历史复测来源记录，取消的复测结论保留追溯，有效问题沿用复测工作台判定规则。复测问题可能与关联 Bug 重叠，数量不能相加；禅道状态与平台闭环状态分别展示。“刷新进度”读取本地最新摘要，“刷新详情”会尝试同步禅道 Bug、用例及当前版本任务；同步失败保留缓存并提示，数据新旧以各条目的同步时间为准。本次升级无需数据库结构迁移。

## 目录结构

```text
APPAuto/
├─ app/
│  ├─ main.py
│  ├─ core/
│  ├─ db/
│  ├─ models/
│  ├─ api/
│  ├─ services/
│  ├─ integrations/
│  └─ utils/
├─ frontend/
│  ├─ index.html
│  ├─ login.html
│  ├─ css/
│  │  └─ main.css
│  └─ js/
│     ├─ app.js
│     ├─ api.js
│     ├─ auth.js
│     ├─ state.js
│     ├─ utils.js
│     ├─ components/
│     └─ tabs/
├─ tests/
├─ scripts/
├─ .env.example
├─ .gitignore
└─ requirements.txt
```

## 本地启动

### 1. 安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，至少配置以下项：

- `APP_SECRET_KEY`
- `APP_DATABASE_URL`
- `APP_ENV`
- `APP_WECOM_WEBHOOK_URL`（如需企业微信推送）

### 3. 初始化数据库

```bash
python -m app.init_db
```

### 4. 启动服务

```bash
uvicorn app.main:app --reload
```

访问地址：

- 首页：`http://127.0.0.1:8000/`
- 登录页：`http://127.0.0.1:8000/login`

## 默认账号

开发环境首次初始化会自动创建默认管理员：

- 用户名：`admin`
- 密码：`admin`

建议首次登录后立即修改密码。

## 测试

```bash
pytest
```

## Windows 部署

支持 Windows Server + NSSM 部署，详见 `DEPLOY_WINDOWS.md`。

## 架构现状

- 后端已进入温和重构阶段：路由层保持接口兼容，核心业务逐步收口到 service 层。
- 前端已完成第一轮模块化：`mine / retest / stage5 / report / assign / data / dispatch` 均已拆分到独立模块。
- `frontend/index.html` 当前保留兼容桥接函数，便于平滑迁移。

## 后续建议

1. 继续压薄 `frontend/index.html`，只保留页面结构与初始化入口。
2. 增加 service 层自动化测试覆盖并接入 CI。
3. 提供审计日志查询接口与后台页面。
4. 为未来 Alembic 迁移预留数据库升级路径。

