import { prepareOperationModal } from '../components/zentao-operation.js';
import { operateTask } from '../components/task-operation.js';
import { api } from '../api.js';
import { escapeHtml, renderPreviewBtn, renderTaskStoryChangeBadge } from '../utils.js?v=20261014-2';
import { showLoading, hideLoading, setLoadingText } from '../components/common.js';

// 任务工作台：展示当前账号名下的禅道任务。
//  - 关联本平台需求的任务：不显示禅道操作按钮，只提供「跳转到需求工作台对应位置」。
//  - 未关联需求的任务：提供开始 / 设置工时 / 完成 / 关闭 / 重新激活等禅道操作。
// 卡片外观与需求工作台的需求卡片保持一致（<details.mine-req-card>：可折叠、预览、
// 跳转、彩色边框流光特效）；2026-11-24 排版 v2 共用 mrc-* 结构，状态筛选改为顶部统计按钮。

let taskWorkbenchData = [];
// 记住已展开的任务卡片，刷新/操作后重新渲染时保持展开，避免每次都被收起。
const openTaskIds = new Set();
// 状态筛选（all / wait / doing / pause / done / closed，closed 含 cancel）。
let statusFilter = 'all';
// 隐藏已关闭/已取消的任务；状态筛选显式选「已关闭」时不生效（明确要看时以筛选为准）。
let hideClosed = false;
// 批量关闭：勾选的任务 id。
const selectedTaskIds = new Set();

const TASK_STATUS_ZH = { wait: '未开始', doing: '进行中', changed: '进行中', done: '已完成', pause: '已暂停', cancel: '已取消', closed: '已关闭' };

function taskMatchesFilter(t) {
  const s = String(t.status || '');
  if (hideClosed && statusFilter !== 'closed' && (s === 'closed' || s === 'cancel')) return false;
  if (statusFilter === 'all') return true;
  if (statusFilter === 'closed') return s === 'closed' || s === 'cancel';
  if (statusFilter === 'doing') return s === 'doing' || s === 'changed';
  return s === statusFilter;
}

// 可参与批量关闭：本人可操作且尚未关闭/取消；
// 关联需求的任务在「已完成」状态下也允许关闭（后端按指派人放行）。
// 父任务与禅道一致：子任务未全部完成前不能关闭，只有 done 状态才可关。
function isBatchClosable(t) {
  if (t.task_story_change_pending) return false;
  const s = String(t.status || '');
  if (s === 'closed' || s === 'cancel') return false;
  if (t.is_parent && s !== 'done') return false;
  if (t.can_operate) return true;
  return !!t.show_jump && s === 'done' && !!t.assigned_to_me;
}

document.addEventListener('zentao-story-change-confirmed', () => {
  if (window.isWorkbenchSubtabActive?.('task')) loadTaskWorkbench().catch(() => {});
});

// ── 卡片渲染（2026-11-24 排版 v2：与需求卡共用 mrc-* 结构，样式见 weave.css R8） ──

const STATUS_CHIP = {
  wait: 'mrc-chip--outline', doing: 'mrc-chip--info', changed: 'mrc-chip--info',
  pause: 'mrc-chip--warn', done: 'mrc-chip--ok', closed: 'mrc-chip--muted', cancel: 'mrc-chip--muted',
};
const twIcon = (id) => `<svg class="ui-ic tw-ic" aria-hidden="true"><use href="#i-${id}"/></svg>`;
const isClosedStatus = (s) => s === 'closed' || s === 'cancel';

function statusBadge(status) {
  const zh = TASK_STATUS_ZH[status] || status || '未知';
  return `<span class="mrc-chip ${STATUS_CHIP[status] || 'mrc-chip--muted'}">${escapeHtml(zh)}</span>`;
}

function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function isOverdue(t) {
  const s = String(t.status || '');
  return !!t.deadline && s !== 'done' && !isClosedStatus(s) && String(t.deadline).slice(0, 10) < localToday();
}

/**
 * 操作按钮。kind：start 绿 / warn 琥珀 / primary 主色 / info 天蓝 / ghost 描边 / danger 红字描边。
 * onclick 为原样拼进属性的 JS 表达式（调用方保证只含数字 id）。
 */
