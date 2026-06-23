# 企业微信 AI 机器人 API 接入文档

> 面向：在企业微信「智能机器人 → 添加 API 插件（工具/Action）」里配置，让机器人能查询测试管理系统的数据并执行轻量操作。
>
> 适用系统：OmniQA 测量软件测试平台（部署在公司内网，经内网穿透对外暴露在 `https://qa.geonest.site/`）。

---

## 1. 一句话说明

系统新开放了一组**专供机器人调用**的接口，统一前缀 `/api/bot/v1`，用**固定 API Key**鉴权（与用户登录的账号密码 / JWT 完全无关，不会过期、不受“换设备登录被踢”影响）。

- **Base URL（线上）**：`https://qa.geonest.site`
- **鉴权方式**：请求头 `X-Bot-Api-Key: <你的密钥>`
- **数据格式**：请求/响应均为 JSON，编码 UTF-8
- **接口分类**：只读查询（GET）+ 轻量写操作（POST）

---

## 2. 启用与配置（管理员，在服务器上做一次）

接口默认**关闭**（未配置密钥时所有 `/api/bot/*` 返回 `503`），需要先在服务器的 `.env` 里设置环境变量后重启服务：

```bash
# 1) 生成一个强随机密钥
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 2) 写入 .env（APP_ 前缀；下面两项）
APP_BOT_API_KEY=粘贴上一步生成的密钥
# 可选：机器人“写操作”（创建反馈/触发推送）以哪个账号身份落库，需为已存在的管理员用户名。
# 留空则自动选用任意一个管理员账号。
APP_BOT_API_USERNAME=admin

# 3) 重启服务使配置生效
```

> 安全提示：
> - `APP_BOT_API_KEY` 等同于一把“万能查询钥匙”，请妥善保管，不要写进前端、不要提交到代码仓库。
> - 该密钥泄露后，直接在 `.env` 改成新值并重启即可吊销旧密钥。
> - 建议在内网穿透/反向代理（Nginx 等）上对 `/api/bot/` 路径单独限流，并尽量只放行企业微信回调出口 IP。

---

## 3. 鉴权（所有接口通用）

| 项 | 值 |
|---|---|
| 授权方式 | 自定义请求头（API Key in Header） |
| 请求头 Key（Name） | `X-Bot-Api-Key` |
| 请求头 Value | 服务器 `.env` 里配置的 `APP_BOT_API_KEY` |

错误码：
- `401`：密钥缺失或不正确
- `503`：服务端未配置 `APP_BOT_API_KEY`（功能未启用）

自检命令（把 `YOUR_KEY` 换成真实密钥）：

```bash
curl -H "X-Bot-Api-Key: YOUR_KEY" https://qa.geonest.site/api/bot/v1/ping
# 预期返回：{"ok":true,"app":"测量软件测试平台","version":"0.4.0","server_time":"..."}
```

---

## 4. 接口清单

下面每个接口都给出了：用途、方法、完整 URL、参数、示例响应。
所有接口都必须带请求头 `X-Bot-Api-Key`，下文不再重复。

### 4.1 连通性自检 `GET /api/bot/v1/ping`
- **用途**：测试密钥是否正确、服务是否在线。
- **URL**：`https://qa.geonest.site/api/bot/v1/ping`
- **参数**：无
- **响应**：
```json
{ "ok": true, "app": "测量软件测试平台", "version": "0.4.0", "server_time": "2026-06-22T17:56:13" }
```

### 4.2 软件产品列表 `GET /api/bot/v1/softwares`
- **用途**：拿到各软件产品的 `id` 和名称（其它接口的 `software_id` 来源）。
- **URL**：`https://qa.geonest.site/api/bot/v1/softwares`
- **参数**：无
- **响应**：
```json
[ { "id": 1, "name": "Survey Master" }, { "id": 2, "name": "WoSense" } ]
```

### 4.3 版本列表 `GET /api/bot/v1/versions`
- **用途**：列出大版本及其子版本，拿到 `major_version_id` / `minor_version_id`。
- **URL**：`https://qa.geonest.site/api/bot/v1/versions?software_id=1`
- **参数**（Query）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `software_id` | 否 | 软件产品 id；不传则返回全部软件的版本 |

- **响应**：
```json
[
  {
    "id": 1,
    "version_no": "V4.0.3.0",
    "software_id": 1,
    "minor_versions": [
      { "id": 15, "version_no": "4.0.3.0.260310_XINGHUI(40300103)" },
      { "id": 88, "version_no": "4.0.3.0.26xxxx_xxxx(4030xxxx)" }
    ]
  }
]
```

