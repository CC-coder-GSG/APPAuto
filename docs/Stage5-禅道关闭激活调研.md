# Stage5 禅道关闭 / 激活 / 删除联动调研

更新时间：2026-04-16

## 1. 结论摘要

- 采用方案 A 可行。
- 当前 `加载全景大盘` 和 `从禅道同步当前版本Bug` 在“单个大版本”下功能高度重叠。
- 对于“从禅道同步来的 Bug”，后续可以把 `编辑 / 删除 / 闭环确认 / 重新激活` 都改成调用禅道 API，而不是只改本地库。
- 禅道实例已经实测确认支持：
  - `PUT /api.php/v1/bugs/{id}` 修改 Bug
  - `POST /api.php/v1/bugs/{id}/close` 关闭 Bug，并支持备注
  - `POST /api.php/v1/bugs/{id}/active` 重新激活 Bug，并支持重新指派和备注
  - `POST /api.php/v1/bugs/{id}/resolve` 解决 Bug，并支持重新指派
  - `DELETE /api.php/v1/bugs/{id}` 删除 Bug
- `DELETE` 在当前实例上的表现是“软删除”，Bug 仍可通过 API 读取，但 `deleted=true`。
- 暂未确认当前实例存在稳定可用的 v1 REST “恢复已删除 Bug”接口，因此删除功能虽然能开放给所有人，但必须配强提示。

## 2. 现在两个按钮的区别

现有实现位置：

- 前端入口：[frontend/index.html](/E:/APPAUTO/APPAuto/frontend/index.html:405)
- Stage5 加载逻辑：[frontend/js/tabs/stage5.js](/E:/APPAUTO/APPAuto/frontend/js/tabs/stage5.js:87)
- 显式同步逻辑：[frontend/js/tabs/stage5.js](/E:/APPAUTO/APPAuto/frontend/js/tabs/stage5.js:255)

当前行为：

### 2.1 `加载全景大盘`

- 选中具体大版本时，会先静默调用一次 `syncStage5FromZentao({ silent: true, reloadAfter: false })`
- 然后再请求 `/stage5/overview`
- 最后渲染分页后的当前页

也就是说，它现在其实是：

`同步当前版本Bug -> 读取本地库 -> 渲染大盘`

### 2.2 `从禅道同步当前版本Bug`

- 显式调用 `/stage5/sync-zentao-bugs`
- 弹出“远端多少条 / 新增多少条 / 更新多少条”的提示
- 然后再触发一次 `loadStage5({ syncBeforeLoad: false })`

也就是说，它现在其实是：

`同步当前版本Bug -> 给出同步结果提示 -> 加载大盘`

### 2.3 实际差异

在“单个大版本”场景下，这两个按钮现在的核心效果几乎一样，区别只有：

- `加载全景大盘`：静默同步
- `从禅道同步当前版本Bug`：显式同步并提示结果

因此采用方案 A 时，建议保留两者，但要明确产品语义：

- `加载全景大盘`：主入口，默认先同步后加载
- `从禅道同步当前版本Bug`：手动刷新入口，强调“强制从禅道刷新一次”

## 3. 本次对禅道实例的实际核验

核验目标实例：

- 地址：`http://192.168.2.148:81/zentao`
- 账号：`chenwenbo`
- 时间：2026-04-16

### 3.1 基础连通

- `POST /api.php/v1/tokens` 可正常获取 token
- 当前账号可访问 `project=134`
- 当前账号可访问 `execution=1647`
- `execution=1647` 对应名称为 `s4031`

### 3.2 `s4031` 当前 Bug 规模

实测接口：

- `GET /api.php/v1/executions/1647/bugs?limit=10`

返回结果里 `total=80`，说明当前实例里 `s4031` 的 Bug 数量为 80。

### 3.3 `s4031` 当前构建数量

实测接口：

- `GET /api.php/v1/executions/1647/builds?limit=50`

返回结果里 `total=16`。

### 3.4 `Bug 29902` 的受控验证结果

本次按你的允许，使用 `Bug 29902` 做了链路测试。

测试前状态：

- `status = resolved`
- `resolution = fixed`
- `assignedTo = guliangyan`
- `resolvedBuild = 4505`
- `openedBuild = 4504`

