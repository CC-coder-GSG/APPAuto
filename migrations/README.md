# PostgreSQL 适配与离线迁移

适配分支：`feat/postgresql-adaptation`。生产服务、原始 `database/` 备份未修改。

## 已完成

- SQLite / PostgreSQL 分方言引擎，PostgreSQL 使用 psycopg 3、连接健康检查和可配置连接池。
- PostgreSQL 启动仅验证 Alembic 版本，不自动补表、重建表、播种账户或执行全库回填；SQLite 保留原启动兼容流程。
- 74 张业务表的冻结 PostgreSQL 基线；执行基线要求目标空库，禁止覆盖已有结构。基线不可降级删除业务数据。
- 当前 SQLAlchemy Enum 保留原有枚举名称及原生 PostgreSQL 枚举类型，未将值擅自改为小写；后续新增枚举需显式迁移。
- 外键默认立即校验，离线导入事务可延迟到提交前校验，支持自引用和循环关系；应用请求不会自行关闭校验。
- 最终测试按数据库方言进行原子 upsert；设备活动占用索引包含 PostgreSQL 部分条件。
- 版本名称容量从 30 扩至 128 字符；三个浏览器毫秒时间戳字段改为 BigInteger。
- 修正工作台产品名称查询的字符串/整数参数混用；保留短事务和已有连接占用日志。
- PostgreSQL 瞬态数据库错误映射为繁忙响应，不自动重放含禅道副作用的操作。
- 提供整组备份快照、只读预检、空库导入、逐表内容摘要核验和自增序列修复工具。

## 备份预检结果（2026-11-04）

`database/app_auto.db` 有配套 WAL/SHM，不能只复制主文件。已在隔离目录复制三件套，再用 SQLite backup API 生成快照；原文件 SHA-256 验证不变。

- 74 张表，31,946 条记录，完整性检查通过，模型无遗漏表/列。
- 281 条 `versions.version_no` 超过原来的 30 字符，最大 47；扩大字段后无需截断。
- 252 条毫秒时间戳超出 32 位整数范围：browser_sync_events 141 条、bug_tracking 97 条、test_cases 14 条；改为 BigInteger 后保持原值。
- 原备份有 5 条孤儿外键；用户已于 2026-11-04 确认按下列方式处理独立迁移副本：

| 表 | 记录 ID | 问题字段 | 已在迁移副本执行（原备份保留） |
| --- | --- | --- | --- |
| requirement_status_history | 71、976、979 | requirement_id 指向已不存在的需求，字段不可空 | 完整归档三行后移出迁移副本 |
| bug_tracking | 5188 | fixed_minor_version_id 指向不存在版本，可空 | 清空引用，保留记录和原引用审计 |
| bug_stage5_records | 1845 | minor_version_id 指向不存在版本，可空 | 清空引用，保留记录和原引用审计 |

已按用户决定，在独立副本上归档 3 条历史行、清空 2 条可空版本引用（保留 Bug 和阶段记录本身）。归档文件同时保留历史行、原版本引用及源文件摘要。原备份未修改。处理后 74 张表、31,943 条数据导入本地 PostgreSQL，逐表字段摘要一致。

可重复执行的修复命令（输出路径均须为新路径）：

```powershell
.venv/Scripts/python.exe -m app.db.migration_repair --source database/app_auto.db --destination .tmp/repaired-copy.db --archive .tmp/orphan-archive.json --report .tmp/repair-report.json
```

该工具只接受此次明确批准的五处引用，若新备份异常集合变化则拒绝处理。归档包含原业务数据，应按数据库备份保管，禁止提交仓库。通用导入工具仍要求预检通过。

隔离验证使用 PostgreSQL 17.11，仅监听本机临时端口。已验证结构差异为零、备份中 30 个大版本的统计/工作台/复测读取，以及最终测试启用版本的 4 位有效团队成员工作台。原快照上的生产写操作、禅道操作、定时同步均未执行。

## 运行前提

安装 `requirements.txt` 中固定依赖。目标 PostgreSQL 数据库预先创建为 UTF-8；数据库与应用服务独立管理。结构迁移和导入使用数据库所有者/迁移账号，应用运行账号单独授予必要的表、序列读写权限。

保留现有应用密钥和绑定加密密钥，否则导入后的禅道/Jenkins 凭据可能无法解密。附件目录单独备份并保持路径可访问。保持原服务器时间语义，不在此次导入中整体转换时区。

