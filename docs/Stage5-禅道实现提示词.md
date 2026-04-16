# Stage5 禅道联动改造提示词

## 使用方式

把下面整段提示词直接交给 AI 编码助手使用。  
目标是让 AI 基于当前仓库，直接完成 Stage5 与禅道联动改造。

---

## 提示词正文

你现在在一个已有的测试管理系统仓库中工作，请直接基于现有代码实现以下功能。  
不要推翻现有 Stage5 结构，要在当前实现上增量修改。

### 一、项目背景

当前系统的整体测试页（Stage5）已经具备：

- 从禅道按大版本同步 Bug 到本地库
- 整体测试分页
- 当前页禅道 hydrate
- 本地闭环确认

但现在还需要继续完成以下能力：

1. Stage5 中“来自禅道同步的 Bug”，编辑、删除、关闭、重新激活、重新指派，都要尽量联动禅道 API，而不是只改本地库。
2. 重新激活时，指派人列表要来自禅道网页实际使用的数据源，并在前端做成可搜索下拉框。
3. 整体测试页默认按时间倒序显示 Bug。
4. 当前禅道同步偏慢，需要做明显优化。
5. 保留现有 Stage5 的“闭环确认”本地记录机制，不要丢掉本地流程数据。

### 二、必须遵守的设计原则

1. 不要破坏现有 Stage5 的本地流程记录。
   也就是说：
   - `BugStage5Record`
   - 本地闭环记录
   - 审计日志
   - 特派信息
   都要保留。

2. “禅道同步 Bug”和“本地自由 Bug”要区分处理。
   区分标准：
   - `zentao_bug_id` 非空：禅道 Bug
   - `zentao_bug_id` 为空：本地 Bug

3. 对禅道 Bug：
   - 不允许再像以前那样随意修改 `bug_id = b#xxxx`
   - `bug_id` 视为远端对象标识

4. 对本地 Bug：
   - 继续保留当前本地编辑 / 删除 / 闭环逻辑

5. 所有涉及禅道写操作的地方，都要有失败保护：
   - 禅道失败时，本地不能进入伪成功状态
   - 返回清晰错误提示

### 三、你必须先理解的现状

当前关键文件：

- Stage5 前端：
  - `frontend/js/tabs/stage5.js`
  - `frontend/index.html`
- Stage5 后端：
  - `app/api/routes/stage5.py`
  - `app/services/stage5_service.py`
- 本地 Bug 编辑 / 删除：
  - `app/api/routes/bugs.py`
  - `app/services/bug_service.py`
- 禅道 hydrate：
  - `app/api/routes/zentao_hydrate.py`
- 禅道客户端：
  - `app/services/zentao_client_service.py`

请先阅读：

- `docs/Stage5-禅道关闭激活调研.md`

这个文档里已经包含：

- 当前按钮行为
- 已确认可用的禅道 API
- 29902 / 29925 的实测结论
- 网页动作页 `.json` 的真实数据来源
- 同步偏慢的根因

### 四、你要实现的功能

#### 1. 调整 Stage5 顶部按钮语义

保留两个按钮，但行为要明确：

- `加载全景大盘`
  - 默认行为：同步当前版本后再加载
- `强制同步禅道并刷新`
  - 无条件从禅道重新同步当前版本，再刷新页面

要求：

- 不要让两个按钮完全重复但文案不同
- 给“强制同步”显示明确同步结果
- “加载全景大盘”可以静默同步，但需要支持短时缓存优化，避免每次点击都完整重拉远端

#### 2. 禅道 Bug 的编辑逻辑

对 `zentao_bug_id` 非空的 Bug：

- `编辑` 改成“编辑禅道 Bug”
- 通过禅道 API 修改，而不是本地改 `bug_id`

最低要求：

- 支持修改标题
- 支持同步回本地 `zentao_bug_title`
- 保持本地记录不丢

注意：

- 不要再允许前端把禅道 Bug 当作“改编号”处理

#### 3. 禅道 Bug 的删除逻辑

对 `zentao_bug_id` 非空的 Bug：

- 删除要调用禅道：
  - `DELETE /api.php/v1/bugs/{id}`

但实现方式不要直接把本地记录硬删掉。

要求：

- 删除前弹强提示框
- 提示用户这是禅道删除，当前实例删除后是 `deleted=true`
- 本地建议保留记录，用“已在禅道删除”或隐藏标记处理
- 保留审计日志

对本地 Bug：

- 保持原来的本地删除逻辑

#### 4. 闭环确认联动禅道关闭

保留现有 Stage5 闭环确认逻辑，并新增：

- 用户勾选“我的闭环确认”时，增加“闭环说明 / 验证说明”输入
- 如果该 Bug 是禅道 Bug，并且禅道状态允许关闭，则调用：
  - `POST /api.php/v1/bugs/{id}/close`

传参至少包括：

- `comment`

注意状态规则：

- 禅道 `resolved` 才允许关闭
- 禅道已经 `closed` 时，不重复关闭
- 禅道仍是 `active` 时，不允许直接关闭，要提示“禅道中尚未解决，不能闭环关闭”

本地仍然要保留：

- `BugStage5Record`
- 闭环说明
- 审计日志

#### 5. 新增“重新激活”能力

对禅道中状态为 `closed` 的 Bug：

- 在 Stage5 行内显示 `重新激活` 按钮

点击后打开弹窗，字段至少包括：

- `重新指派给`
- `影响版本`
- `重新激活说明`

提交时调用：

- `POST /api.php/v1/bugs/{id}/active`

传参至少包括：

- `assignedTo`
- `openedBuild`
- `comment`

激活成功后：

- 刷新本地数据
- 本地状态改为未闭环
- 保留本地历史闭环记录，不要物理删除

#### 6. 新增“仅重新指派”能力