#### 3.4.1 关闭测试

请求：

- `POST /api.php/v1/bugs/29902/close`

请求体：

```json
{
  "comment": "stage5 close probe 2026-04-16"
}
```

结果：

- 返回 200
- Bug 状态变为 `closed`
- `closedBy = chenwenbo`
- `closedDate` 被写入

#### 3.4.2 重新激活并重新指派测试

请求：

- `POST /api.php/v1/bugs/29902/active`

请求体：

```json
{
  "assignedTo": "chenwenbo",
  "openedBuild": ["4504"],
  "comment": "stage5 active probe 2026-04-16"
}
```

结果：

- 返回 200
- Bug 状态变为 `active`
- `resolution` 被清空
- `assignedTo` 成功切换为 `chenwenbo`
- `closedBy / closedDate / resolvedBy / resolvedDate` 被清空

#### 3.4.3 恢复为已解决状态

请求：

- `POST /api.php/v1/bugs/29902/resolve`

请求体：

```json
{
  "resolution": "fixed",
  "resolvedBuild": "4505",
  "assignedTo": "guliangyan",
  "comment": "restore after api probe 2026-04-16"
}
```

结果：

- 返回 200
- Bug 状态恢复为 `resolved`
- `assignedTo` 恢复为 `guliangyan`
- `resolvedBuild` 恢复为 `4505`

注意：

- 该 Bug 虽然被恢复到了 `resolved`，但 `resolvedBy` 和 `resolvedDate` 已更新为本次测试动作的操作者与时间。
- 这说明禅道写接口会真实落库，不是只校验参数。

## 4. 与需求直接相关的接口结论

### 4.1 修改 Bug

可行。

接口：

- `PUT /api.php/v1/bugs/{bugID}`

适合做：

- 标题修改
- 严重程度
- 优先级
- 影响版本
- 所属执行
- 复现步骤

不适合继续做：

- 本地直接修改 `bug_id = b#xxxx`

原因：

- 对于禅道同步 Bug，`b#29902` 本质上是远端对象标识，不应允许前端像本地自由 Bug 一样任意改号。

### 4.2 删除 Bug

可行，但风险最高。

接口：

- `DELETE /api.php/v1/bugs/{bugID}`

实测表现：

- 返回 `{"message":"success"}`
- 之后 `GET /api.php/v1/bugs/{bugID}` 仍能查到该 Bug
- 但字段会变成 `deleted=true`

结论：

- 当前实例的删除是软删除
- 但当前未确认 v1 REST 下存在稳定可用的恢复接口

### 4.3 闭环确认 -> 关闭 Bug

可行。

接口：

- `POST /api.php/v1/bugs/{bugID}/close`

支持参数：

- `comment`

这与“闭环确认时输入确认信息”的需求完全匹配。

### 4.4 关闭后重新激活并重新指派

可行。

接口：

- `POST /api.php/v1/bugs/{bugID}/active`

支持参数：

- `assignedTo`
- `openedBuild`
- `comment`

这与“像禅道一样，关闭的 Bug 可以重新激活并选择指派给谁”的需求完全匹配。

### 4.5 重新解决

可行。

接口：

- `POST /api.php/v1/bugs/{bugID}/resolve`

支持参数：

- `resolution`
- `resolvedBuild`
- `assignedTo`
- `comment`

这个接口在系统里主要适合做“恢复测试环境时的补救操作”，不是 Stage5 主流程必需项。

### 4.6 单独重新指派

可行。

接口：

- `POST /api.php/v1/bugs/{bugID}/assign`

本次用 `29902` 实测：

- 将指派人改为 `chenwenbo`
- Bug 状态仍保持 `resolved`
- `resolution` 仍保持 `fixed`
- 然后又恢复指派人为 `guliangyan`

结论：

- `assign` 是独立动作
- 不会像 `active` 那样清空关闭/解决状态
- 所以后续系统里除了“重新激活并选择指派人”，也可以支持“仅重新指派”

## 5. 方案 A 下的推荐产品设计

## 5.1 按钮设计

保留两个按钮，但职责重新表述：