function twBtn(kind, icon, label, onclick, title = '') {
  const secondary = kind === 'ghost' || kind === 'danger' ? ' secondary' : '';
  return `<button type="button" class="mrc-btn tw-btn tw-btn--${kind}${secondary}"${title ? ` title="${escapeHtml(title)}"` : ''}
    onclick="event.stopPropagation(); ${onclick}">${twIcon(icon)}<span>${escapeHtml(label)}</span></button>`;
}
const twHint = (text) => `<span class="tw-hint">${escapeHtml(text)}</span>`;

// 按钮分三组排布：主操作（开始/暂停/完成…）｜辅助（工时、指派）｜危险操作靠右（关闭/取消）
function renderTaskActions(t) {
  const link = t.linked_requirement;
  const id = Number(t.task_id);
  const op = (action) => `taskWorkbenchOperate(${id}, '${action}')`;
  // 指派（转派）面向所有用户开放：任何任务都可以指派给指定的人。
  const assignBtn = twBtn('ghost', 'user', '指派', `openTaskAssign(${id})`, '把该任务指派给指定的人（所有人可用）');
  // 工时记录（查看对所有人开放；本人的记录可在弹窗里修改或删除）
  const effortBtn = twBtn('ghost', 'clock', '工时记录', `window.openTaskEffortModal(${id})`, '查看、修改或删除该任务已提交的禅道工时记录');
  const main = [], aux = [], danger = [];
  let hint = '';
  const status = String(t.status || '');

  if (t.task_story_change_pending) {
    hint = renderTaskStoryChangeBadge(t);
    aux.push(effortBtn, assignBtn);
  } else if (t.show_jump && link) {
    // 关联需求且该需求归属本人 → 跳转到需求工作台管理，不在此直接操作禅道。
    // 例外：任务已完成时，除跳转外也允许直接关闭（避免只为关闭再绕一圈）。
    main.push(twBtn('primary', 'external', '跳转到关联需求',
      `jumpToLinkedRequirement(${Number(link.id)}, ${Number(link.major_version_id) || 0})`, '跳转到需求工作台中该需求的位置'));
    if (t.assigned_to_me && (status === 'done' || isClosedStatus(status))) {
      main.push(twBtn('info', 'rotate', '重新激活', op('reactivate'), '重新激活任务，并同步取消需求工作台的测试完成状态'));
    }
    if (status === 'done' && t.assigned_to_me) danger.push(twBtn('danger', 'x-circle', '关闭', op('close')));
    aux.push(effortBtn, assignBtn);
  } else if (!t.can_operate) {
    // 不可操作：任务未指派给本人（且不是可跳转的自有需求）。查看工时/指派仍开放。
    hint = twHint('该任务未指派给你，无法操作');
    aux.push(effortBtn, assignBtn);
  } else if (t.is_parent) {
    // 父任务与禅道一致：状态由子任务驱动，子任务未完成时只能暂停/取消；
    // 子任务全部完成后禅道自动置为已完成，此时可关闭。
    if (status === 'pause') {
      main.push(twBtn('start', 'play', '继续', op('start')));
      danger.push(twBtn('danger', 'ban', '取消', op('cancel')));
    } else if (isClosedStatus(status)) {
      hint = twHint(status === 'cancel' ? '任务已取消' : '任务已关闭');
      main.push(twBtn('info', 'rotate', '重新激活', op('reactivate')));
    } else if (status === 'done') {
      danger.push(twBtn('danger', 'x-circle', '关闭', op('close')));
    } else { // wait / doing
      main.push(twBtn('warn', 'pause', '暂停', op('pause')));
      danger.push(twBtn('danger', 'ban', '取消', op('cancel')));
      hint = twHint('父任务由子任务驱动，子任务全部完成后自动完成');
    }
    aux.push(effortBtn, assignBtn);
  } else if (isClosedStatus(status)) {
    // 可操作：独立任务，或需求归属他人的衍生任务（本人是任务指派人）。
    hint = twHint('任务已关闭');
    main.push(twBtn('info', 'rotate', '重新激活', op('reactivate')));
    aux.push(effortBtn, assignBtn);
  } else {
    // 开始（未开始或已暂停时；已暂停时「开始」即继续）；进行中显示「暂停」。
    if (status === 'doing' || status === 'changed') main.push(twBtn('warn', 'pause', '暂停', op('pause')));
    else if (status === 'pause') main.push(twBtn('start', 'play', '继续', op('start')));
    else if (status !== 'done') main.push(twBtn('start', 'play', '开始', op('start')));
    // 完成 / 重新激活
    if (status === 'done') main.push(twBtn('info', 'rotate', '重新激活', op('reactivate')));
    else main.push(twBtn('primary', 'check', '完成', op('finish')));
    // 设置工时（预计）/ 工时记录（已消耗明细）
    aux.push(twBtn('ghost', 'timer', '设置工时', `taskWorkbenchSetTime(${id})`), effortBtn, assignBtn);
    danger.push(twBtn('danger', 'x-circle', '关闭', op('close')));
  }

  const sep = main.length && aux.length ? '<span class="tw-sep" aria-hidden="true"></span>' : '';
  return `<div class="tw-actions">
    ${main.join('')}${sep}${aux.join('')}
    ${hint}
    <span class="mrc-spacer"></span>
    ${danger.join('')}
  </div>`;
}