### 4.3.1 版本解析（口语化版本 → id）`GET /api/bot/v1/resolve-version` ⭐

- **用途**：用户问"V4.0.3.1 怎么样了""40315 这个版本""构建 40300103"时，版本号往往不规范。本接口把**自由文本**解析为带 `id` 的候选大版本，**机器人无需再自己拉全量 `/versions` 去猜**。
- **URL**：`https://qa.geonest.site/api/bot/v1/resolve-version?q=40315`
- **参数**（Query）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `q` | 是 | 用户原话里的版本片段，如 `V4.0.3.1` / `40315` / `40300103` / `4.0.3.0 那个版本` |

- **解析规则**：纯数字串前 3 位视为 `a.b.c`、其余为尾段（`40315 → V4.0.3.15`）；5 位以上数字也会当作**构建号**去匹子版本，命中后回溯其父大版本。
- **响应**：
```json
{
  "query": "40315",
  "resolved": true,
  "ambiguous": false,
  "match_count": 1,
  "matches": [
    { "major_version_id": 7, "major_version_no": "V4.0.3.15", "software_id": 1,
      "minor_version_id": null, "minor_version_no": null, "match": "exact_major", "score": 95 }
  ]
}
```
- **机器人使用建议**：
  - `resolved=true` → 直接取 `matches[0].major_version_id` 调用后续接口（进度/Bug/反馈）。
  - `ambiguous=true` → 把 `matches` 里的 `major_version_no` 列给用户，请其指明是哪个大版本。
  - `match_count=0` → 回复"没找到该版本，请确认版本号"。

### 4.3.2 版本一问到底（解析+汇总，推荐）`GET /api/bot/v1/version-status` ⭐⭐

- **用途**：用户问"某版本怎么样了"的**首选接口**——一次调用完成「版本解析 + 进度 + Bug + 反馈」聚合，返回**紧凑 JSON**（无明细数组，体积受控，不会触发 2048 截断）。
- **URL**：`https://qa.geonest.site/api/bot/v1/version-status?q=40315`
- **参数**（Query）：同 4.3.1（`q`，必填）
- **响应（解析成功）**：
```json
{
  "query": "40315",
  "resolved": true,
  "major_version_id": 7,
  "major_version_no": "V4.0.3.15",
  "software_id": 1,
  "matched_minor_version_no": null,
  "progress": { "requirement_total": 120, "case_completed": 100, "case_completed_rate": 83.3,
                "test_completed": 95, "test_completed_rate": 79.2, "test_pending": 25,
                "retest_pending": 5, "retest_done": 90 },
  "bugs": { "total": 88, "open": 80, "by_status": { "active": 70, "resolved": 10, "closed": 8, "local": 0 }, "retest_failed": 0 },
  "feedback": { "total": 12, "pending": 4, "by_status": { "pending": 4, "processing": 0, "resolved": 8, "closed": 0 } }
}
```
- **响应（解析不唯一）**：返回候选，便于机器人追问：
```json
{ "query": "4030", "resolved": false, "ambiguous": true,
  "candidates": [ { "major_version_id": 1, "major_version_no": "V4.0.3.0" },
                  { "major_version_id": 9, "major_version_no": "V4.0.3.0.1" } ],
  "message": "匹配到多个版本，请指明具体大版本" }
```

> 提示：把 4.3.2 配成机器人的**主力工具**（描述写成"查某个版本的整体情况/进度/怎么样了"），多数提问一步到位；只有需要单独维度时再用 4.4 / 4.5 / 4.6。

### 4.4 版本测试进度 `GET /api/bot/v1/version-progress`
- **用途**：查某个大版本的需求测试 / 用例编写 / 复测进度。
- **URL**：`https://qa.geonest.site/api/bot/v1/version-progress?major_version_id=1`
- **参数**（Query）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `major_version_id` | 是 | 大版本 id（来自 4.3） |

- **响应**：
```json
{
  "major_version_id": 1,
  "major_version_no": "V4.0.3.0",
  "software_id": 1,
  "requirement_total": 120,
  "case_completed": 100,
  "case_completed_rate": 83.3,
  "test_completed": 95,
  "test_completed_rate": 79.2,
  "test_pending": 25,
  "retest_pending": 5,
  "retest_done": 90
}
```