- `加载全景大盘`
  - 默认行为：同步当前版本 -> 加载大盘
  - 这是主入口
- `从禅道同步当前版本Bug`
  - 默认行为：只强调“强制刷新远端数据”
  - 同步完成后再刷新当前列表
  - 弹出同步数量提示

建议文案：

- `加载全景大盘`
- `强制同步禅道并刷新`

这样用户会更容易理解两者关系。

## 5.2 Stage5 行操作如何改

### 禅道同步 Bug

判定条件：

- `zentao_bug_id` 非空

建议行为：

- `编辑` -> 改为“编辑禅道 Bug”
- `删除` -> 改为“删除禅道 Bug”
- `闭环确认` -> 成功后关闭禅道 Bug
- 新增 `重新激活` 按钮 -> 只对 `closed` 状态显示

### 本地自由 Bug

判定条件：

- `zentao_bug_id` 为空

建议行为：

- 继续保留现在的本地编辑/删除逻辑
- 不强制要求禅道联动

## 5.3 闭环确认设计

推荐保留现在的 `BugStage5Record` 本地记录模型，不要丢。

原因：

- 禅道只负责 Bug 对象状态
- 本系统负责测试流程记录
- “谁在什么小版本做了闭环确认”是本系统的重要过程数据

推荐流程：

1. 测试人员点击“保存记录”
2. 如果勾选“我的闭环确认”，弹出确认框或弹窗
3. 要求填写“闭环说明 / 验证说明”
4. 本地先写入 `BugStage5Record`
5. 若该 Bug 有 `zentao_bug_id`，再调用 `POST /bugs/{id}/close`
6. 成功后刷新当前页

推荐新增字段：

- 本地 `stage5_close_comment`
- 或挂在 `BugStage5Record.comment`

原因：

- 即使禅道备注写成功，本地也应该保留一份，便于系统内审计和时间线展示

## 5.4 重新激活设计

当 Bug 在禅道中为 `closed` 时：

- 行内显示 `重新激活` 按钮
- 点击后弹出表单

表单建议字段：

- `重新指派给`
- `影响版本`
- `重新激活说明`

提交时调用：

```text
POST /api.php/v1/bugs/{id}/active
```

请求体建议：

```json
{
  "assignedTo": "chenwenbo",
  "openedBuild": ["4504"],
  "comment": "回归未通过，重新激活"
}
```

本地联动：

- 将本地 `closed=false`
- 清理或追加新的 Stage5 过程记录
- 写审计日志：`stage5.reactivate`

## 5.5 删除设计

你要求删除对所有人开放，可以做，但必须加重提示。

推荐交互：

- 点击 `删除`
- 弹出明确风险提示框

建议提示文案：

`将删除禅道中的该 Bug。当前实例删除后会进入 deleted 状态，暂未确认存在稳定可用的 REST 恢复接口。是否继续？`

推荐按钮：

- `取消`
- `确认删除`

推荐技术策略：

- 远端执行 `DELETE /bugs/{id}`
- 本地不要直接硬删记录
- 本地将该记录标记为“已在禅道删除”并默认从主列表隐藏

为什么不建议本地直接删掉：

- 需要保留闭环记录
- 需要保留审计日志
- 需要保留谁删的、什么时候删的
- 一旦远端恢复困难，本地至少还能追溯

## 6. 关键边界与风险

### 6.1 不是所有 Bug 都应该允许关闭

禅道正常流程是：

`active -> resolved -> closed`

所以 Stage5 的“闭环确认”如果要联动禅道关闭，必须先判断远端状态：

- `resolved`：允许关闭
- `closed`：只写本地闭环记录，不重复关闭
- `active`：不允许直接关闭，提示“禅道中尚未解决，不能闭环关闭”

否则会破坏禅道流程。

### 6.2 重新激活的指派人是 account，不是本地 user_id

实测 `POST /bugs/{id}/active` 的 `assignedTo` 使用的是禅道 `account`，例如：

- `chenwenbo`
- `guliangyan`

不是数字型用户 ID。

因此实现时必须做映射。

推荐来源：

- 直接用本系统里已绑定禅道账号的用户
- 或者维护 `本地用户 -> zentao_account` 映射

### 6.3 用户列表接口权限可能受限