// 任务信息：执行 / 优先级 / 截止 / 指派 + 工时进度（预计、已消耗、剩余合在一条）
function renderTaskFacts(t) {
  const fact = (label, value, cls = '') => (value === null || value === undefined || value === '')
    ? '' : `<span class="tw-fact${cls}"><em>${label}</em>${value}</span>`;
  const pri = t.pri != null && t.pri !== '' ? `<b class="tw-pri tw-pri--${Number(t.pri) || 0}">P${escapeHtml(String(t.pri))}</b>` : '';
  const overdue = isOverdue(t);
  const due = t.deadline ? `${escapeHtml(String(t.deadline))}${overdue ? '<i class="tw-overdue">已逾期</i>' : ''}` : '';
  const fmt = (v) => `${Math.round(Number(v) * 100) / 100}h`;
  let effort = '';
  if (t.estimate != null || t.consumed != null || t.left != null) {
    const est = Number(t.estimate) || 0;
    const used = Number(t.consumed) || 0;
    const pct = est > 0 ? Math.min(100, Math.round((used / est) * 100)) : (used > 0 ? 100 : 0);
    const parts = [];
    if (t.consumed != null) parts.push(`已消耗 <b>${fmt(t.consumed)}</b>`);
    if (t.estimate != null) parts.push(`预计 ${fmt(t.estimate)}`);
    if (t.left != null) parts.push(`剩余 ${fmt(t.left)}`);
    effort = `<span class="tw-fact tw-effort"><em>工时</em>
      <span class="tw-bar${used > est && est > 0 ? ' is-over' : ''}" aria-hidden="true"><i style="width:${pct}%"></i></span>
      <span>${parts.join('<span class="tw-dot">·</span>')}</span></span>`;
  }
  const items = [
    fact('执行', t.execution_name ? escapeHtml(t.execution_name) : ''),
    fact('优先级', pri),
    fact('截止', due, overdue ? ' is-overdue' : ''),
    fact('指派', (t.assigned_to_realname || t.assigned_to) ? escapeHtml(t.assigned_to_realname || t.assigned_to) : ''),
    effort,
  ].filter(Boolean).join('');
  return `<div class="tw-facts">${items || '<span class="tw-hint">暂无更多信息</span>'}</div>`;
}

// 父任务卡片内嵌的子任务列表（含他人的子任务，与禅道层级一致）
function renderChildrenList(t) {
  if (!t.is_parent) return '';
  const kids = t.children || [];
  const body = kids.length
    ? kids.map((k) => `
      <div class="tw-child">
        <span class="mrc-id">#${Number(k.task_id)}</span>
        <span class="tw-child-name" title="${escapeHtml(k.name || '')}">${escapeHtml(k.name || '')}</span>
        ${renderTaskStoryChangeBadge(k) || statusBadge(k.status)}
        <span class="tw-child-who">${twIcon('user')}${escapeHtml(k.assigned_to_realname || k.assigned_to || '未指派')}</span>
      </div>`).join('')
    : '<span class="mrc-empty">该父任务暂无子任务</span>';
  return `<div class="mrc-section tw-children">
    <div class="mrc-section-head">子任务 <span class="n">${t.children_done || 0}/${t.children_total || 0} 完成</span></div>
    <div class="mrc-section-list">${body}</div>
  </div>`;
}

