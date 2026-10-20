# SurveyMaster 自动化接入（本地验收版）

这次提供平台用例关联、执行记录、Jenkins 接入流水线、Robot 结果回调和独立的禅道结果客户端。当前完成本地部署与演练验收；远程平台和 Jenkins Job 尚未更新。提交或推送本目录不会自动部署平台、更新 Jenkins Job 或开启禅道结果回写。

## 自动化项目配置

- 项目名称自定义；Git 地址支持 HTTPS、`git@github.com:组织/仓库.git` 和 `ssh://git@github.com/组织/仓库.git`。平台保存脚本来源，实际拉取由 Jenkins 完成；Job 的仓库配置须与这里一致，SSH 私钥留在 Jenkins 凭据或执行节点。
- 禅道地址自动复用当前用户的禅道账号绑定，无需再次填写；后端也以该绑定为准。Jenkins 使用当前用户已有的账号绑定。
- Jenkins Job 从已配置的自动化测试视图读取，保留文件夹 Job 的完整名称。禅道产品、项目、研发执行、构建和测试单均从本人可访问的远程数据中下拉选择。禅道项目仅用于筛选研发执行；构建按执行和产品筛选，测试单按产品、执行和构建筛选。列表按远程分页完整读取，获取失败可点击“刷新远程选项”重试。
- 测试单是禅道中的一次测试批次，包含本轮执行的用例与执行历史。“专用”表示约定用于该自动化项目，不是额外的对象类型；可以选择已有且归属正确、尚未完成的测试单，但须先在禅道把所需用例关联进去。
- 创建项目时固定测试单可选；未配置时，每次运行从下拉框选择。当前运行协议要求测试单，因此真实测试和脚本演练都必须选择。启用禅道回写仍要求项目配置固定测试单；配置后，执行和回写均限定在该测试单中。产品为必选，研发执行和构建仍为可选。
- 配置表单的远程选项只读，不会新建禅道产品、项目、执行、测试单或构建，也不会触发 Jenkins 构建。首次正式执行与回写仍按下文验收顺序进行。

## 本地查看

- 平台：<http://127.0.0.1:8001/?automation_repository=1>，本地账号 `admin`，密码 `admin`。
- 进入「用例中心」，点击「自动化测试」分页查看用例关联与执行历史；「用例查询」分页保留普通用例筛选、列表和同步功能。已导入 3 条实际脚本，其中核心导航脚本关联禅道用例 #23466；另 2 条没有擅自关联业务用例。
- 查看历史记录中的「详情」，可以打开 Robot 报告与日志。状态明确标为「脚本演练」，结果不回写禅道。
- 再次演练：勾选 #23466；设备填 `LOCAL-DRYRUN`；APK 填 `local-script-validation-no-apk`；勾选「仅校验脚本」，提交。

本地执行适配器在 `127.0.0.1:18766` 提供 Jenkins 所需的队列、构建及归档接口，并实际执行 `Robot --dryrun`。它用于验证平台触发、计划冻结、脚本选择、回调、落库、页面和报告链接，不代表真实 Jenkins、Appium 或手机业务测试已通过。适配器拒绝非演练构建。

本地专用数据库为 `.codex-tmp/automation-local-validation/platform.db`；正常 `app_auto.db` 和 `.env` 未修改。后台禅道同步、企业微信推送、AI Webhook 和终端控制均在本地实例关闭。创建运行时会对真实禅道读取测试单、用例和版本，并生成独立认证令牌；不提交测试结论。

## 启动及验证

在项目根目录打开两个 PowerShell 窗口。首次需要初始化；重复初始化会保留自动化配置和历史，但会将本地 admin 密码设为上述演示密码。

```powershell
.\.venv\Scripts\python.exe integrations/local_validation/local_platform.py init
.\.venv\Scripts\python.exe integrations/local_validation/local_platform.py serve
```

另一个窗口运行：

```powershell
.\.venv\Scripts\python.exe integrations/local_validation/broker.py
```

两个窗口分别按 Ctrl+C 停止。这两个服务只监听本机地址。启动器不连接远程部署通道。