实测：

- `GET /api.php/v1/users?limit=20` 对当前账号返回权限错误
- 但 `GET /api.php/v1/users/407`、`GET /api.php/v1/users/419` 可读

这意味着：

- 不适合在前端直接依赖“禅道全量用户列表”来填充指派下拉框
- 更适合使用本系统本地用户列表，并要求用户已绑定禅道账号

### 6.4 删除对所有人开放的风险高于关闭和激活

关闭和激活都有明确的状态回退路径。

删除虽然当前看是软删除，但：

- 当前未确认 v1 REST 恢复接口
- 一旦误删，恢复成本明显更高

所以即使对所有人开放，也建议：

- 二次确认
- 强提示
- 写本地删除审计
- 默认只隐藏，不直接从本地彻底清空

## 7. 关于“重新指派给谁”的进一步调研

你的新要求是：

- 重新激活时，指派人列表尽量从禅道 API 获取
- 前端使用下拉列表
- 下拉列表支持输入筛选

这个需求从产品形态上是完全合理的，但在当前实例上存在一个权限前提。

### 7.1 禅道 API 对“指派给谁”使用什么字段

本次实测确认：

- `POST /api.php/v1/bugs/{id}/active`
- `POST /api.php/v1/bugs/{id}/resolve`

这两个接口里的 `assignedTo` 都使用禅道用户的 `account` 字段，而不是数字 ID。

例如：

```json
{
  "assignedTo": "chenwenbo"
}
```

另外，本次进一步实测确认：

- `POST /api.php/v1/bugs/29902/assign`

是可用的，且会真实修改 Bug 的指派人。

这说明：

- 当前账号 `chenwenbo` 具备“执行指派动作”的业务权限
- 我上一次把“拿不到 v1 用户列表”直接等同于“账号没有可指派人权限”，这个结论不准确
- 正确结论应当是：
  - `指派动作权限` 存在
  - 但 `候选人列表接口` 的获取方式需要进一步区分版本和实例实现

### 7.2 候选人列表接口重新核验

#### v1 用户列表

本次实测：

- `GET /api.php/v1/users`
- `GET /api.php/v1/users?browse=all&page=1`

返回都是：

```text
error: no company-browse priv.
```

说明：

- 这个账号不能通过 v1 用户列表接口读取全量用户

#### v2 用户列表

本次实测：

- `GET /api.php/v2/users`
- `GET /api.php/v2/users?browseType=all&pageID=1&recPerPage=20`

返回状态是 `200`，但实际响应体是服务端 Fatal Error：

```text
Class 'usersEntry' not found
```

说明：

- 当前实例的 `v2/users` 在服务端实现上存在异常
- 不是账号权限问题，而是实例端点本身不可用

#### 网页动作页的真实数据来源

这是本次重新调研最关键的修正点。

按你的要求，我继续沿网页页面本身追查后，确认了：

- 禅道网页的动作页存在对应的 `.json` 页面数据接口
- 指派候选人并不一定来自单独的 `/users` 列表接口
- 至少在当前实例上，`激活 / 解决 / 查看 / 关闭` 这些 Bug 页面自己的 JSON 数据里，就已经包含了 `users` 字典

本次实测：

- `GET /bug-activate-29925.json`
- `GET /bug-resolve-29925.json`
- `GET /bug-view-29925.json`
- `GET /bug-close-29925.json`

都返回了页面 JSON 数据。

其中：

- `bug-activate-29925.json` 中 `users` 数量为 `267`
- `bug-resolve-29925.json` 中 `users` 数量为 `267`
- `bug-view-29925.json` 中 `users` 数量为 `268`
- `bug-close-29925.json` 中 `users` 数量为 `268`

而且这些 `users` 字典里已经包含了：

- `chenwenbo`
- `guliangyan`
- `zhangchao`
- `xujing`

说明：

- 当前账号在网页层面确实能拿到指派候选人
- 我的上一版结论“拿不到稳定候选人来源”不准确，需要修正

#### `users` 字段的实际格式

以 `bug-activate-29925.json` 为例，`users` 是：

```json
{
  "chenwenbo": "C:陈文博",
  "guliangyan": "G:顾靓燕",
  "zhangchao": "Z:张超"
}
```

