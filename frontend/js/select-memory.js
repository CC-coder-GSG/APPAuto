/*
 * 下拉框选择记忆（SelectMemory）
 * ---------------------------------------------------------------------------
 * 让各工作页面的「版本 / 筛选」下拉框记住上一次的选择：刷新页面、重新登录后，
 * 下拉框自动回填到上次选中的选项，页面据此渲染内容。
 *
 * 设计要点：
 *  - 白名单驱动：只记忆工作页面的版本/筛选选择器，避免「新建Bug/新建用户」等
 *    表单录入型 select 被错误回填。
 *  - 按软件维度存储：键名带上 currentSoftwareId，切换软件后各自记忆各自的版本，
 *    且某软件保存的版本 id 不会污染另一软件（回填时再用 hasOption 兜底校验）。
 *  - 自动保存：捕获阶段全局监听 change，命中白名单即写入 localStorage。
 *  - 自动回填：
 *      · 关键的版本级联在 index.html 的 fillVersionSelects 中「同步」回填，
 *        保证后续 loadXxx() 读取到的就是记忆值；
 *      · 其余动态填充的下拉框由 MutationObserver 兜底回填（不派发 change，避免重复加载）。
 */
(function () {
  'use strict';

  const PREFIX = 'omniqa.selmem.';

  // 允许记忆的下拉框 id 白名单（工作页面的版本/筛选选择器）。
  const REMEMBER_IDS = new Set([
    // 我的工作台
    'mineDisplayMode', 'mineMajorSelect', 'mineMinorSelect',
    // 复测
    'retestDisplayMode', 'retestMajorSelect', 'retestMinorSelect',
    // 整体测试（全景大盘）
    's5MajorSelect', 's5MinorSelect',
    // 需求分配
    'assignMajorSelect', 'linkSourceMajorSelect',
    // 反馈
    'feedbackMajorSelect', 'feedbackMinorSelect', 'feedbackStatusSelect',
    'feedbackAssigneeSelect', 'feedbackSortBy', 'feedbackSortOrder', 'feedbackPageSize',
    // 报表
    'reportMajorSelect', 'reportUserSelect', 'vbMajorSelect',
    // 现场测试
    'fieldTestFilterMajor', 'fieldTestFilterMinor', 'fieldTestFilterPurpose',
    'fieldTestFilterTester', 'fieldTestPageSize',
    // 构建记录
    'buildRecordsMajorFilter', 'buildRecordsStatusFilter',
    // 用例中心
    'testcaseCenterStatus', 'testcaseCenterModule',
    // 动态/活动
    'activityRangeDays', 'activityTargetType', 'activityActorSelect',
    // 任务板
    'taskBoardAssigneeFilter', 'taskBoardStatusFilter',
  ]);

  function currentSoftwareId() {
    return Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);
  }

  function keyFor(id) {
    return `${PREFIX}${currentSoftwareId()}.${id}`;
  }

  function shouldRemember(el) {
    return !!el && el.tagName === 'SELECT' && !el.multiple && !!el.id && REMEMBER_IDS.has(el.id);
  }

  function hasOption(el, val) {
    return Array.prototype.some.call(el.options, (o) => o.value === val);
  }

  function save(el) {
    if (!shouldRemember(el)) return;
    try { localStorage.setItem(keyFor(el.id), el.value); } catch (_) { /* ignore */ }
  }

  function loadSaved(id) {
    try { return localStorage.getItem(keyFor(id)); } catch (_) { return null; }
  }

  // 把记忆值回填到下拉框（仅当该选项仍存在且与当前值不同）。返回是否发生了改变。
  function apply(elOrId) {
    const el = typeof elOrId === 'string' ? document.getElementById(elOrId) : elOrId;
    if (!shouldRemember(el)) return false;
    const saved = loadSaved(el.id);
    if (saved == null || saved === '') return false;
    if (!hasOption(el, saved)) return false;
    if (el.value === saved) return false;
    el.value = saved;
    return true;
  }

  // 批量回填，返回发生改变的 id 列表。
  function applyMany(ids) {
    const changed = [];
    (ids || []).forEach((id) => { if (apply(id)) changed.push(id); });
    return changed;
  }

  // 回填全部白名单下拉框（用于初始化时一次性恢复静态选项的筛选器）。
  function applyAll() {
    REMEMBER_IDS.forEach((id) => apply(id));
  }

  // 自动保存：捕获阶段监听全局 change，命中白名单即写入。
  document.addEventListener('change', (e) => {
    const el = e.target;
    if (el && el.tagName === 'SELECT') save(el);
  }, true);

  // 兜底：监听下拉框选项变化（动态填充后），自动回填记忆值。
  // 仅设置 value，不派发 change，避免与各 tab 自身的加载逻辑重复触发。
  let observer = null;
  function ensureObserver() {
    if (observer || typeof MutationObserver === 'undefined' || !document.body) return;
    observer = new MutationObserver((mutations) => {
      const seen = new Set();
      for (const m of mutations) {
        const el = m.target;
        if (el && el.tagName === 'SELECT' && el.id && !seen.has(el) && REMEMBER_IDS.has(el.id)) {
          seen.add(el);
          apply(el);
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }
  if (document.body) ensureObserver();
  else document.addEventListener('DOMContentLoaded', ensureObserver);

  window.SelectMemory = { apply, applyMany, applyAll, save, REMEMBER_IDS };
})();