本机脚本副本位于 `.codex-tmp/surveymaster-integration-20261019`，分支 `feat/zentao-automation-integration`；原 `D:\SurveyMasterAutoTesting` 的业务脚本没有改动。本地适配器使用原项目虚拟环境运行 Robot，并读取副本中的脚本和忽略的环境配置。初始化从原项目 `conf/ztf.conf` 读取现有禅道账号，密码仅加密写入独立数据库；集成密钥也加密保存在本地运行文件中。可用环境变量 `LOCAL_ZTF_CONFIG`、`LOCAL_ROBOT_PYTHON` 指定其他配置和 Python。

以下验收命令会发布脚本目录、使用专用浏览器配置打开页面、从页面创建一次演练，保存截图和非敏感验证证据：

```powershell
.\.venv\Scripts\python.exe integrations/local_validation/verify.py
```

验证工具需要本机 Chrome 及平台环境中的 `websockets`。证据位于 `.codex-tmp/automation-local-validation/verification.json`、`local-browser.png`；报告位于 `builds/<编号>/results/`。运行目录含本地数据库和浏览器配置，不应提交到 Git。

## 脚本关联规则

每条 Robot 测试使用一个唯一的禅道用例标签：

```robotframework
*** Test Cases ***
Core Navigation Should Be Available
    [Tags]    zentao:23466    autoid:sm.core_navigation
    Given Prepare Core Navigation Smoke
    When Browse All Core Navigation Tabs
    Then Return To Project Home
```

目录生成器只做静态发现，不执行测试；隐藏文件不进入目录。平台只允许运行有映射、属于指定产品和测试单的用例。每次冻结 Git SHA、用例版本、测试单运行 ID、步骤和定义摘要，回调必须匹配同一次构建。实际仍从 `tests` 根目录运行，保留父套件的 Appium 初始化。

单个叶子步骤的禅道用例可接收整条 Robot 测试结论。多个叶子步骤必须在对应用户关键字上标记 `zentao-step:<用例ID>:<步骤ID>`，并完整匹配，防止将整体通过错误复制到每一步。重复执行同一映射步骤暂不自动合并。

PASS、FAIL、SKIP 和环境异常分别保留；父套件 Appium 初始化失败记录为环境异常。缺失、损坏或无法匹配计划的 XML 记录为环境异常，不伪造业务用例结果。脚本演练和 SKIP 不写入禅道。回调相同则幂等接收，内容不同返回 409。

## 禅道数据改动清单

此前真实接入准备已在用户授权的范围内创建以下专用数据，本次继续阶段没有新增业务数据，也未写入测试执行结论：

| 对象 | ID / 范围 | 改动 |
| --- | --- | --- |
| 产品 / 项目 / 研发执行 | 15 / 134 / 1289，s4030(V4.0.3.0) | 作为限定范围，没有修改原对象 |
| 专用用例 | 23466，版本 1 | 新建「[自动化接入验证] SurveyMaster 核心导航」，一个整体自动化检查步骤 |
| 专用测试单 | 1909 | 新建「自动化接入验证 · SurveyMaster-RF-Test」 |
| 用例关联 | run 6113 | 将新用例关联到新测试单 |
| 既有构建 | 4843 | 新测试单引用它，没有修改构建 |

专用数据保留用于后续联调，没有清理删除。测试单描述说明构建仅用于联调归属，不能作为对应版本的验收结论。服务器 Jenkins 最近一次已完成构建使用的是其他 APK 来源，后续实机执行必须重新确认 APK 来源、版本和设备，不能把不同版本的结果混为一谈。

## 后续接入真实 Jenkins 的顺序

以下内容已经准备为可审阅文件，本次未在服务器执行：