以 `bug-view-29925.json` 为例，`users` 是：

```json
{
  "chenwenbo": "陈文博",
  "guliangyan": "顾靓燕",
  "zhangchao": "张超"
}
```

结论：

- `key` 就是后续提交给 `assignedTo` 的禅道账号 `account`
- `value` 是页面显示名称
- 在动作页里，显示值前面常带首字母前缀，例如 `C:陈文博`
- 这已经足够支撑“可搜索下拉框”

#### 动作接口是否能返回候选人元数据

本次还探测了：

- `GET /api.php/v1/bugs/29902/assign`
- `GET /api.php/v1/bugs/29902/active`
- `GET /api.php/v1/bugs/29902/resolve`
- `GET /api.php/v1/bugs/29902/close`

这些接口都返回 `200`，但响应体长度为 `0`。

说明：

- 当前实例没有通过这些 REST 动作接口直接暴露表单元数据
- 不能指望通过 `GET /bugs/{id}/assign` 之类接口拿到候选人列表

#### 单用户详情

本次实测：

- `GET /api.php/v1/users/407`
- `GET /api.php/v1/users/419`

是可读的。

说明：

- 当前账号对“已知用户 ID 的详情读取”没有问题

### 7.3 正确结论

基于这次重新核验，应该把结论改成下面这样：

- 当前账号**确实有指派权限**
- 当前账号**也确实能在禅道网页里看到可指派人列表**
- 当前实例的“标准 `/users` 接口”并不稳定
- 但网页动作页自己的 `.json` 页面数据里，已经稳定包含了 `users` 候选字典

换句话说：

- `网页里能显示候选人`
- 并且
- `网页页签 JSON 本身就带了候选人列表`

真正需要区分的是：

- `标准用户列表接口`
- `网页动作页内嵌的候选人数据`

### 7.4 对系统实现的含义

如果你的要求是：

`系统里重新激活时，指派下拉的数据必须来自禅道`

那现在最推荐的路径已经变成：

#### 路径 1：推荐

直接复用禅道网页动作页对应的 `.json` 数据源。

建议映射：

- 重新激活时：`GET /bug-activate-{bugID}.json`
- 重新解决时：`GET /bug-resolve-{bugID}.json`
- 仅查看或兜底时：`GET /bug-view-{bugID}.json`

从返回值中读取：

- `users`
- `builds`
- `bug.assignedTo`

优点：

- 这就是当前网页自己实际使用的数据
- 不依赖 `/v1/users` 权限
- 不受 `/v2/users` 端点异常影响
- 候选人集合更贴近当前 Bug 动作上下文

缺点：

- 是页面级 JSON，不是纯资源型列表接口

#### 路径 2：次选

由后端维护一份“禅道可指派用户缓存表”。

来源方式：

- 后端定时从动作页 JSON 或其他可用来源同步用户
- 或由管理员手工刷新
- 或基于历史已见到的用户账号逐步补齐

优点：

- 前端可以稳定做可搜索下拉
- 不被当前实例 `/users` 端点卡住
- 仍然以禅道账号为主键

缺点：

- 严格来说不是每次实时拉禅道

#### 路径 3：备用

等禅道实例修复后，再切回标准 `/users` 接口。

### 7.5 UI 设计建议

如果后续能取到用户列表，推荐做成：

- 一个可搜索下拉框，而不是普通 `select`
- 显示格式：`真实姓名（account）`
- 输入关键字时同时按：
  - `realname`
  - `account`
  - `pinyin`
  过滤

推荐返回字段：

- `id`
- `account`
- `realname`
- `pinyin`

推荐接口优先级应修改为：

1. `GET /bug-activate-{bugID}.json` 中的 `users`
2. `GET /bug-resolve-{bugID}.json` 中的 `users`
3. `GET /bug-view-{bugID}.json` 中的 `users`
4. 如果页面 JSON 不可用，再退回本地缓存候选表

推荐前端展示数据：

```json
{
  "value": "chenwenbo",
  "label": "陈文博（chenwenbo）",
  "account": "chenwenbo",
  "realname": "陈文博",
  "sort_hint": "C"
}
```