已确认：

- `POST /api.php/v1/bugs/{id}/assign`

是独立动作，不会改变 Bug 状态。

要求：

- 对禅道 Bug 增加“重新指派”入口
- 打开可搜索下拉框
- 选择人后调用 `assign`
- 成功后同步本地显示

这个能力与“重新激活”分开存在。

#### 7. 指派人下拉的数据来源

这是本次实现的重点，不要误用 `/v1/users`。

已确认当前实例中：

- `/api.php/v1/users`：权限受限
- `/api.php/v2/users`：实例端点异常
- 但网页动作页自己的 JSON 数据中，已经稳定包含 `users`

必须优先使用下面这些页面 JSON：

- `GET /bug-activate-{bugID}.json`
- `GET /bug-resolve-{bugID}.json`
- `GET /bug-view-{bugID}.json`
- 必要时 `GET /bug-close-{bugID}.json`

从中读取：

- `users`
- `builds`
- `bug.assignedTo`

`users` 字段格式示例：

```json
{
  "chenwenbo": "C:陈文博",
  "guliangyan": "G:顾靓燕",
  "zhangchao": "Z:张超"
}
```

要求你实现：

- 后端封装一个“获取 Bug 动作页候选数据”的接口或服务
- 前端把 `users` 转为可搜索下拉框选项
- 提交时使用字典的 `key` 作为 `assignedTo`
- 下拉框支持输入筛选：
  - 中文名
  - 账号
  - 首字母前缀

推荐展示：

- `陈文博（chenwenbo）`

#### 8. 整体测试默认按时间倒序

当前 Stage5 没有显式排序。

要求：

- 后端返回 Stage5 `overview` 时，默认按：
  - `updated_at desc`
  - `id desc`
  排序

不要只在前端排序。

#### 9. 同步性能优化

当前慢的根因包括：

- 每次加载都先完整同步
- build bug 拉取是串行的
- 本地 upsert 是逐条查询
- 当前页 hydrate 仍然有成本

你需要至少完成以下优化：

1. build bug 列表改并发抓取
   - 并发数建议 4 到 8

2. 本地 upsert 改为批量预取
   - 先一次查出本地已有 `zentao_bug_id`
   - 内存中判断新增 / 更新

3. 给“加载全景大盘”增加短时同步缓存策略
   - 同一大版本最近几分钟内同步过，就优先读本地
   - “强制同步禅道并刷新”要绕过缓存

4. 尽量减少当前页额外 hydrate
   - 已有本地 `zentao_bug_title / zentao_bug_url` 时优先直接显示

5. 保持当前分页逻辑可用
   - 不要因为性能优化把分页删掉

### 五、接口与行为映射

你在实现时，至少需要支持这些禅道动作：

- 修改 Bug：
  - `PUT /api.php/v1/bugs/{id}`
- 关闭 Bug：
  - `POST /api.php/v1/bugs/{id}/close`
- 激活 Bug：
  - `POST /api.php/v1/bugs/{id}/active`
- 解决 Bug：
  - `POST /api.php/v1/bugs/{id}/resolve`
- 指派 Bug：
  - `POST /api.php/v1/bugs/{id}/assign`
- 删除 Bug：
  - `DELETE /api.php/v1/bugs/{id}`

同时支持页面 JSON 候选数据：

- `GET /bug-activate-{bugID}.json`
- `GET /bug-resolve-{bugID}.json`
- `GET /bug-view-{bugID}.json`
- `GET /bug-close-{bugID}.json`

### 六、你需要补的后端能力

请优先考虑增加这些能力：

1. 禅道 Bug 动作服务
   - 编辑
   - 删除
   - 关闭
   - 激活
   - 指派

2. Bug 动作页候选数据服务
   - 输入 `zentao_bug_id`
   - 返回：
     - `users`
     - `builds`
     - `current_assigned_to`
     - 必要时 `bug.status`

3. Stage5 同步缓存控制

4. Stage5 排序统一后端化

### 七、你需要补的前端能力

1. 禅道 Bug 行操作区分本地 Bug / 禅道 Bug
2. 闭环说明输入
3. 重新激活弹窗
4. 重新指派弹窗或下拉
5. 可搜索指派人下拉
6. 删除强提示
7. 强制同步按钮与加载按钮的行为区分

### 八、验收标准

实现完成后，必须满足：

1. Stage5 禅道 Bug 可以单独重新指派，不改变状态。
2. 已关闭的禅道 Bug 可以重新激活，并选择指派给谁。
3. Stage5 闭环确认时，可以填写说明，并联动关闭禅道 Bug。
4. Stage5 禅道 Bug 删除时有强提示，并联动禅道删除。
5. 指派下拉的数据来自禅道网页动作页 JSON 中的 `users`，不是硬编码本地列表。
6. 指派下拉支持搜索筛选。
7. 整体测试默认按时间倒序。
8. 同步速度相较当前实现有明显改善。
9. 原有本地闭环记录、特派、审计能力不能丢。

### 九、禁止事项

1. 不要删除现有 Stage5 本地流程记录机制。
2. 不要把禅道 Bug 继续当作“可任意改编号”的本地对象。
3. 不要只在前端做排序。
4. 不要只给分析，不落代码。
5. 不要忽略失败回滚与错误提示。

### 十、输出要求

请直接修改代码，不要只给方案。  
完成后请输出：

1. 你改了哪些核心行为
2. 哪些接口是新增的
3. 哪些页面交互被修改了
4. 你如何验证
5. 还有哪些已知风险

---

## 备注

实现前请先完整阅读：

- [Stage5-禅道关闭激活调研.md](/E:/APPAUTO/APPAuto/docs/Stage5-禅道关闭激活调研.md)

这份文档是当前最完整的事实依据。