### 4.5 Bug 统计 `GET /api/bot/v1/bugs/summary`
- **用途**：按状态 / 来源统计 Bug 数量。
- **URL**：`https://qa.geonest.site/api/bot/v1/bugs/summary?software_id=1`
- **参数**（Query，均可选；都不传=全部）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `software_id` | 否 | 按软件产品过滤 |
| `major_version_id` | 否 | 按大版本过滤（优先于 software_id） |

- **响应**：
```json
{
  "software_id": 1,
  "major_version_id": null,
  "total": 4910,
  "by_status": { "active": 88, "resolved": 112, "closed": 4710, "local": 0 },
  "by_source": { "requirement": 13, "manual": 4884, "case": 11, "retest": 2 },
  "retest_failed": 0,
  "open": 200
}
```
- 字段含义：`active`=活跃未解决；`resolved`=已解决待验证；`closed`=已关闭；`local`=仅本地未同步禅道；`open`=活跃+已解决+本地（即未关闭）。

### 4.5.1 搜索 Bug `GET /api/bot/v1/bugs/search` ⭐

- **用途**：用户报"29875 这个 bug 怎么样了"或只记得标题大意时，先定位到具体 Bug。**纯数字按禅道 Bug id 精确匹配，否则按标题模糊匹配**，返回最可能的若干候选。
- **URL**：`https://qa.geonest.site/api/bot/v1/bugs/search?q=地图崩溃`
- **参数**（Query）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `q` | 是 | 禅道 Bug id（`29875` / `b#29875`）或标题关键词（`地图崩溃`） |
| `limit` | 否 | 返回候选数上限，默认 5，最大 20 |

- **响应**：
```json
{
  "query": "地图崩溃",
  "match_count": 2,
  "matches": [
    { "zentao_bug_id": "29875", "bug_id": "b#12", "title": "地图加载时偶发崩溃",
      "status": "active", "assigned_to": "李四", "score": 90, "match": "title_fuzzy" }
  ]
}
```
- `status`：`active` 活跃 / `resolved` 已解决 / `closed` 已关闭 / `local` 仅本地；`match`：`id_exact` 或 `title_fuzzy`。
- **机器人用法**：拿 `matches[0].zentao_bug_id` 调 4.5.2 查精细内容；多候选时把标题列给用户选。

### 4.5.2 Bug 精细内容 `GET /api/bot/v1/bugs/detail` ⭐

- **用途**：已确定禅道 Bug id 后，查该 Bug 的详细信息（字段较丰富）。
- **URL**：`https://qa.geonest.site/api/bot/v1/bugs/detail?zentao_bug_id=29875`
- **参数**（Query）：`zentao_bug_id`（必填）
- **响应**（节选，字段较多）：
```json
{
  "zentao_bug_id": "29875", "bug_id": "b#12", "title": "地图加载时偶发崩溃",
  "status": "active", "live_status": "active", "closed": false, "is_retest_failed": false,
  "assigned_to": "李四", "opened_by": "王五", "opened_at": "2026-06-10 09:12",
  "source_type": "requirement", "affected_version": "4.0.3.0.260310(40300103)",
  "major_version_no": "V4.0.3.0", "found_minor_version_no": "4.0.3.0.260310(40300103)",
  "fixed_minor_version_no": null, "product_name": "Survey Master", "execution_name": "s4030",
  "linked_requirement": { "zentao_requirement_id": "5604", "title": "支持野外离线地图缓存" },
  "linked_case_label": "", "resolution": "fixed",
  "closed_by": "", "close_date": null, "close_comment": "",
  "zentao_bug_url": "https://zentao.../bug-view-29875.html",
  "last_zentao_synced_at": "2026-06-22 18:00",
  "note": "完整复现步骤/正文请见禅道原始链接 zentao_bug_url"
}
```
> 说明：本系统本地库存的是 Bug 的**标题与元数据**（状态/指派/版本/关联需求等），**完整复现步骤正文存在禅道**——需要时让用户点 `zentao_bug_url` 查看。

### 4.5.3 搜索需求 `GET /api/bot/v1/requirements/search` ⭐

- **用途**：同 4.5.1，但针对**需求**。纯数字按禅道需求 id 精确匹配，否则按标题模糊匹配。
- **URL**：`https://qa.geonest.site/api/bot/v1/requirements/search?q=离线地图`
- **参数**（Query）：`q`（必填）、`limit`（可选，默认 5）
- **响应**：
```json
{
  "query": "离线地图", "match_count": 1,
  "matches": [
    { "id": 88, "zentao_req_id": "r#5604", "title": "支持野外离线地图缓存",
      "major_version_no": "V4.0.3.0", "status": "pending", "owner": "张三",
      "score": 90, "match": "title_fuzzy" }
  ]
}
```
- **机器人用法**：拿 `matches[0].zentao_req_id`（必要时加 `major_version_no` 对应的 id）调 4.5.4。

