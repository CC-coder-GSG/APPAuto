**数据库对比工具：使用与维护说明**

功能入口：测试平台“实用工具 → 数据库对比”，采用与工作台一致的模块子导航。管理员默认可见；普通用户需要管理员在数据管理台分配“实用工具（数据库对比）”页面权限。已有数据库对比权限继续有效，无需重新授权。任务、输入和报告默认仅创建人及管理员可查看。

**使用流程。** 重启更新后的平台，让现有启动初始化自动创建 database_compare_inputs、database_compare_tasks 两张新表。依赖使用现有 requirements.txt，无需新增安装项。

1. 从旧、新两个软件版本分别导出完整数据库快照。先停止写入，保证 ATTRIBUTE.db、SYSTEM.db、TASK.db 来自各自一致的业务时点。浏览器仅上传单个 .db 时无法知道设备端是否还有未合并的 WAL 数据。
2. 打开“实用工具 → 数据库对比”，填写版本名称，分别多选两侧文件，点击“上传并预览”。也可以只比较一个 SQLite 文件。
3. 检查对应关系。多文件默认按不区分大小写的文件名配对；两侧各一个文件时自动配对。更名文件可调整选择、添加或移除对应关系，然后重新预览。全部文件必须各覆盖一次。
4. 检查每张表的匹配方式。自动使用可验证的声明主键；无键时按整行内容及次数比较。如果版本间自增 ID 重建，选择稳定业务键或整行比较。业务键用逗号分隔，预览/创建任务和实际执行均检查唯一性、非空及字段存在性。
5. 点击“开始比较”。任务在独立后台进程执行，可离开页面后从历史任务查看，也可请求取消。
6. 查看结构变化数、增加/减少/修改记录数、修改字段数。按表、范围、变化类型筛选，展开明细查看带类型的旧值、新值和记录定位。
7. 下载“Excel 报告包”或“完整 JSON”。Excel 报告包是 ZIP，包含数据库对比报告.xlsx 和 complete.json。长文本的页面/Excel 节选会明确标注，完整值及 BLOB 的 base64 保存在 JSON 中。
8. 历史任务支持单条删除、勾选本页批量删除，或“清理全部已结束历史”（跨所有页）。确认后永久删除任务记录、结果与导出报告，并删除不再被任何任务引用的上传快照；共享快照保留到最后一个引用任务被删除。排队中、比较中的任务需先取消并等待结束。普通用户仅能清理自己的历史，管理员可清理全部用户历史。删除当前查看的任务后会关闭结果面板，清理过的任务无法重跑。

**当前范围。** 已实现全量严格比较：普通表与 SQLite 内部表、声明类型与实际值类型、默认值、非空、主键、索引、外键、原始建表 SQL、视图/触发器定义、记录和字段值、重复次数。没有可靠键时不推断修改关系。原始 SQL 的格式差异仍可能表现为定义变化，需要人工复核。

当无键表同时增删字段，工具保存两侧完整行证据，仅比较共同字段投影，并标记部分完成。虚拟表/影子表数据、加密库或专有扩展不能被当作普通表静默略过；不支持或读取失败会明确提示。视图比较定义，数据由底层表覆盖。

首期不支持 JSON 语义等价、测量字段容差、忽略列/表、名称映射、差异备注和数据库同步。页面的文件配对解决文件名变化，不自动推断表或字段重命名。未经实现的规则由服务端拒绝，不能静默忽略。默认严格比较可发现 JSON 原文和浮点值变化。

任务执行状态与一致性结论分别展示。succeeded 表示已完成比较，不表示数据一致；partial、failed、cancelled、interrupted 均不能认定全库一致。无键但相同结构的整行计数比较可以给出内容一致性结论，同时不能确认记录身份。

**后台进程与配置。** 平台启动后自动启动无可见窗口的比较工作进程，每 15 秒检查托管进程是否退出。文件锁保证同一存储目录最多一个工作进程，首期同时执行一个任务。状态保存到平台数据库，结果保存到任务独立 SQLite 文件。