function renderTaskCard(t) {
  const link = t.linked_requirement;
  const status = String(t.status || '');
  const chips = [];
  // 父子层级徽章（与禅道层级一致）：父任务显示子任务完成进度；子任务标注所属父任务。
  if (t.is_parent) {
    chips.push(`<span class="mrc-chip mrc-chip--purple" title="父任务：状态由子任务驱动，子任务全部完成后禅道自动完成父任务">父任务 · ${t.children_done || 0}/${t.children_total || 0}</span>`);
  } else if (t.parent_info) {
    chips.push(`<span class="mrc-chip mrc-chip--outline" title="所属父任务 #${Number(t.parent_info.task_id)} ${escapeHtml(t.parent_info.name || '')}（指派人：${escapeHtml(t.parent_info.assigned_to_realname || '未知')}）">↳ 父任务 #${Number(t.parent_info.task_id)}</span>`);
  }
  if (link) {
    const ownerHint = t.requirement_mine ? '' : ` · ${escapeHtml(link.owner_name || '他人')}`;
    chips.push(`<span class="mrc-chip mrc-chip--purple" title="关联本平台需求${t.requirement_mine ? '（负责人：我）' : `（负责人：${escapeHtml(link.owner_name || '他人')}）`}">关联需求 ${escapeHtml(link.zentao_req_id || '')}${ownerHint}</span>`);
  } else if (!t.is_parent) {
    chips.push('<span class="mrc-chip mrc-chip--muted" title="未关联本平台需求，可直接在这里开始 / 完成 / 关闭">独立任务</span>');
  }

  const checkbox = isBatchClosable(t)
    ? `<input type="checkbox" class="tw-select" data-task-check="${t.task_id}" ${selectedTaskIds.has(t.task_id) ? 'checked' : ''}
        onclick="event.stopPropagation();" onchange="taskWorkbenchToggleSelect(${t.task_id}, this.checked)"
        title="勾选后可在上方「批量关闭」" aria-label="选择任务 #${t.task_id}">`
    : '';
  const overdue = isOverdue(t);
  const due = t.deadline && !isClosedStatus(status) && status !== 'done'
    ? `<span class="tw-due${overdue ? ' is-overdue' : ''}" title="截止日期 ${escapeHtml(String(t.deadline))}">${overdue ? '已逾期 · ' : '截止 '}${escapeHtml(String(t.deadline).slice(5, 10))}</span>`
    : '';
  const cls = ['mine-req-card', 'task-card'];
  if (status === 'done') cls.push('is-done');
  if (isClosedStatus(status)) cls.push('is-done', 'is-closed');

  return `
    <details class="${cls.join(' ')}" data-task-id="${t.task_id}" ${openTaskIds.has(t.task_id) ? 'open' : ''} ontoggle="taskWorkbenchRememberFold(${t.task_id}, this.open)">
      <summary class="mrc-head">
        <svg class="mrc-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
        ${checkbox}
        <div class="mrc-title">
          <span class="mrc-id">#${t.task_id}</span>
          <span class="mrc-name">${escapeHtml(t.name || '')}</span>
          ${renderPreviewBtn('task', t.task_id)}
          ${chips.join('')}
        </div>
        <div class="mrc-status">${due}${renderTaskStoryChangeBadge(t) || statusBadge(status)}</div>
      </summary>
      <div class="mrc-body">
        ${renderTaskFacts(t)}
        ${renderChildrenList(t)}
        ${renderTaskActions(t)}
      </div>
    </details>`;
}

