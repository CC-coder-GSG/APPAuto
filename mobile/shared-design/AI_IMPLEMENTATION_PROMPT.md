# APPAuto QA Mobile UI Implementation Prompt

你现在负责为“APPAuto QA 测试管理系统”的移动端实现统一视觉语言。项目是测试管理工具，不是营销产品，所以界面要专业、清爽、高效、可扫描，避免花哨装饰。

## 设计目标

视觉关键词：精准、轻盈、现代、工程感、状态清晰。

整体风格：

- 面向测试人员和项目管理人员的生产力工具。
- 信息密度要高，但层级清楚。
- 不做大面积渐变，不做发光球，不做装饰 blob。
- 不做营销式 hero 页面。
- UI 要像现代工程控制台，但比传统后台更轻、更精致。

## 跨端技术方向

Android：

- 使用 Material 3 作为基础。
- 使用浅色背景、细边界、低阴影。
- 用半透明白色面板模拟轻玻璃感。
- 普通列表卡片保持白底、8dp 圆角、1dp 边框。

iOS：

- 使用 SwiftUI。
- 支持 Liquid Glass 液态玻璃风格。
- 液态玻璃只用于导航栏、底部 tab、登录面板、弹窗、浮动操作区。
- 普通列表卡片不要全玻璃，避免信息发灰。

## 色彩系统

主色：

- `primary`: `#0F766E`
- `primaryDark`: `#115E59`
- `primaryContainer`: `#E6FFFA`
- `onPrimaryContainer`: `#0F766E`

辅助强调色：

- `accent`: `#2563EB`
- `accentContainer`: `#EFF6FF`

文本：

- `textStrong`: `#0F172A`
- `textDefault`: `#1E293B`
- `textMuted`: `#64748B`
- `textDisabled`: `#94A3B8`

表面：

- `background`: `#F5F7FB`
- `surfaceSubtle`: `#F8FAFC`
- `card`: `#FFFFFF`
- `cardPressed`: `#F1F5F9`
- `border`: `#E2E8F0`
- `borderStrong`: `#CBD5E1`

状态色：

- `success`: `#16A34A`
- `successContainer`: `#F0FDF4`
- `successBorder`: `#BBF7D0`
- `warning`: `#B45309`
- `warningContainer`: `#FFF7ED`
- `warningBorder`: `#FED7AA`
- `danger`: `#E11D48`
- `dangerContainer`: `#FFF1F2`
- `dangerBorder`: `#FFCCD5`
- `info`: `#2563EB`

颜色使用规则：

- `primary` 用于主要按钮、当前导航、关键聚焦态。
- `accent` 用于链接、预览、外部跳转。
- `success`、`warning`、`danger` 只表达状态，不要滥用成品牌色。
- 背景可以使用很轻的竖向渐变：`#EFFCF8 -> #F5F7FB -> #F8FAFC`。

## 字体系统

移动端字阶：

- `Headline`: `28sp / lineHeight 34 / Bold`
- `Title`: `21sp / lineHeight 28 / Bold`
- `SectionTitle`: `17sp / lineHeight 24 / Semibold`
- `ItemTitle`: `15sp / lineHeight 21 / Semibold`
- `Body`: `14sp / lineHeight 21 / Regular`
- `Label`: `13sp / lineHeight 18 / Medium`
- `Caption`: `12sp / lineHeight 16 / Regular`

规则：

- 不使用负字距。
- 不按屏幕宽度动态缩放字体。
- 紧凑工具区内不要使用 hero 级大字。
- 状态、编号、标签使用 `Label` 或 `Caption`。

## 圆角与间距

圆角：

- `sm`: `4`
- `md`: `8`
- `lg`: `8`
- `xl`: `12`
- `pill`: `999`

间距：

- `xxs`: `2`
- `xs`: `4`
- `sm`: `8`
- `md`: `12`
- `lg`: `16`
- `xl`: `24`
- `xxl`: `32`

规则：

- 普通卡片默认 8dp 圆角。
- 只有 pill 标签、分段控件、选中导航指示器使用全圆角。
- 不要到处使用大圆角卡片。

## Android 实现规则

使用 Jetpack Compose + Material 3。

建议组件：

- `QaScreenBackground`：全屏浅色柔和渐变背景。
- `QaPanel`：普通内容卡片，白底、8dp 圆角、1dp 边框。
- `QaGlassPanel`：登录、弹窗、浮层使用的半透明白色玻璃面板。
- `QaPill`：编号、状态、短标签。
- `QaMetric`：状态摘要数字。

Android 视觉：

- `TopAppBar` 背景与页面背景融合，不要重阴影。
- `BottomNavigation` 使用白色表面，选中项用 `primaryContainer` 胶囊。
- 表单按钮高度 50dp。
- 卡片默认无阴影或极低 tonal elevation。
- 筛选条应是一个独立的轻量面板，位于列表上方。

## iOS Liquid Glass 实现规则

Liquid Glass 参数建议：

- `material`: `ultraThinMaterial`
- `tint`: `rgba(255,255,255,0.42)`
- `stroke`: `rgba(255,255,255,0.62)`
- `innerHighlight`: `rgba(255,255,255,0.75)`
- `blurRadius`: `22`
- `saturation`: `1.35`
- `cornerRadius`: `18`
- `shadow`: `0 18 48 rgba(15, 23, 42, 0.14)`
- `pressedScale`: `0.985`

使用场景：

- 登录面板
- 顶部导航浮层
- 底部 TabBar
- 弹窗
- 浮动操作区

不要用于：

- 普通长列表每一项
- 大面积整屏内容
- 表格密集信息区域

## 页面结构规则

每个业务页面优先按这个结构组织：

1. 顶部导航
2. 筛选 / 上下文面板
3. 状态摘要
4. 可操作列表
5. 弹窗 / 预览

操作按钮必须靠近被操作对象，不要把关键操作藏太深。

## 组件风格

卡片：

- 白底
- 8dp 圆角
- 1dp border
- padding 16
- 不要卡片套卡片

标签：

- pill 圆角
- paddingX 8
- paddingY 4
- caption semibold
- 用于 ID、状态、版本号、短元信息

按钮：

- Primary：primary 填充，白字，高度 50
- Secondary：白底或玻璃底，border，primary 文本
- Danger：只用于确认打回、删除、退出等危险操作

空状态：

- 简洁文本即可。
- 不要插入大插画。
- 可以给出清晰下一步动作。

## 禁止项

- 不要使用装饰性渐变球、发光 blob、随机背景纹理。
- 不要使用营销站式 hero 结构。
- 不要大面积单一蓝色主题。
- 不要用 emoji 当功能图标，优先使用系统图标。
- 不要让文字溢出按钮或卡片。
- 不要卡片嵌套卡片。
- 不要为了酷炫牺牲信息密度和可读性。

## 当前 Android 项目落地位置

设计 token：

```text
mobile/shared-design/design-tokens.json
```

视觉规范：

```text
mobile/shared-design/VISUAL_LANGUAGE.md
```

Android 设计系统：

```text
mobile/android/app/src/main/java/site/geonest/qa/core/designsystem/
```

核心文件：

- `Color.kt`
- `Type.kt`
- `Dimens.kt`
- `Theme.kt`
- `Components.kt`

优先改造页面：

- `LoginScreen.kt`
- `MainScreen.kt`
- `WorkbenchFilterBar.kt`
- `WorkbenchScreen.kt`
- `RetestScreen.kt`
- `OverallTestScreen.kt`
- `ProfileScreen.kt`
