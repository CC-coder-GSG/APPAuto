# 企业微信机器人 · API 工具「照抄版」配置

> 用途：在企业微信「智能机器人 → 添加 API 插件 / 工具（Action）」里逐个照抄填写。
> 每个工具都给出：**基础信息**（名称/方法/URL/请求头）+ **配置输入参数** + **配置输出参数**。

---

## 0. 通用填写说明（每个工具都一样）

**请求头（鉴权，所有工具都要加）**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `X-Bot-Api-Key` | 机器人密钥（服务端 APP_BOT_API_KEY） | String | Header | 是 | 你的密钥 |

- **Base URL（线上）**：`https://qa.geonest.site`
- **参数类型**可选项：`String / Integer / Number / Array / Object / Boolean`
- **传入方法**可选项：`Body / Query / Header / Path`
- **输出参数**：根节点固定是 `Response`。返回单个对象→`Response` 选 `Object`；返回数组→`Response` 选 `Array`，数组元素叫 `[Array item]`（Object）。
- 文档里凡是写「（Object 的子字段）」「（Array item 的子字段）」的表，都是在企微 UI 里点开对应节点后，往**它下面**添加这些子字段。
- 输入参数里所有 `Integer/Number` 类型在企微里如不确定，填 `String` 也能用（后端会自行转换）；但建议按本文标注填。

---

## 1. 连通性自检 · ping

- **工具名称**：连通性自检
- **工具描述**：测试机器人密钥是否有效、服务是否在线
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/ping`

**配置输入参数**：无（仅需通用请求头）

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `ok` | 是否正常 | Boolean |
| `app` | 应用名 | String |
| `version` | 版本号 | String |
| `server_time` | 服务器时间 | String |

---

## 2. 软件产品列表 · softwares

- **工具名称**：查软件产品列表
- **工具描述**：列出所有软件产品的 id 和名称
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/softwares`

**配置输入参数**：无

**配置输出参数**（`Response` = Array，`[Array item]` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `id` | 软件产品 id，不需要告诉用户 | Integer |
| `name` | 软件产品名称 | String |

---

## 3. 版本列表 · versions

- **工具名称**：查版本列表
- **工具描述**：列出大版本及其子版本，用于查到版本 id
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/versions`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `software_id` | 软件产品 id，不传则返回全部 | Integer | Query | 否 | |

**配置输出参数**（`Response` = Array，`[Array item]` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `id` | 大版本 id，不需要告诉用户 | Integer |
| `version_no` | 大版本号 | String |
| `software_id` | 软件 id，不需要告诉用户 | Integer |
| `minor_versions` | 子版本列表 | Array |

> `minor_versions`（Array item = Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `id` | 子版本 id，不需要告诉用户 | Integer |
| `version_no` | 子版本号 | String |

---

## 4. 版本解析 · resolve-version ⭐

- **工具名称**：解析口语化版本号
- **工具描述**：把用户口语化的版本（如 V4.0.3.1、40315、40300103）解析成候选大版本及其 id
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/resolve-version`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `q` | 用户原话里的版本片段，如 V4.0.3.1 / 40315 / 40300103 | String | Query | 是 | |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `query` | 原始查询词 | String |
| `resolved` | 是否唯一确定（true 可直接用 matches[0]） | Boolean |
| `ambiguous` | 是否有多个候选需用户澄清 | Boolean |
| `match_count` | 候选数量 | Integer |
| `matches` | 候选大版本列表 | Array |

> `matches`（Array item = Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `major_version_id` | 大版本 id，不需要告诉用户 | Integer |
| `major_version_no` | 大版本号 | String |
| `software_id` | 软件 id，不需要告诉用户 | Integer |
| `minor_version_id` | 命中的子版本 id（可能为空） | Integer |
| `minor_version_no` | 命中的子版本号（可能为空） | String |
| `match` | 匹配方式（exact_major/build_code/substring） | String |
| `score` | 匹配置信分 | Integer |

---

## 5. 版本一问到底 · version-status ⭐⭐（推荐主力工具）