| 配置 | 默认值 | 用途 |
|---|---|---|
| APP_DATABASE_COMPARE_ROOT | uploads/database_compare | 输入、结果、导出、worker.log 和工作进程锁目录；相对项目根目录解析 |
| APP_DATABASE_COMPARE_WORKER_ENABLED | true | 平台是否自动托管工作进程 |
| APP_DATABASE_COMPARE_MAX_FILE_MB | 256 | 上传单文件上限，单位 MiB |
| APP_DATABASE_COMPARE_MAX_GROUP_MB | 512 | 上传单组总量上限，单位 MiB |
| APP_DATABASE_COMPARE_TIMEOUT_SECONDS | 1800 | 单任务比较执行时限 |

每侧最多上传 16 个文件。任务进度以表为单位，长表扫描期间仍会检查取消和超时。历史由用户手动清理，不设置自动保留期或配额；仅预览但未创建任务的上传文件不在历史清理范围内。

若使用独立 Windows 服务托管工作进程，将自动托管开关设为 false，并在相同 Python 环境、项目目录、APP_DATABASE_URL 和存储目录配置下执行：

```powershell
.venv\Scripts\python.exe -m scripts.database_compare_worker
```

需要先完成平台数据库初始化。工作进程获得独占文件锁后，将上次遗留的 running 任务标记为 interrupted；取消或中断的任务可在页面按相同输入重跑，新任务保留新的执行记录。首期不支持断点续算。

**命令行。** 比较引擎无需启动 Web 平台或数据库工作进程，可直接比较文件或目录：

```powershell
.venv\Scripts\python.exe -m scripts.compare_databases `
  --old 'E:\samples\v1' `
  --new 'E:\samples\v2' `
  --output '.codex-tmp\database-comparison-001.sqlite' `
  --export excel
```

输出路径必须是新的文件，不覆盖现有结果或输入。退出码 0 表示比较完成，结果是否有差异查看 conclusion；退出码 2 表示失败或覆盖不完整。命令行需自行使用未使用过的结果路径。

可选 --rules 指向 UTF-8 JSON 文件，当前仅支持以下格式：

```json
{
  "schema_version": 1,
  "strict_comparison": true,
  "tables": {
    "SYSTEM.db.EXTRADOMAINVALUE": {"match_mode": "row_multiset"},
    "SYSTEM.db.SYSTEMPARAM": {"match_mode": "key", "key": ["NAME"]}
  }
}
```

业务键示例仅说明语法，使用前需要确认跨版本含义稳定。规则中的数据库名称对应报告逻辑名称；单文件比较时默认使用新侧文件名，目录比较时使用文件配对名称。可用 match_mode 为 auto、key、row_multiset。

**故障定位。** 排队不动时检查 worker.log、工作进程服务状态、平台数据库连接及存储目录权限。输入变动、损坏、残留非空 WAL/回滚日志时，重新导出并上传。规则失效时按错误提示修正键，或显式选择整行比较。部分完成的表查看覆盖说明与单侧 evidence 明细。读取失败的文件不能通过删除错误信息转成“一致”结论。

**验证。** 自动化测试使用隔离目录与数据库，不依赖线上业务数据。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_database_compare_engine.py tests/test_database_compare_api.py -q -p no:cacheprovider
# 已安装 Playwright 和 Chrome 的环境：
node tests/database_compare.spec.cjs
```

浏览器测试支持通过 PLAYWRIGHT_MODULE 指向已有 Playwright 包，PYTHON 指向已有 Python；默认使用项目 .venv\Scripts\python.exe。它在 .codex-tmp 下创建专用平台数据库、输入和结果，启动本地测试服务及真实独立工作进程，退出时关闭服务。

当前模板自身对比已核对 75 张表（72 张非内部表、3 张内部表）及 2 个视图；其自身对比无差异。实际历史版本和空表的真实业务数据仍需研发/测试补充后联合验收。

2026-10-21 首期验证记录：

- 新增引擎与 API 测试 23 项通过，连同相关登录和权限回归共 39 项通过。
- 真实 Chrome 浏览器、隔离 FastAPI 服务、独立工作进程完成上传、预览、比较、筛选、Excel 下载和重跑闭环。
- 在实际三库模板副本上删除一条 EXTRADOMAINVALUE 完全重复记录，并修改一条 SURVEYSETTING.COUNTTIME；75 张表全部处理完成，准确报告减少 1 条、修改 1 条、修改字段 1 个。
- 原始模板执行前后 SHA-256 一致；模板副本验收已生成 Excel/JSON 报告包。
- 主应用可加载全部 10 个新接口，Python 编译检查及前端脚本语法检查通过。