// 顶部统计 = 状态筛选（替代原下拉框；数字为该状态的任务总数，与「隐藏已关闭」无关）
function renderTaskStats(visibleCount) {
  const count = (pred) => taskWorkbenchData.filter((t) => pred(String(t.status || ''))).length;
  const total = taskWorkbenchData.length;
  const linked = taskWorkbenchData.filter((t) => t.linked_requirement).length;
  const items = [
    ['all', '全部', total],
    ['wait', '未开始', count((s) => s === 'wait')],
    ['doing', '进行中', count((s) => s === 'doing' || s === 'changed')],
    ['pause', '已暂停', count((s) => s === 'pause')],
    ['done', '已完成', count((s) => s === 'done')],
    ['closed', '已关闭', count(isClosedStatus)],
  ];
  const pills = items.map(([key, label, n]) => `
    <button type="button" class="tw-stat${statusFilter === key ? ' is-on' : ''}" data-tw-filter="${key}"
      aria-pressed="${statusFilter === key}" onclick="taskWorkbenchFilterChanged('${key}')">
      <span>${label}</span><b>${n}</b></button>`).join('');
  const filtered = statusFilter !== 'all' || (hideClosed && statusFilter !== 'closed');
  const note = total
    ? `关联需求 ${linked} · 独立任务 ${total - linked}${filtered ? ` · 当前显示 ${visibleCount}` : ''}`
    : '当前账号名下暂无任务';
  return `<div class="tw-stat-group" role="group" aria-label="按状态筛选">${pills}</div><span class="tw-stats-note">${note}</span>`;
}

export async function loadTaskWorkbench(opts = {}) {
  const container = document.getElementById('taskWorkbenchCards');
  if (!container) return;
  const url = '/workbench/tasks' + (opts.refresh ? '?refresh=true' : '');
  if (opts.refresh) showLoading('正在从禅道刷新任务，请稍候…');
  try {
    const data = await (await api(url)).json();
    taskWorkbenchData = data.tasks || [];
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载任务工作台失败', 'error');
    taskWorkbenchData = [];
  } finally {
    if (opts.refresh) hideLoading();
  }

  // 清理已不可批量关闭的勾选（任务被关闭/移出名下后自动取消勾选）。
  const closableIds = new Set(taskWorkbenchData.filter(isBatchClosable).map((t) => t.task_id));
  for (const id of [...selectedTaskIds]) {
    if (!closableIds.has(id)) selectedTaskIds.delete(id);
  }

  renderTaskWorkbench();
}

// 按当前状态筛选渲染任务卡片（不重新请求后端）。
function renderTaskWorkbench() {
  const container = document.getElementById('taskWorkbenchCards');
  if (!container) return;

  const filterSel = document.getElementById('taskWorkbenchStatusFilter');
  if (filterSel && filterSel.value !== statusFilter) filterSel.value = statusFilter;
  const hideClosedCb = document.getElementById('taskWorkbenchHideClosed');
  if (hideClosedCb && hideClosedCb.checked !== hideClosed) hideClosedCb.checked = hideClosed;

  const visible = taskWorkbenchData.filter(taskMatchesFilter);
  const summaryEl = document.getElementById('taskWorkbenchSummary');
  if (summaryEl) summaryEl.innerHTML = renderTaskStats(visible.length);

  container.innerHTML = visible.length
    ? visible.map((t) => renderTaskCard(t)).join('')
    : (total
        ? '<div class="tw-empty">当前筛选条件下没有任务，可切换上方状态查看其他任务。</div>'
        : '<div class="tw-empty">当前账号名下暂无禅道任务（可点击「从禅道刷新」拉取最新）。</div>');

  updateBatchBar(visible);

  // 彩色边框流光特效（与需求工作台一致）
  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    container.querySelectorAll('.mine-req-card[data-task-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'task_workbench', key: el.getAttribute('data-task-id'), tone: 'blue', hoverDelayMs: 420 });
    });
  }
  window.scheduleWorkbenchViewportResize?.();
}

function updateBatchBar(visibleTasks) {
  const closable = (visibleTasks || taskWorkbenchData.filter(taskMatchesFilter)).filter(isBatchClosable);
  const btn = document.getElementById('taskWorkbenchBatchCloseBtn');
  if (btn) {
    btn.textContent = `批量关闭选中 (${selectedTaskIds.size})`;
    btn.disabled = selectedTaskIds.size === 0;
  }
  const wrap = document.getElementById('taskWorkbenchSelectAllWrap');
  // main.css 对含 checkbox 的 label 有 display:inline-flex !important，
  // 普通内联 display:none 压不过，必须带 important。
  if (wrap) {
    if (closable.length) wrap.style.removeProperty('display');
    else wrap.style.setProperty('display', 'none', 'important');
  }
  const selAll = document.getElementById('taskWorkbenchSelectAll');
  if (selAll) selAll.checked = closable.length > 0 && closable.every((t) => selectedTaskIds.has(t.task_id));
}