- **工具名称**：查某个版本的整体情况
- **工具描述**：用户问"某版本怎么样了/进度如何"时调用，自动解析版本并返回进度+Bug+反馈汇总
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/version-status`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `q` | 用户原话里的版本片段，如 V4.0.3.1 / 40315 | String | Query | 是 | |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `query` | 原始查询词 | String |
| `resolved` | 是否成功解析到唯一版本 | Boolean |
| `major_version_id` | 大版本 id，不需要告诉用户 | Integer |
| `major_version_no` | 大版本号 | String |
| `software_id` | 软件 id，不需要告诉用户 | Integer |
| `matched_minor_version_no` | 命中的子版本号（可能为空） | String |
| `progress` | 测试进度 | Object |
| `bugs` | Bug 概况 | Object |
| `feedback` | 反馈概况 | Object |
| `ambiguous` | 解析不唯一时为 true（此时无 progress/bugs/feedback） | Boolean |
| `candidates` | 解析不唯一时的候选列表 | Array |
| `message` | 解析不唯一/失败时的提示语 | String |

> `progress`（Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `requirement_total` | 需求总数 | Integer |
| `case_completed` | 用例完成数量 | Integer |
| `case_completed_rate` | 用例完成率 | Number |
| `test_completed` | 测试完成数量 | Integer |
| `test_completed_rate` | 测试完成率 | Number |
| `test_pending` | 等待测试的需求数量 | Integer |
| `retest_pending` | 等待复测的需求数量 | Integer |
| `retest_done` | 已复测完成的需求数量 | Integer |

> `bugs`（Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `total` | Bug 总数 | Integer |
| `open` | 未关闭 Bug 数 | Integer |
| `by_status` | 各状态 Bug 数 | Object |
| `retest_failed` | 复测未通过数 | Integer |

> `bugs.by_status`（Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `active` | 活跃未解决 | Integer |
| `resolved` | 已解决待验证 | Integer |
| `closed` | 已关闭 | Integer |
| `local` | 仅本地未同步 | Integer |

> `feedback`（Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `total` | 反馈总数 | Integer |
| `pending` | 未完结反馈数 | Integer |
| `by_status` | 各状态反馈数 | Object |

> `feedback.by_status`（Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `pending` | 待处理 | Integer |
| `processing` | 处理中 | Integer |
| `resolved` | 已处理 | Integer |
| `closed` | 已关闭 | Integer |

> `candidates`（Array item = Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `major_version_id` | 大版本 id，不需要告诉用户 | Integer |
| `major_version_no` | 大版本号 | String |

---

## 6. 版本测试进度 · version-progress

- **工具名称**：查版本测试进度
- **工具描述**：查某个大版本的需求测试/用例编写/复测进度（需先有 major_version_id）
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/version-progress`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `major_version_id` | 大版本 id | Integer | Query | 是 | |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `major_version_id` | 大版本 id，不需要告诉用户 | Integer |
| `major_version_no` | 大版本号 | String |
| `software_id` | 软件 id，不需要告诉用户 | Integer |
| `requirement_total` | 这个大版本下需求总数 | Integer |
| `case_completed` | 用例完成的数量 | Integer |
| `case_completed_rate` | 用例完成率 | Number |
| `test_completed` | 测试完成的数量 | Integer |
| `test_completed_rate` | 测试完成率 | Number |
| `test_pending` | 等待测试的需求数量 | Integer |
| `retest_pending` | 等待复测的需求数量 | Integer |
| `retest_done` | 已经测试完成的需求数量 | Integer |

---

## 7. Bug 统计 · bugs/summary

- **工具名称**：查 Bug 统计
- **工具描述**：按状态/来源统计 Bug 数量
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/bugs/summary`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `software_id` | 软件产品 id，可选 | Integer | Query | 否 | |
| `major_version_id` | 大版本 id，可选（优先于 software_id） | Integer | Query | 否 | |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `software_id` | 软件 id，不需要告诉用户 | Integer |
| `major_version_id` | 大版本 id，不需要告诉用户 | Integer |
| `total` | Bug 总数 | Integer |
| `by_status` | 各状态 Bug 数 | Object |
| `by_source` | 各来源 Bug 数 | Object |
| `retest_failed` | 复测未通过数 | Integer |
| `open` | 未关闭 Bug 数（活跃+已解决+本地） | Integer |

> `by_status`（Object）的子字段：`active` / `resolved` / `closed` / `local`，均为 Integer（含义同第 5 节）。
> `by_source` 为 Object，键是来源类型（requirement/manual/case/retest 等），值为 Integer；企微里 `by_source` 选 Object 即可，子字段可不细列。

---

## 8. 反馈统计 · feedback/summary

- **工具名称**：查反馈统计
- **工具描述**：按状态统计用户反馈数量
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/feedback/summary`

**配置输入参数**：同第 7 节（`software_id` / `major_version_id`，均可选）

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `software_id` | 软件 id，不需要告诉用户 | Integer |
| `major_version_id` | 大版本 id，不需要告诉用户 | Integer |
| `total` | 反馈总数 | Integer |
| `by_status` | 各状态反馈数 | Object |
| `pending` | 未完结反馈数（待处理+处理中） | Integer |

> `by_status`（Object）的子字段：`pending` / `processing` / `resolved` / `closed`，均为 Integer。

---

## 9. 工作量报表 · report/summary