这样用户可以：

- 直接点选
- 输入姓名筛选
- 输入工号/账号筛选
- 输入首字母筛选

### 7.6 我对上一版报告的修正

上一版报告里“最好配一个有 `company-browse` 权限的账号或服务账号”这个建议，只覆盖了 `v1/users` 这一条路，不够完整。

修正后的更准确说法是：

- `v1/users`：当前账号确实没有列表权限
- `v2/users`：当前实例端点报错
- `assign/active/resolve`：动作接口可用，但不直接返回动作元数据
- `bug-activate-{id}.json / bug-resolve-{id}.json / bug-view-{id}.json`：页面 JSON 本身已经包含 `users`

因此这次修正后的结论是：

- 你的账号不仅能指派
- 也能通过网页数据源拿到候选人
- 后续系统完全可以按网页实际方式，直接复用动作页 JSON 中的 `users` 来做可搜索下拉

## 8. 关于整体测试默认按时间倒序排序

### 8.1 当前现状

当前 Stage5 总览没有显式排序。

位置：

- [app/services/stage5_service.py](/E:/APPAUTO/APPAuto/app/services/stage5_service.py:59)
- [app/services/stage5_service.py](/E:/APPAUTO/APPAuto/app/services/stage5_service.py:79)

当前查询：

- 只是 `.filter(...)`
- 没有 `.order_by(...)`

所以现在返回顺序主要取决于数据库默认顺序，通常接近插入顺序，但不应被当成稳定排序规则。

### 8.2 可用的时间字段

`BugTracking` 模型里已有：

- `created_at`
- `updated_at`

位置：

- [app/models/bug.py](/E:/APPAUTO/APPAuto/app/models/bug.py:70)

### 8.3 推荐排序规则

默认建议：

- 一级排序：`updated_at desc`
- 二级排序：`id desc`

原因：

- 禅道同步回来有更新的 Bug 会自然靠前
- 新增 / 编辑 / 闭环 / 重新激活过的 Bug 也会靠前
- 用户体感更符合“最新问题先看”

如果你更想强调“新同步进来的 Bug”，也可以用：

- 一级排序：`created_at desc`
- 二级排序：`id desc`

但从整体测试的管理视角，我更推荐 `updated_at desc`。

### 8.4 前端是否还需要二次排序

不建议。

建议直接在后端 `overview` 查询时排序，理由：

- 分页以后必须以后端排序为准
- 避免前端和后端排序规则不一致
- 未来如果改成服务端分页，后端排序可以直接复用

## 9. 关于“同步禅道信息偏慢”的根因与解决方案

这部分已经做了实际量化。

### 9.1 当前慢在哪里

当前“加载全景大盘”在单个大版本下，会先自动做一次完整禅道同步：

- 位置：[frontend/js/tabs/stage5.js](/E:/APPAUTO/APPAuto/frontend/js/tabs/stage5.js:87)

另外，大版本下拉变化时，如果当前正处于 Stage5 页签，也会自动触发一次 `loadStage5()`：

- 位置：[frontend/index.html](/E:/APPAUTO/APPAuto/frontend/index.html:1589)

而后端的 Stage5 同步流程是：

1. 拉一次 execution 的 bug 列表
2. 拉一次 execution 的 build 列表
3. 对每个 build 再拉一次 bug 列表
4. 每条 Bug 在本地逐条做 upsert

核心位置：

- [app/services/stage5_service.py](/E:/APPAUTO/APPAuto/app/services/stage5_service.py:245)
- [app/services/stage5_service.py](/E:/APPAUTO/APPAuto/app/services/stage5_service.py:360)
- [app/services/stage5_service.py](/E:/APPAUTO/APPAuto/app/services/stage5_service.py:412)

### 9.2 本次对 `s4031` 的实测耗时

实测对象：

- `execution = 1647`
- `build 数量 = 16`

网络层耗时实测：

- `GET /executions/1647/bugs?limit=100&page=1`：约 `1.095s`
- `GET /executions/1647/builds?limit=200`：约 `0.659s`
- 16 个 `GET /builds/{id}/bugs?...` 串行总耗时：约 `5.448s`

仅这三部分串起来，远端网络层就约：