### 4.5.4 需求精细内容 `GET /api/bot/v1/requirements/detail` ⭐

- **用途**：已确定禅道需求 id 后，查该需求的详细信息与测试进展。
- **URL**：`https://qa.geonest.site/api/bot/v1/requirements/detail?zentao_req_id=r%235604`
- **参数**（Query）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `zentao_req_id` | 是 | 禅道需求 id（用 4.5.3 返回的 `zentao_req_id` 原样回传） |
| `major_version_id` | 否 | 同一需求 id 跨多个大版本时用于消歧 |

- **响应（唯一命中）**：
```json
{
  "zentao_req_id": "r#5604", "resolved": true, "id": 88, "title": "支持野外离线地图缓存",
  "status": "pending", "major_version_id": 1, "major_version_no": "V4.0.3.0", "owner": "张三",
  "zentao_story_id": 5604, "plan_title": "4.0.3 计划",
  "case_completed": true, "test_completed": false, "test_completed_at": null,
  "retest_completed": false, "retest_passed": null, "retested_by": null,
  "test_notes": "离线瓦片需覆盖到 18 级",
  "case_count": 12, "bug_count": 3, "bug_open": 1, "linked_bug_ids": ["29875"],
  "note": "完整需求正文（spec）请见禅道原始页面"
}
```
- **响应（跨多个大版本，需消歧）**：`resolved=false` + `ambiguous=true` + `candidates`（各含 `major_version_no`），请补 `major_version_id` 再查。

### 4.6 反馈统计 `GET /api/bot/v1/feedback/summary`
- **用途**：按状态统计用户反馈数量。
- **URL**：`https://qa.geonest.site/api/bot/v1/feedback/summary?software_id=1`
- **参数**（Query，均可选）：同 4.5（`software_id` / `major_version_id`）
- **响应**：
```json
{
  "software_id": 1,
  "major_version_id": null,
  "total": 19,
  "by_status": { "pending": 4, "processing": 12, "resolved": 3, "closed": 0 },
  "pending": 16
}
```
- 字段含义：`pending`=待处理；`processing`=处理中；`resolved`=已处理；`closed`=已关闭；顶层 `pending`=待处理+处理中（未完结）。

### 4.7 工作量报表 `GET /api/bot/v1/report/summary`
- **用途**：某时间段内团队整体工作量汇总。
- **URL**：`https://qa.geonest.site/api/bot/v1/report/summary?start_date=2026-06-01&end_date=2026-06-22&software_id=1`
- **参数**（Query）：

| 参数 | 必填 | 说明 |
|---|---|---|
| `start_date` | 是 | 开始日期，格式 `YYYY-MM-DD` |
| `end_date` | 是 | 结束日期，格式 `YYYY-MM-DD` |
| `software_id` | 否 | 按软件产品过滤 |
| `major_version_id` | 否 | 按大版本过滤 |

- **响应**：
```json
{
  "start_date": "2026-06-01",
  "end_date": "2026-06-22",
  "software_id": 1,
  "major_version_id": null,
  "overview": {
    "executed_requirements": 42,
    "created_cases": 130,
    "created_bugs": 88,
    "retested_reqs": 20,
    "closed_bugs": 75,
    "created_feedbacks": 12,
    "processed_feedbacks": 9
  }
}
```

### 4.8 创建反馈 `POST /api/bot/v1/feedback`（写）
- **用途**：让机器人代提一条用户反馈，落库后状态=待处理。
- **URL**：`https://qa.geonest.site/api/bot/v1/feedback`
- **请求头**：额外加 `Content-Type: application/json`
- **请求体（JSON）**：

| 字段 | 必填 | 说明 |
|---|---|---|
| `major_version_id` | 是 | 大版本 id |
| `minor_version_id` | 是 | 子版本 id |
| `summary` | 是 | 反馈内容（1–2000 字） |
| `feedback_no` | 否 | 反馈编号（纯数字，可不填） |

- **请求示例**：
```json
{ "major_version_id": 1, "minor_version_id": 15, "summary": "野外采集偶发卡死" }
```
- **响应**：`{ "id": 21, "message": "反馈已创建" }`