- **工具名称**：查团队工作量报表
- **工具描述**：某时间段内团队整体工作量汇总
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/report/summary`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `start_date` | 开始日期，格式 YYYY-MM-DD | String | Query | 是 | |
| `end_date` | 结束日期，格式 YYYY-MM-DD | String | Query | 是 | |
| `software_id` | 软件产品 id，可选 | Integer | Query | 否 | |
| `major_version_id` | 大版本 id，可选 | Integer | Query | 否 | |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `start_date` | 开始日期 | String |
| `end_date` | 结束日期 | String |
| `software_id` | 软件 id，不需要告诉用户 | Integer |
| `major_version_id` | 大版本 id，不需要告诉用户 | Integer |
| `overview` | 工作量概览 | Object |

> `overview`（Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `executed_requirements` | 执行的需求数 | Integer |
| `created_cases` | 新建用例数 | Integer |
| `created_bugs` | 新建 Bug 数 | Integer |
| `retested_reqs` | 复测需求数 | Integer |
| `closed_bugs` | 关闭 Bug 数 | Integer |
| `created_feedbacks` | 新建反馈数 | Integer |
| `processed_feedbacks` | 处理反馈数 | Integer |

---

## 10. 搜索 Bug · bugs/search ⭐

- **工具名称**：搜索 Bug
- **工具描述**：用户报某个 bug（给禅道编号或只记得标题）时，先定位到具体 Bug。纯数字按禅道 Bug id 精确匹配，否则按标题模糊匹配
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/bugs/search`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `q` | 禅道 Bug id（如 29875）或标题关键词（如 地图崩溃） | String | Query | 是 | |
| `limit` | 返回候选数上限，最大 20 | Integer | Query | 否 | 5 |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `query` | 原始查询词 | String |
| `match_count` | 候选数量 | Integer |
| `matches` | Bug 候选列表 | Array |

> `matches`（Array item = Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `zentao_bug_id` | 禅道 Bug id（用它查详情） | String |
| `bug_id` | 本地编号 | String |
| `title` | Bug 标题 | String |
| `status` | 状态（active/resolved/closed/local） | String |
| `assigned_to` | 当前指派给谁 | String |
| `score` | 匹配分 | Integer |
| `match` | 匹配方式（id_exact/title_fuzzy） | String |

---

## 11. Bug 精细内容 · bugs/detail ⭐

- **工具名称**：查 Bug 精细内容
- **工具描述**：已确定禅道 Bug id 后，查该 Bug 的状态/指派/标题/受影响版本/关联需求/关闭信息等
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/bugs/detail`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `zentao_bug_id` | 禅道 Bug id（用搜索结果里的 zentao_bug_id） | String | Query | 是 | |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `zentao_bug_id` | 禅道 Bug id | String |
| `bug_id` | 本地编号 | String |
| `title` | Bug 标题 | String |
| `status` | 有效状态（active/resolved/closed/local） | String |
| `live_status` | 禅道实时状态 | String |
| `closed` | 是否已关闭 | Boolean |
| `is_retest_failed` | 是否复测未通过 | Boolean |
| `assigned_to` | 当前指派给谁 | String |
| `opened_by` | 提交人 | String |
| `opened_at` | 提交时间 | String |
| `source_type` | 来源类型 | String |
| `zentao_source_type` | 禅道来源类型 | String |
| `affected_version` | 受影响版本 | String |
| `major_version_no` | 所属大版本号 | String |
| `found_minor_version_no` | 发现的子版本号 | String |
| `fixed_minor_version_no` | 修复的子版本号 | String |
| `product_name` | 所属产品名 | String |
| `execution_name` | 所属执行 | String |
| `linked_requirement` | 关联需求 | Object |
| `linked_case_label` | 关联用例标签 | String |
| `resolution` | 解决方案 | String |
| `closed_by` | 关闭人 | String |
| `close_date` | 关闭时间 | String |
| `close_comment` | 关闭备注 | String |
| `zentao_bug_url` | 禅道原始链接（完整复现步骤在这里看） | String |
| `last_zentao_synced_at` | 最近同步时间 | String |
| `note` | 提示语 | String |

> `linked_requirement`（Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `zentao_requirement_id` | 关联需求的禅道 id | String |
| `title` | 关联需求标题 | String |

---

## 12. 搜索需求 · requirements/search ⭐

- **工具名称**：搜索需求
- **工具描述**：用户问某个需求（给禅道编号或只记得标题）时先定位。纯数字按禅道需求 id 精确匹配，否则按标题模糊匹配
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/requirements/search`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `q` | 禅道需求 id（如 5604）或标题关键词（如 离线地图） | String | Query | 是 | |
| `limit` | 返回候选数上限，最大 20 | Integer | Query | 否 | 5 |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `query` | 原始查询词 | String |
| `match_count` | 候选数量 | Integer |
| `matches` | 需求候选列表 | Array |