`7.2s`

这还没有算：

- 本地数据库逐条 upsert
- 返回 overview 后前端渲染
- 当前页再做禅道 hydrate

### 9.3 当前页禅道 hydrate 也会增加体感延迟

当前页的禅道补信息，会对页面中的 Bug 逐条调用 `GET /bugs/{id}`。

本次按 50 条可见 Bug 做了测量：

- 50 个 Bug 详情串行总耗时：约 `36.725s`
- 单条平均约 `0.734s`

不过这里前端已经不是纯串行：

- 后端 hydrate 使用了并发
- 并发上限是 `10`

位置：

- [app/api/routes/zentao_hydrate.py](/E:/APPAUTO/APPAuto/app/api/routes/zentao_hydrate.py:58)

所以实际当前页 hydrate 的体感大概是：

- `36.7 / 10 ≈ 3.7s` 左右

如果页面条数设置得比较大，例如 100 条，体感就会继续变慢。

### 9.4 根因总结

当前慢，不是单一原因，而是三层叠加：

#### 原因 1：每次加载大盘都会先做一次完整远端同步

也就是说，用户想“看一眼页面”，实际上也被迫做了一次重同步。

#### 原因 2：build bug 拉取是串行的

`s4031` 下有 16 个 build，就意味着至少 16 次串行请求。

#### 原因 3：本地 upsert 是逐条查询、逐条写入

同步代码里每个 Bug 都要单独查本地是否存在，再决定创建或更新。

#### 原因 4：页面渲染后当前页还要做一轮 hydrate

即使大盘主体数据已经出来了，标题、状态等禅道补充信息仍然要二次补齐。

### 9.5 推荐优化方案

#### 优化 1：把“加载”和“强制同步”节奏拆开

虽然方案 A 保留“加载时先同步”的产品语义，但技术上建议增加同步缓存策略。

推荐：

- 最近 3 到 5 分钟内同步过的同一大版本，再点 `加载全景大盘` 时只加载本地
- 用户点击 `强制同步禅道并刷新` 时，才忽略缓存强制拉远端

这样能明显降低重复等待。

#### 优化 2：build bug 列表改成并发抓取

这是当前最直接的提速点。

建议：

- 并发抓取所有 `builds/{id}/bugs`
- 并发数控制在 `4 ~ 8`

按本次 `s4031` 的数据估算：

- 串行约 `5.45s`
- 并发后理论上可以压到 `1.2s ~ 2s`

#### 优化 3：本地 upsert 改成批量预取

当前是“每个 Bug 一次数据库查询”。

建议：

- 先把本次禅道返回的所有 `zentao_bug_id` 收集起来
- 一次性查出本地已有记录
- 构造成 map
- 再在内存里判断创建 / 更新

这样能显著减少数据库 round-trip。

#### 优化 4：只对当前页 hydrate，不要把 page size 设太大

现在虽然已经是按页 hydrate，但如果用户把每页设置到 200 或 500，仍然会明显变慢。

建议：

- 默认每页 `20`
- 常用选项 `20 / 50 / 100`
- 超过 `100` 时给轻提示：`每页过多会影响禅道信息补全速度`

#### 优化 5：优先使用本地已缓存字段渲染

对已同步 Bug：

- 标题优先用本地 `zentao_bug_title`
- URL 优先用本地 `zentao_bug_url`
- 只有本地缺失时才请求 hydrate

这样可以减少当前页额外的 `GET /bugs/{id}` 数量。

#### 优化 6：增加“同步时间”提示

在按钮附近展示：

- `最近同步时间`
- `上次同步耗时`
- `上次同步条数`

这样用户能区分：

- 页面慢是因为“正在强制同步”
- 还是“只是在加载大盘”

### 9.6 最推荐的最终节奏

推荐最终交互：

- `加载全景大盘`
  - 默认读取本地
  - 若超过同步缓存时间则自动轻量同步
- `强制同步禅道并刷新`
  - 无条件全量同步
  - 完成后刷新当前大盘

这样既保留方案 A 的体验，也能解决“每点一次都等很久”的问题。

## 10. 推荐实施顺序