### 4.9 推送版本进度到企业微信群 `POST /api/bot/v1/push/version-progress`（写）
- **用途**：触发把某大版本的“用例编写进度”推送到企业微信机器人 webhook 群（需服务端已配置 `APP_WECOM_WEBHOOK_URL`，否则静默跳过不报错）。
- **URL**：`https://qa.geonest.site/api/bot/v1/push/version-progress`
- **请求头**：额外加 `Content-Type: application/json`
- **请求体（JSON）**：`{ "major_version_id": 1 }`
- **响应**：`{ "message": "Case progress pushed" }`

---

## 5. 在企业微信「智能机器人 / API 插件」里怎么填

企业微信添加 API 工具时，各字段对应关系如下（以「查版本测试进度」为例）：

| 配置项 | 填写内容 |
|---|---|
| 工具名称 / API 描述 | `查询某大版本的测试进度`（中文描述，便于大模型判断何时调用） |
| 请求方法（Method） | `GET` |
| 请求地址（URL） | `https://qa.geonest.site/api/bot/v1/version-progress` |
| 请求头（Header）名 | `X-Bot-Api-Key` |
| 请求头（Header）值（VALUE） | `<你的 APP_BOT_API_KEY 密钥>` |
| 授权方式（鉴权） | 自定义 Header / API Key（不是 OAuth，不是 Bearer Token） |
| 入参（Query 参数） | `major_version_id`（整数，必填）——可描述为“大版本 id，先用 `/versions` 查到” |
| 出参 | 见 4.4 响应字段 |

> 若企业微信插件配置界面支持直接导入 **OpenAPI / Swagger** 描述：本系统自带在线接口文档，访问
> `https://qa.geonest.site/docs`（Swagger UI）或 `https://qa.geonest.site/openapi.json`（机读规范），
> 在标签 **bot-api** 下即可找到上述全部接口，可直接导入后再补上 `X-Bot-Api-Key` 请求头。

### 给机器人配置 Header 的两种常见形态

- **形态 A：固定请求头**（推荐）
  - Name：`X-Bot-Api-Key`
  - Value：你的密钥（写死在插件配置里）
- **形态 B：把密钥作为“鉴权凭据/Secret”变量**
  - 鉴权类型选「API Key」「自定义 Header」
  - 字段名 `X-Bot-Api-Key`，位置 `Header`，值为你的密钥

---

## 6. 建议优先给机器人开放的几个工具（可直接照搬）

| 机器人工具（中文名） | 方法 | URL | 关键参数 |
|---|---|---|---|
| **查某版本整体情况（首选）** | GET | `/api/bot/v1/version-status` | `q`（用户原话版本） |
| 解析口语化版本号 | GET | `/api/bot/v1/resolve-version` | `q`（用户原话版本） |
| 搜索 Bug（id/标题） | GET | `/api/bot/v1/bugs/search` | `q` |
| 查 Bug 精细内容 | GET | `/api/bot/v1/bugs/detail` | `zentao_bug_id` |
| 搜索需求（id/标题） | GET | `/api/bot/v1/requirements/search` | `q` |
| 查需求精细内容 | GET | `/api/bot/v1/requirements/detail` | `zentao_req_id` |
| 查软件产品列表 | GET | `/api/bot/v1/softwares` | 无 |
| 查版本列表 | GET | `/api/bot/v1/versions` | `software_id`(可选) |
| 查版本测试进度 | GET | `/api/bot/v1/version-progress` | `major_version_id` |
| 查 Bug 统计 | GET | `/api/bot/v1/bugs/summary` | `software_id`/`major_version_id` |
| 查反馈统计 | GET | `/api/bot/v1/feedback/summary` | `software_id`/`major_version_id` |
| 查工作量报表 | GET | `/api/bot/v1/report/summary` | `start_date`,`end_date` |
| 代提反馈 | POST | `/api/bot/v1/feedback` | `major_version_id`,`minor_version_id`,`summary` |
| 推送版本进度到群 | POST | `/api/bot/v1/push/version-progress` | `major_version_id` |

（前缀均为 `https://qa.geonest.site`）

---

## 7. 安全小结

1. 这组接口**只读为主 + 少量写**，写操作仅限“创建反馈”和“触发企微推送”，不涉及删除/改配置等高危动作。
2. 鉴权与用户体系隔离，机器人密钥独立、可随时吊销，不会影响任何人登录。
3. 默认关闭：不配置 `APP_BOT_API_KEY` 就不会对外暴露任何数据。
4. 强烈建议反向代理层对 `/api/bot/` 限流 + 限定来源，并全程走 HTTPS（`qa.geonest.site` 已满足）。