备份源必须是静态、完整的一组文件；在线生产应先通过一致性备份或停写生成备份组。迁移工具不连接生产源服务，不提供在线增量追平。

## 操作顺序（先演练，禁止直接对生产执行）

以下 PowerShell 示例假定项目根目录为当前目录；数据库连接字符串应通过安全配置注入，不能提交到 Git，也不要发给聊天工具。

1. 从备份生成新的快照，输出位置不得已存在：

```powershell
.venv/Scripts/python.exe -m app.db.migrate snapshot --source database/app_auto.db --destination .tmp/migration-snapshot.db --report .tmp/snapshot-report.json
.venv/Scripts/python.exe -m app.db.migrate preflight --source .tmp/migration-snapshot.db --report .tmp/preflight-report.json
```

预检存在问题时返回退出码 2。报告只记录结构、记录 ID、统计和摘要，不输出账户密钥或业务字段原文。原备份默认会报告上述 5 条孤儿记录。处理规则确认后，只在独立副本上进行受审计修复并重新预检。

2. 为当前维护进程配置 `APP_DATABASE_URL`，格式为 `postgresql+psycopg://用户:URL编码后的密码@主机:端口/数据库`。设置该变量只影响当前命令进程，不会自动修改 NSSM 服务环境。随后对预先创建的空库执行：

```powershell
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic check
```

3. 只对通过预检的独立快照执行：

```powershell
.venv/Scripts/python.exe -m app.db.migrate import --source .tmp/migration-snapshot.db --report .tmp/import-report.json --confirm-empty-target
```

导入要求 Alembic 版本匹配、所有业务表为空；在单个事务中加锁、分批插入、验证外键、比对内容摘要和修复序列，出错回滚数据。随后执行 ANALYZE。导入不运行 ORM 业务回调，不访问禅道或广播事件。工具不是生产数据合并工具，已有数据时拒绝执行；不支持默认断点续传。

4. 演练和受限冒烟时设置 `APP_BACKGROUND_JOBS_ENABLED=false`，禁止启动数据库对比 worker、定时禅道同步、日报推送及复测定时回填。该开关只关闭后台任务，不阻止用户主动点击外部操作，因此仍需限制演练环境的外部访问。校验完成后，才将 PostgreSQL 地址配置到 NSSM。继续保持单个应用 worker，SSE 与部分同步限流仍是进程内实现；按切换操作单恢复后台任务。

## 回归方式

SQLite 默认回归无需额外环境变量。PostgreSQL 回归需预先建立独立、空白测试数据库并执行基线，将 `APP_DATABASE_URL` 和 `APP_TEST_DATABASE_URL` 指向该库；测试库名必须以 `_test` 结尾。禁止指向业务库。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_database_adaptation.py tests/test_final_test.py tests/test_final_test_presence.py tests/test_assignment_progress.py tests/test_requirement_tasks.py tests/test_mine_query_loading.py tests/test_retest.py tests/test_retest_aggregate_transactions.py tests/test_sync_lock_service.py tests/test_db_lifecycle.py -q
```

通用 fixture 在 PostgreSQL 外层事务内使用 savepoint，测试提交后仍可整体回滚；并发用例创建独立测试记录并清理。没有设置 PostgreSQL 测试地址时，真实 PostgreSQL 专用用例会明确跳过。

## 配置及边界

- PostgreSQL 连接池起始值：`APP_DATABASE_POOL_SIZE=5`、`APP_DATABASE_MAX_OVERFLOW=5`、`APP_DATABASE_POOL_TIMEOUT=10`。按后续生产压测调整，不把大连接池当作长事务修复。
- 未迁移生产、未更改 NSSM、未启用多进程、未推送分支。
- 数据库对比模块继续使用独立 SQLite 文件，不属于主业务库替换范围。
- 五条孤儿引用已在本地迁移副本处理；正式切换仍需确认生产账号权限/Windows 版本/可用资源、密钥和文件目录、维护窗口及备份恢复演练。
- 开放 PostgreSQL 业务写入前可恢复原 SQLite；开放后不能直接切回旧备份，否则新增数据会丢失。本次不提供反向迁移工具。
- `.tmp/pg-adaptation/` 包含数据库二进制、临时密码、演练归档与报告，已忽略，禁止提交或发布。