1. 先把 Stage5 行操作拆成“禅道 Bug”和“本地 Bug”两条逻辑。  
2. 再把“闭环确认 + 闭环说明”联动到 `POST /bugs/{id}/close`。  
3. 然后增加“重新激活”弹窗，联动 `POST /bugs/{id}/active`。  
4. 同时补上“禅道用户候选列表 + 可筛选指派框”。  
5. 把 Stage5 默认排序改为 `updated_at desc`。  
6. 最后再开放“删除禅道 Bug”，并做好强提示和本地归档。  
7. 再做并发同步和同步缓存优化，解决加载偏慢问题。  

## 11. 推荐最终界面行为

### 对 resolved 的禅道 Bug

- `编辑`
- `删除`
- `闭环确认`

### 对 closed 的禅道 Bug

- `查看`
- `重新激活`
- `删除`

### 对 active 的禅道 Bug

- `编辑`
- `删除`
- `闭环确认` 按钮置灰或提示“未解决不可关闭”

### 对本地自由 Bug

- 继续走本地编辑 / 删除 / 闭环逻辑

## 12. 关于“重新激活”弹窗的推荐字段

为了同时满足你的需求，`重新激活` 弹窗建议包含：

- `重新指派给`
- `影响版本`
- `重新激活说明`

其中：

- `重新指派给`
  - 使用可搜索下拉框
  - 数据优先来自禅道 `/users`
- `影响版本`
  - 默认带出当前 Bug 的 `openedBuild`
  - 允许切换
- `重新激活说明`
  - 必填更稳妥
  - 直接写入禅道 `comment`

## 13. 参考来源

### 官方文档

- 获取用户列表（v1）：https://www.zentao.net/book/api/666.mhtml
- 获取用户列表（v2）：https://www.zentao.net/book/api/2145.html
- 修改 Bug（官方）：https://www.zentao.net/book/api/723.html
- 关闭 Bug（官方）：https://www.zentao.net/book/api/1121.html
- 激活 Bug（官方）：https://www.zentao.net/book/api/1142.mhtml
- 解决 Bug（官方）：https://www.zentao.net/book/api/1181.html
- 删除 Bug（官方）：https://www.zentao.net/book/api/724.html
- 禅道 Bug 流程说明（官方）：https://www.zentao.net/book/zentaopms/898.mhtml
- 激活 Bug 使用说明（官方）：https://www.zentao.net/book/zentaopmshelp/84.mhtml

### 本地代码定位

- Stage5 加载逻辑：[frontend/js/tabs/stage5.js](/E:/APPAUTO/APPAuto/frontend/js/tabs/stage5.js:87)
- Stage5 显式同步逻辑：[frontend/js/tabs/stage5.js](/E:/APPAUTO/APPAuto/frontend/js/tabs/stage5.js:255)
- 现有本地 Bug 编辑 / 删除接口：[app/api/routes/bugs.py](/E:/APPAUTO/APPAuto/app/api/routes/bugs.py:55)
- 现有本地 Bug 编辑 / 删除服务：[app/services/bug_service.py](/E:/APPAUTO/APPAuto/app/services/bug_service.py:52)
- 现有 Stage5 闭环记录逻辑：[app/services/stage5_service.py](/E:/APPAUTO/APPAuto/app/services/stage5_service.py:151)
- 当前 Stage5 总览查询：[app/services/stage5_service.py](/E:/APPAUTO/APPAuto/app/services/stage5_service.py:21)
- 当前 Stage5 同步主逻辑：[app/services/stage5_service.py](/E:/APPAUTO/APPAuto/app/services/stage5_service.py:245)
- 当前 build bug 拉取逻辑：[app/services/stage5_service.py](/E:/APPAUTO/APPAuto/app/services/stage5_service.py:360)
- 当前禅道 hydrate 并发设置：[app/api/routes/zentao_hydrate.py](/E:/APPAUTO/APPAuto/app/api/routes/zentao_hydrate.py:58)

## 14. 备注

本次调研中，对 `Bug 29902` 做了真实接口测试，并已将其恢复回 `resolved` 状态；但 `resolvedBy` / `resolvedDate` 已更新为本次测试动作的操作者与时间，这是禅道真实写入行为导致的正常结果。