> `matches`（Array item = Object）的子字段：

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `id` | 本地需求 id，不需要告诉用户 | Integer |
| `zentao_req_id` | 禅道需求 id（用它查详情） | String |
| `title` | 需求标题 | String |
| `major_version_no` | 所属大版本号 | String |
| `status` | 需求状态 | String |
| `owner` | 负责人 | String |
| `score` | 匹配分 | Integer |
| `match` | 匹配方式（id_exact/title_fuzzy） | String |

---

## 13. 需求精细内容 · requirements/detail ⭐

- **工具名称**：查需求精细内容
- **工具描述**：已确定禅道需求 id 后，查该需求的详情与测试进展
- **请求方法**：GET
- **URL**：`https://qa.geonest.site/api/bot/v1/requirements/detail`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `zentao_req_id` | 禅道需求 id（用搜索结果里的 zentao_req_id 原样回传） | String | Query | 是 | |
| `major_version_id` | 同一需求跨多个大版本时用于消歧，可选 | Integer | Query | 否 | |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `zentao_req_id` | 禅道需求 id | String |
| `resolved` | 是否唯一命中（false 时见下方候选字段） | Boolean |
| `id` | 本地需求 id，不需要告诉用户 | Integer |
| `title` | 需求标题 | String |
| `status` | 需求状态 | String |
| `major_version_id` | 大版本 id，不需要告诉用户 | Integer |
| `major_version_no` | 所属大版本号 | String |
| `owner` | 负责人 | String |
| `zentao_story_id` | 禅道 story id | Integer |
| `plan_title` | 所属计划标题 | String |
| `case_completed` | 用例是否编写完成 | Boolean |
| `test_completed` | 是否测试完成 | Boolean |
| `test_completed_at` | 测试完成时间 | String |
| `retest_completed` | 是否复测完成 | Boolean |
| `retest_passed` | 复测是否通过 | Boolean |
| `retested_by` | 复测人 | String |
| `test_notes` | 测试备注 | String |
| `case_count` | 关联用例数 | Integer |
| `bug_count` | 关联 Bug 数 | Integer |
| `bug_open` | 未关闭 Bug 数 | Integer |
| `linked_bug_ids` | 关联 Bug 的禅道 id 列表 | Array |
| `ambiguous` | 跨多大版本未消歧时为 true | Boolean |
| `candidates` | 未消歧时的候选列表 | Array |
| `message` | 未消歧时的提示语 | String |
| `note` | 提示语 | String |

> `linked_bug_ids` 为 Array，`[Array item]` = String。
> `candidates`（Array item = Object）子字段同第 12 节 `matches` 的字段。

---

## 14. 代提反馈 · feedback（写操作）

- **工具名称**：代提用户反馈
- **工具描述**：让机器人代提一条用户反馈，落库后状态为待处理
- **请求方法**：POST
- **URL**：`https://qa.geonest.site/api/bot/v1/feedback`
- **额外请求头**：`Content-Type: application/json`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `major_version_id` | 大版本 id | Integer | Body | 是 | |
| `minor_version_id` | 子版本 id | Integer | Body | 是 | |
| `summary` | 反馈内容（1–2000 字） | String | Body | 是 | |
| `feedback_no` | 反馈编号（纯数字，可选） | String | Body | 否 | |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `id` | 新建反馈 id | Integer |
| `message` | 结果提示 | String |

---

## 15. 推送版本进度到群 · push/version-progress（写操作）

- **工具名称**：推送版本进度到企业微信群
- **工具描述**：触发把某大版本的用例编写进度推送到企业微信机器人群
- **请求方法**：POST
- **URL**：`https://qa.geonest.site/api/bot/v1/push/version-progress`
- **额外请求头**：`Content-Type: application/json`

**配置输入参数**

| 参数名称 | 参数描述 | 参数类型 | 传入方法 | 是否必填 | 默认值 |
|---|---|---|---|---|---|
| `major_version_id` | 大版本 id | Integer | Body | 是 | |

**配置输出参数**（`Response` = Object）

| 参数名称 | 参数描述 | 参数类型 |
|---|---|---|
| `message` | 结果提示 | String |

---

## 附：建议优先配置的工具顺序

1. **查某个版本的整体情况**（第 5 节 version-status）——最常用，一步到位
2. **搜索 Bug** + **查 Bug 精细内容**（第 10、11 节）
3. **搜索需求** + **查需求精细内容**（第 12、13 节）
4. **解析口语化版本号**（第 4 节，给上面几个工具兜底定位版本）
5. 其余统计/报表/写操作按需添加