export function taskWorkbenchFilterChanged(value) {
  statusFilter = value || 'all';
  renderTaskWorkbench();
}

export function taskWorkbenchHideClosedChanged(checked) {
  hideClosed = !!checked;
  renderTaskWorkbench();
}

export function taskWorkbenchToggleSelect(taskId, checked) {
  if (checked) selectedTaskIds.add(taskId);
  else selectedTaskIds.delete(taskId);
  updateBatchBar();
}

// 全选/取消全选：只作用于当前筛选可见、且可关闭的任务。
export function taskWorkbenchToggleSelectAll(checked) {
  taskWorkbenchData.filter(taskMatchesFilter).filter(isBatchClosable).forEach((t) => {
    if (checked) selectedTaskIds.add(t.task_id);
    else selectedTaskIds.delete(t.task_id);
  });
  document.querySelectorAll('#taskWorkbenchCards input[data-task-check]').forEach((cb) => {
    cb.checked = selectedTaskIds.has(Number(cb.getAttribute('data-task-check')));
  });
  updateBatchBar();
}

// 逐个调用禅道关闭接口，失败的任务单独汇报，不影响其余任务。
export async function taskWorkbenchBatchClose() {
  const ids = taskWorkbenchData.filter((t) => selectedTaskIds.has(t.task_id) && isBatchClosable(t)).map((t) => t.task_id);
  if (!ids.length) {
    window.showMessage && window.showMessage('请先勾选要关闭的任务', 'info');
    return;
  }
  if (!window.confirm(`确认关闭选中的 ${ids.length} 个任务？关闭后如需继续可「重新激活」。`)) return;
  showLoading(`正在关闭任务（0/${ids.length}）…`);
  let okCount = 0;
  const failed = [];
  try {
    for (let i = 0; i < ids.length; i++) {
      setLoadingText(`正在关闭任务 #${ids[i]}（${i + 1}/${ids.length}）…`);
      try {
        const res = await (await api(`/workbench/tasks/${ids[i]}/operate`, {
          method: 'POST', headers: window.H, body: ({ action: 'close' }),
        })).json();
        if (res.ok) {
          okCount += 1;
          selectedTaskIds.delete(ids[i]);
        } else {
          failed.push(`#${ids[i]}：${(res.errors || []).join('；') || '未知错误'}`);
        }
      } catch (err) {
        failed.push(`#${ids[i]}：${err.message || '请求失败'}`);
      }
    }
    // 加载条挂到任务列表重载完成再收起，提示与界面更新同时出现
    setLoadingText('正在刷新任务列表…');
    try { await loadTaskWorkbench(); } catch (e) { console.warn('task workbench reload failed', e); }
  } finally {
    hideLoading();
  }
  if (failed.length) {
    window.showMessage && window.showMessage(`已关闭 ${okCount} 个，${failed.length} 个失败：${failed.join('；')}`, 'error');
  } else {
    window.showMessage && window.showMessage(`已关闭 ${okCount} 个任务，已同步禅道`, 'success');
  }
}

export async function refreshTaskWorkbench() {
  await loadTaskWorkbench({ refresh: true });
  window.showMessage && window.showMessage('任务已刷新', 'success');
}

