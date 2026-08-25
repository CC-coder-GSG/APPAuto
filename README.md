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
- 我的工作台、复测工作台
- 用例维护、执行记录、Bug 录入与追踪
- Stage5 整体测试闭环看板
- Bug 特派与专项验证
- 报表中心图表和 PDF 导出
- 企业微信消息推送、每日播报

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

