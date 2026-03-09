# APPAuto / OmniQA Windows Server 部署与迁移指南

本文档包含两部分：
- 新服务器部署（从 0 到可运行）
- 旧版（main 分支未重构）升级到重构版的上线迁移步骤

## 1. 适用范围
- 系统：Windows Server 2016/2019/2022
- Python：3.10+（建议 3.11）
- 服务形态：`uvicorn + NSSM`
- 数据库：SQLite（默认 `app_auto.db`）

## 2. 环境变量规范（已收口）
项目优先读取 `APP_` 前缀变量，同时兼容旧变量名。

推荐使用（新规范）：
- `APP_ENV`
- `APP_SECRET_KEY`
- `APP_DATABASE_URL`
- `APP_ACCESS_TOKEN_EXPIRE_MINUTES`
- `APP_WECOM_WEBHOOK_URL`
- `APP_SCHEDULER_TIMEZONE`

兼容读取（旧变量，不推荐长期使用）：
- `ENV`
- `SECRET_KEY`
- `DATABASE_URL`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `WECHAT_WEBHOOK_URL`
- `SCHEDULER_TIMEZONE`

注意：
- 生产环境（`APP_ENV != dev`）必须设置 `APP_SECRET_KEY`（或 `SECRET_KEY`），否则启动会报错。
- 默认管理员 `admin/admin` 只在 `dev` 环境自动种子。

## 3. 首次部署（全新服务器）

### 3.1 安装依赖
```bat
python --version
pip --version
```

### 3.2 拉取代码并安装
```bat
cd /d D:\
git clone <your-repo-url> APPAuto
cd APPAuto

python -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3.3 配置环境变量（推荐系统级）
```bat
setx APP_ENV "prod"
setx APP_SECRET_KEY "replace_with_a_very_strong_secret"
setx APP_DATABASE_URL "sqlite:///./app_auto.db"
setx APP_ACCESS_TOKEN_EXPIRE_MINUTES "720"
setx APP_WECOM_WEBHOOK_URL "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx"
setx APP_SCHEDULER_TIMEZONE "Asia/Shanghai"
```

### 3.4 初始化数据库
```bat
.venv\Scripts\activate
python -m app.init_db
```

### 3.5 本地前台验证
```bat
.venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

访问：
- 登录页：`http://<server-ip>:8000/login`
- 主界面：`http://<server-ip>:8000/dashboard`
- 文档：`http://<server-ip>:8000/docs`

## 4. 使用 NSSM 注册服务

### 4.1 安装 NSSM
下载：<https://nssm.cc/download>

### 4.2 注册服务
管理员 CMD：
```bat
D:\tools\nssm\nssm.exe install APPAutoService
```

配置：
- Application Path: `D:\APPAuto\.venv\Scripts\python.exe`
- Startup directory: `D:\APPAuto`
- Arguments: `-m uvicorn app.main:app --host 0.0.0.0 --port 8000`

### 4.3 配置服务环境变量
在 NSSM 的 `Environment` 里填入（建议与系统变量一致）：
```text
APP_ENV=prod
APP_SECRET_KEY=replace_with_a_very_strong_secret
APP_DATABASE_URL=sqlite:///./app_auto.db
APP_ACCESS_TOKEN_EXPIRE_MINUTES=720
APP_WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx
APP_SCHEDULER_TIMEZONE=Asia/Shanghai
```

### 4.4 日志配置（推荐）
NSSM `I/O`：
- stdout: `D:\APPAuto\logs\app_stdout.log`
- stderr: `D:\APPAuto\logs\app_stderr.log`

```bat
mkdir D:\APPAuto\logs
```

### 4.5 启动与自启
```bat
sc start APPAutoService
sc config APPAutoService start= auto
sc query APPAutoService
```

## 5. 旧版升级到重构版（上线迁移 SOP）
以下步骤针对“服务器当前运行旧 main 分支代码”的场景。

### 5.1 迁移前检查（必须）
1. 记录当前服务状态：
```bat
sc query APPAutoService
```
2. 备份数据库：
```bat
copy D:\APPAuto\app_auto.db D:\APPAuto\backup\app_auto_%date:~0,10%.db
```
3. 备份 `.env` / NSSM 环境变量截图。
4. 备份当前代码（可选）：
```bat
cd /d D:\APPAuto
git rev-parse HEAD > backup\before_migration_commit.txt
```

### 5.2 停服务并更新代码
```bat
sc stop APPAutoService
cd /d D:\APPAuto
git fetch --all
git checkout <重构分支或目标tag>
git pull
```

### 5.3 更新依赖
```bat
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 5.4 环境变量切换到新规范
至少确保以下变量存在：
- `APP_ENV`
- `APP_SECRET_KEY`
- `APP_DATABASE_URL`

说明：代码已兼容旧变量，但上线建议统一改为 `APP_`，减少后续维护风险。

### 5.5 执行初始化（幂等）
```bat
python -m app.init_db
```
说明：
- `create_all` 只会补齐缺失表，不会删除已有数据。
- 如历史库字段差异较大，建议先在预发环境验证后再上生产。

### 5.6 前台冒烟验证（强烈建议）
先不用 NSSM，前台启动看日志：
```bat
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
重点验证：
1. `/login` 可访问
2. admin 登录成功
3. 登录后 `/auth/me` 返回 200
4. 任务分配台可加载测试人员
5. 数据管理台可刷新版本/用户
6. 报表中心可加载图表
7. 企业微信推送（手动触发一个）

### 5.7 启动服务
```bat
sc start APPAutoService
sc query APPAutoService
```

### 5.8 回滚预案（必须准备）
如上线失败：
1. 停服务：`sc stop APPAutoService`
2. 代码回滚到旧提交：`git checkout <old_commit>`
3. 恢复数据库备份（必要时）
4. 启动旧服务：`sc start APPAutoService`

## 6. 防火墙放行
```bat
netsh advfirewall firewall add rule name="APPAuto 8000" dir=in action=allow protocol=TCP localport=8000
```

## 7. 常见问题排查
1. 服务启动失败
- 看 `logs\app_stderr.log`
- 检查虚拟环境路径、Python 路径、依赖是否安装完整

2. 登录成功后 `/auth/me` 401
- 检查 `APP_SECRET_KEY` 是否一致
- 确认没有多实例混用不同密钥

3. 推送不生效
- 检查 `APP_WECOM_WEBHOOK_URL`
- 服务器是否可访问企业微信域名

4. 数据库被占用
- 避免多个进程同时写同一个 SQLite 文件

## 8. 生产建议
- 首次登录后立即修改管理员密码
- 使用高强度随机 `APP_SECRET_KEY`
- 定期备份 `app_auto.db`
- 建议通过 IIS/Nginx 反向代理并启用 HTTPS
- 并发增大后考虑迁移到 MySQL/PostgreSQL