// 跳转到需求工作台中对应需求卡片：切到需求子页 → 选中大版本并加载 → 展开/滚动/流光。
export async function jumpToLinkedRequirement(reqId, majorVersionId) {
  if (typeof window.switchWorkbenchSubtab === 'function') {
    await window.switchWorkbenchSubtab('demand', { load: false });
  }
  const modeSel = document.getElementById('mineDisplayMode');
  if (modeSel && modeSel.value !== 'version') {
    modeSel.value = 'version';
    window.OmniQAMineTab?.toggleMineMode?.();
  }
  const majorSel = document.getElementById('mineMajorSelect');
  if (majorVersionId && majorSel) majorSel.value = String(majorVersionId);
  if (typeof window.loadMyWorkbench === 'function') await window.loadMyWorkbench();

  const el = document.querySelector(`.mine-req-card[data-req-id="${reqId}"]`);
  if (el) {
    el.open = true;
    if (typeof window.rememberMineReqFold === 'function') window.rememberMineReqFold(reqId, true);
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.OmniQASSE?.pulseBoundaryGlow?.(el, 'blue');
  } else {
    window.showMessage && window.showMessage('该需求不在你的需求工作台列表中（可能非本人负责或不在当前版本）', 'info');
  }
}

export async function taskWorkbenchOperate(taskId, action) {
  const confirmText = {
    finish: '确认将该任务标记为完成？',
    close: '确认关闭该任务？',
    cancel: '确认取消该任务？（与禅道联动，取消后可重新激活）',
    reactivate: '确认重新激活该任务？',
  }[action];
  if (confirmText && !window.confirm(confirmText)) return;
  let consumed;
  if (action === 'finish') {
    const task = taskWorkbenchData.find((item) => Number(item.task_id) === Number(taskId));
    if (task && !task.has_time_tracking) {
      const suggested = Number(task.suggested_consumed_hours || task.left || task.estimate || 1);
      const input = window.prompt(
        '未检测到该任务的计时记录，请输入本次实际工时（小时）：',
        String(suggested),
      );
      if (input === null) return;
      consumed = Number(input);
      if (!Number.isFinite(consumed) || consumed <= 0 || consumed > 999) {
        window.showMessage && window.showMessage('实际工时需在 0~999 小时之间', 'error');
        return;
      }
    }
  }
  // 加载条挂到任务列表重载完成、提示放在重载之后：条消失时界面已与禅道一致。
  await operateTask(taskId, { action, ...(consumed !== undefined ? { consumed } : {}) }, { onSuccess: () => loadTaskWorkbench() });
}

export async function taskWorkbenchSetTime(taskId) {
  const input = window.prompt('设置该任务的预计工时（小时，0~999）：', '4');
  if (input === null) return;
  const hours = Number(input);
  if (!Number.isFinite(hours) || hours <= 0 || hours > 999) {
    window.showMessage && window.showMessage('工时需在 0~999 小时之间', 'error');
    return;
  }
  await operateTask(taskId, { action:'set_time', hours }, { onSuccess: () => loadTaskWorkbench() });
}

export function taskWorkbenchRememberFold(taskId, open) {
  if (open) openTaskIds.add(taskId);
  else openTaskIds.delete(taskId);
  window.scheduleWorkbenchViewportResize?.();
}

// ── 任务指派（面向所有用户开放，支持姓名/账号模糊搜索）──────────────────────

let assignCtx = { taskId: null, items: [], picked: '', source: 'workbench' };

function assignEls() {
  return {
    modal: document.getElementById('taskAssignModal'),
    input: document.getElementById('taskAssignInput'),
    list: document.getElementById('taskAssignList'),
    hint: document.getElementById('taskAssignHint'),
    label: document.getElementById('taskAssignTaskLabel'),
  };
}

// 模糊过滤：输入的单词（拼音/账号字母）或汉字都能命中（大小写不敏感的子串匹配）
function renderAssignList() {
  const { input, list } = assignEls();
  if (!input || !list) return;
  const q = input.value.trim().toLowerCase();
  const matched = q
    ? assignCtx.items.filter((u) => (`${u.realname || ''} ${u.account || ''}`).toLowerCase().includes(q))
    : assignCtx.items;
  list.innerHTML = matched.length
    ? matched.slice(0, 100).map((u) => `
        <div class="zt-combo-item ${assignCtx.picked === u.account ? 'active' : ''}" data-account="${escapeHtml(u.account)}"
          style="display:flex; justify-content:space-between; gap:8px; cursor:pointer;">
          <span>${escapeHtml(u.realname || u.account)}</span>
          <span class="muted" style="font-size:12px;">${escapeHtml(u.account)}</span>
        </div>`).join('')
    : '<div class="muted" style="padding:8px 10px; font-size:12px;">无匹配人员，试试换个关键字</div>';
}