1. 将脚本副本的 3 个变更文件评审后合入自动化仓库：`ci/omniqa_robot.py`、`ci/ci_piplines` 和核心导航用例的关联标签。`robot/SurveyMaster.integration.patch` 是这 3 个文件相对于原 main 的补丁。当前用例 ID 是专用联调数据，正式用例应使用自己的准确 ID。
2. 更新测试管理平台代码，先备份数据库；启动时会创建 3 张新自动化表。保留现有 `APP_SECRET_KEY` / `APP_ZENTAO_BINDING_SECRET`，避免已存凭据无法解密。
3. 用户在平台绑定自己的 Jenkins、禅道账号，创建自动化项目。限定产品、执行、构建和专用测试单；平台初始关闭结果回写。
4. 保留 `SurveyMaster-RF-Test` 原有参数，按 `robot/jenkins-parameters.json` 添加新参数。布尔项使用 Boolean Parameter，其他项使用 String Parameter。设置 Jenkins Agent 能访问的 `OMNIQA_URL` 和平台项目 ID。`127.0.0.1:8001` 是本地演示地址，不能直接作为远程 Jenkins 的回调地址。
5. 将创建项目时仅显示一次的密钥存入 Jenkins **Secret Text** 凭据 `omniqa-surveymaster`，不设为明文构建参数。更新 Job 流水线为 `robot/SurveyMaster.Jenkinsfile`，或对应 Git 中的 `ci/ci_piplines`。原 Job 的参数选择逻辑和旧执行方式在 `OMNIQA_ENABLED=false` 时保留；集成执行不发送企业微信消息。
6. 先从 Jenkins 发起 `OMNIQA_ENABLED=true`、`OMNIQA_CATALOG_ONLY=true` 构建发布目录，再从平台以演练模式验证 SHA 固定和回调。平台发起时会填写运行 ID、用例 ID、测试单、设备和 SHA；不要手工篡改运行 ID。
7. 确认测试 APK 对应版本、设备可用、Appium 环境正常后，在专用用例上执行真实测试。报告继续归档在 **SurveyMaster-RF-Test** 下，平台链接至同一构建的 `results/report.html`、`log.html` 和截图。
8. 人工核对首次真实结果后，再启用项目的禅道回写。结果专用客户端独立获取 REST v1 token；现有业务接口继续用 v2。它只写执行结果，不使用可能覆盖用例定义的 `ciresults` 导入。写入后读取历史核对；网络中断导致提交状态不明时只做只读核对，不盲目重复提交。失败结果始终保留在平台。
9. 如需在禅道页面直接启动，安装服务端按钮/扩展，以禅道登录态校验产品及测试单权限，再由服务端调用 `POST /automation/integrations/{项目ID}/launch`。该接口已实现，需传 `X-Automation-Key`、运行参数和 `zentao_account`，并使用稳定的 `idempotency_key`。密钥必须留在禅道服务端，不能放浏览器脚本。返回的运行 ID 可组成平台链接 `/?automation_repository=<项目ID>&automation_run=<运行ID>`。**禅道页面扩展尚未安装和验收，本次没有将原生按钮作为已完成功能。**

## 验证范围

- 后端全量回归：698 通过，3 项持续 SSE 流测试因会持续等待而排除。后续补充字段兼容和父套件错误等相关测试后，针对自动化、Jenkins 和设备锁的 66 项测试全部通过。
- 浏览器验收：实际本地 Chrome 页面选择、提交、结果展示和报告可访问；相同回调不重复插入，篡改回调 409，无效密钥 401。
- Jenkins：在真实服务器完成 Declarative Pipeline 语法校验，未修改 Job、安装凭据或触发服务器构建。
- 禅道：真实读取并校验上述专用测试单、用例及关联；执行结果写入仅做协议模拟验证，未以演练结果冒充实机结果。

最后一次本地验收运行 ID 为 `4d3d695e-b2c5-4b4f-a429-b3e3ec19c51b`，本地构建 #2，脚本提交 `e769ee3951f882924876eb3d10d92c3baff6a109`。Robot 演练 1 条通过；平台已接收，禅道同步状态为跳过。验收后再次只读检查：用例定义摘要未变，run 6113 的结果记录仍为 0 条。

尚待验收的是服务器环境的真实 Jenkins → Appium → 手机执行、真实结果写入禅道，以及禅道原生入口。完成本地验收后才进入这些步骤。
