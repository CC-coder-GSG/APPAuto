# APPAuto QA Mobile Visual Language

## 设计方向

APPAuto QA 是测试管理工具，不做营销化首屏，也不做花哨装饰。视觉目标是：清爽、快速、可信、状态清晰。界面要像工程控制台一样可扫描，但比传统后台更轻、更现代。

核心关键词：

- 精准：信息密度高，但层级明确。
- 轻盈：浅色底、细边界、低阴影。
- 流动：Android 使用轻玻璃近似，iOS 使用真实 Liquid Glass。
- 语义：颜色用于表达状态，不用大面积单一蓝色铺满。

## 色彩系统

主色是 `primary #0F766E`，用于主要操作、当前导航、关键聚焦态。辅助强调色是 `accent #2563EB`，用于链接、预览、跨系统跳转。

状态色固定语义：

- 成功/闭环：`success #16A34A`
- 警告/待处理：`warning #B45309`
- 危险/失败/打回：`danger #E11D48`
- 信息/预览/链接：`accent #2563EB`

背景使用 `#F5F7FB` 到 `#F8FAFC` 的轻渐变。不要使用渐变球、发光 blob、暗色大面积背景。

## Android 表达

Android 使用 Material 3 作为基础：

- 顶部栏贴合背景，避免重阴影。
- 底部导航使用白色浮层，选中项使用 `primaryContainer` 胶囊。
- 卡片默认 8dp 圆角、1dp 边界、低或无阴影。
- 登录、弹窗、浮层可以使用半透明白色面板模拟玻璃感。
- 列表页保持高信息密度，筛选条永远在列表上方。

## iOS 液态玻璃

iOS 使用 SwiftUI Liquid Glass 风格，但只用于导航、登录面板、弹窗、浮动操作区。普通列表卡片保持清爽白色表面，避免整屏玻璃导致信息发灰。

推荐实现：

- 背景：浅色柔和渐变。
- 玻璃材质：`.ultraThinMaterial`
- tint：白色 42% 透明度。
- stroke：白色 62% 透明度。
- blur radius：22。
- saturation：1.35。
- corner radius：18。
- shadow：`0 18 48 rgba(15, 23, 42, 0.14)`。
- pressed scale：0.985。

## 字体与排版

移动端字阶：

- Headline：28 / 34 / Bold，用于登录页和少量品牌标题。
- Title：21 / 28 / Bold，用于页面主标题。
- Section：17 / 24 / Semibold，用于卡片标题和区块标题。
- Item：15 / 21 / Semibold，用于列表项标题。
- Body：14 / 21 / Regular，用于正文。
- Label：13 / 18 / Medium，用于按钮和状态。
- Caption：12 / 16 / Regular，用于补充元信息。

不要用负字距，不按屏幕宽度缩放字体。

## 组件规则

- `QaPanel`：普通内容卡片，白底、8dp 圆角、1dp 边界。
- `QaGlassPanel`：登录、弹窗、浮动区域的玻璃面板。Android 是近似，iOS 是真实 Liquid Glass。
- `QaPill`：状态、编号、短标签，999 圆角。
- 主按钮：50dp/50pt 高度，主色填充。
- 次按钮：白色或玻璃底，边界描线。
- 危险按钮：只在确认类操作里使用危险色。

## AI 实现约束

AI 生成新页面时优先复用 `design-tokens.json` 和 Android 的 `core/designsystem`。不要重新发明颜色、圆角和阴影。

页面结构优先级：

1. 顶部导航
2. 筛选/上下文面板
3. 状态摘要
4. 可操作列表
5. 弹窗和预览

操作按钮靠近被操作对象；不要把主要操作藏到页面底部之外。