export async function openTaskAssign(taskId, source = 'workbench') {
  const snapshot = assignCtx = { taskId, items: [], picked: '', source };
  const { modal, input, list, hint, label } = assignEls();
  if (!modal || !input || !list) return;
  if (label) label.textContent = `#${taskId}`;
  if (hint) hint.textContent = '';
  input.value = '';
  list.innerHTML = '<div class="muted" style="padding:8px 10px; font-size:12px;">正在加载可指派人…</div>';
  prepareOperationModal(modal);
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  if (!input.dataset.bound) {
    input.dataset.bound = '1';
    input.addEventListener('input', () => { assignCtx.picked = ''; renderAssignList(); });
    list.addEventListener('click', (e) => {
      const item = e.target.closest('[data-account]');
      if (!item) return;
      assignCtx.picked = item.getAttribute('data-account');
      renderAssignList();
    });
  }
  try {
    const data = await (await api(`/workbench/tasks/${taskId}/assignable`)).json();
    if (assignCtx !== snapshot) return;
    assignCtx.items = data.users || [];
    if (hint) {
      const cur = assignCtx.items.find((u) => u.account === data.current);
      hint.textContent = data.current ? `当前指派：${cur ? `${cur.realname}（${data.current}）` : data.current}` : '当前未指派';
    }
    renderAssignList();
  } catch (err) {
    if (assignCtx !== snapshot) return;
    list.innerHTML = `<div class="muted" style="padding:8px 10px; font-size:12px;">加载可指派人失败：${escapeHtml(err.message || '未知错误')}</div>`;
  }
  input.focus();
}

export function closeTaskAssignModal() {
  const { modal } = assignEls();
  if (!modal) return;
  modal.classList.add('hidden');
  modal.style.display = 'none';
}

export async function confirmTaskAssign() {
  if (!assignCtx.taskId) return;
  if (!assignCtx.picked) {
    window.showMessage && window.showMessage('请先从列表中选择要指派的人员', 'info');
    return;
  }
  const snapshot = assignCtx;
  await operateTask(snapshot.taskId, { action:'assign', assigned_to:snapshot.picked }, {
    modal: 'taskAssignModal',
    isCurrent: () => assignCtx === snapshot,
    restore: () => {
      assignCtx = snapshot;
      const { label } = assignEls();
      if (label) label.textContent = `#${snapshot.taskId}`;
      renderAssignList();
    },
    onSuccess: async () => {
      if (assignCtx === snapshot) closeTaskAssignModal();
      await loadTaskWorkbench();
      if (snapshot.source === 'board') await window.OmniQATaskBoardTab?.reloadZentaoFromMirror?.();
    },
  });
}

window.OmniQATaskWorkbenchTab = {
  loadTaskWorkbench,
  refreshTaskWorkbench,
  jumpToLinkedRequirement,
  taskWorkbenchOperate,
  taskWorkbenchSetTime,
  taskWorkbenchRememberFold,
  taskWorkbenchFilterChanged,
  taskWorkbenchHideClosedChanged,
  taskWorkbenchToggleSelect,
  taskWorkbenchToggleSelectAll,
  taskWorkbenchBatchClose,
  openTaskAssign,
  closeTaskAssignModal,
  confirmTaskAssign,
};

let taskWorkbenchSseBound = false;
function bindTaskWorkbenchSSE() {
  if (taskWorkbenchSseBound || !window.OmniQASSE?.subscribe) return;
  window.OmniQASSE.subscribe('zentao_task_changed', () => {
    if (!window.isWorkbenchSubtabActive?.('task')) return;
    if (window._taskWorkbenchSSERefreshTimer) clearTimeout(window._taskWorkbenchSSERefreshTimer);
    window._taskWorkbenchSSERefreshTimer = setTimeout(() => {
      window._taskWorkbenchSSERefreshTimer = null;
      loadTaskWorkbench().catch(() => {});
    }, 400);
  });
  taskWorkbenchSseBound = true;
}
bindTaskWorkbenchSSE();
setTimeout(bindTaskWorkbenchSSE, 3000);
