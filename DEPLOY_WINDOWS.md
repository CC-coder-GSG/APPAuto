# APPAuto Windows Server 部署指南

本文档用于在 Windows Server 上部署 APPAuto（FastAPI + SQLite + 纯前端）。

## 1. 前置条件

- 操作系统：Windows Server 2016/2019/2022
- 网络：可访问 Python 包源（如受限请配置企业代理）
- 建议目录：`D:\APPAuto`
- 端口：默认 `8000`（请在防火墙和安全组放行）

## 2. 安装 Python

1. 下载并安装 Python 3.10+（建议 3.11）。
2. 安装时勾选：
   - `Add python.exe to PATH`
   - `Install for all users`
3. 验证：

```bat
python --version
pip --version
```

## 3. 获取项目并创建虚拟环境

```bat
cd /d D:\
git clone <your-repo-url> APPAuto
cd APPAuto

python -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

> 若网络受限，可在内网 PyPI 镜像或离线 whl 包方式安装。

## 4. 初始化数据库与管理员账号

执行以下命令初始化 SQLite 并自动确保默认超级管理员：`admin / admin`。

```bat
.venv\Scripts\activate
python -m app.init_db
```

数据库文件默认位于项目根目录：`app_auto.db`。

## 5. 配置环境变量（建议）

建议在系统级或服务级设置：

- `APP_SECRET_KEY`：JWT 密钥（生产必须修改）
- `ACCESS_TOKEN_EXPIRE_MINUTES`：Token 过期分钟数（默认 720）
- `WECHAT_WEBHOOK_URL`：企业微信群机器人 webhook（可空）

临时设置示例：

```bat
set APP_SECRET_KEY=replace_with_strong_secret
set ACCESS_TOKEN_EXPIRE_MINUTES=720
set WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx
```

## 6. 直接运行（验证阶段）

```bat
.venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

访问：

- 登录页：`http://<server-ip>:8000/login`
- 主界面：`http://<server-ip>:8000/dashboard`
- OpenAPI：`http://<server-ip>:8000/docs`

## 7. 使用 NSSM 注册为 Windows 后台服务

## 7.1 安装 NSSM

1. 下载 NSSM：<https://nssm.cc/download>
2. 解压后将 `nssm.exe` 放入例如：`D:\tools\nssm\nssm.exe`

## 7.2 注册服务

以管理员权限打开 `cmd`，执行：

```bat
D:\tools\nssm\nssm.exe install APPAutoService
```

在弹窗中配置：

- **Application Path**：`D:\APPAuto\.venv\Scripts\python.exe`
- **Startup directory**：`D:\APPAuto`
- **Arguments**：`-m uvicorn app.main:app --host 0.0.0.0 --port 8000`

### 环境变量（推荐在 NSSM 中设置）

在 `Environment` 增加：

```text
APP_SECRET_KEY=replace_with_strong_secret
ACCESS_TOKEN_EXPIRE_MINUTES=720
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx
```

### 日志配置（推荐）

在 `I/O` 中设置：

- `Output (stdout)`：`D:\APPAuto\logs\app_stdout.log`
- `Error (stderr)`：`D:\APPAuto\logs\app_stderr.log`

先创建日志目录：

```bat
mkdir D:\APPAuto\logs
```

## 7.3 启动与设置自启动

```bat
sc start APPAutoService
sc config APPAutoService start= auto
sc query APPAutoService
```

## 7.4 更新程序后的重启

```bat
sc stop APPAutoService
cd /d D:\APPAuto
git pull
.venv\Scripts\activate
pip install -r requirements.txt
sc start APPAutoService
```

## 8. 防火墙放行 8000 端口

```bat
netsh advfirewall firewall add rule name="APPAuto 8000" dir=in action=allow protocol=TCP localport=8000
```

## 9. 常见问题排查

1. **服务启动失败**
   - 检查 `logs\app_stderr.log`
   - 确认虚拟环境路径、Python 路径是否正确
2. **无法登录 / Token 报错**
   - 确认 `APP_SECRET_KEY` 在服务环境中已设置
3. **企业微信不推送**
   - 检查 `WECHAT_WEBHOOK_URL` 是否有效
   - 确认服务器可访问企业微信域名
4. **数据库被占用**
   - SQLite 为单文件，避免多个写入进程并发启动

## 10. 生产建议

- 修改默认管理员密码（`admin/admin`）
- 使用复杂随机 `APP_SECRET_KEY`
- 定期备份 `app_auto.db`
- 通过反向代理（IIS/Nginx）启用 HTTPS
- 若并发增长明显，后续可迁移至 MySQL/PostgreSQL
