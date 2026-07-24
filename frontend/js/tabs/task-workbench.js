import { api } from '../api.js';
import { escapeHtml, renderPreviewBtn } from '../utils.js';
import { showLoading, hideLoading, setLoadingText } from '../components/common.js';

// 任务工作台：展示当前账号名下的禅道任务。
//  - 关联本平台需求的任务：不显示禅道操作按钮，只提供「跳转到需求工作台对应位置」。
//  - 未关联需求的任务：提供开始 / 设置工时 / 完成 / 关闭 / 重新激活等禅道操作。
// 卡片外观与需求工作台的需求卡片保持一致（<details.mine-req-card>：可折叠、预览、
// 跳转、彩色边框流光特效）。

let taskWorkbenchData = [];
// 记住已展开的任务卡片，刷新/操作后重新渲染时保持展开，避免每次都被收起。
const openTaskIds = new Set();
// 状态筛选（all / wait / doing / pause / done / closed，closed 含 cancel）。
let statusFilter = 'all';
// 隐藏已关闭/已取消的任务；状态筛选显式选「已关闭」时不生效（明确要看时以筛选为准）。
let hideClosed = false;
// 批量关闭：勾选的任务 id。
const selectedTaskIds = new Set();

const TASK_STATUS_ZH = { wait: '未开始', doing: '进行中', done: '已完成', pause: '已暂停', cancel: '已取消', closed: '已关闭' };

function taskMatchesFilter(t) {
  const s = String(t.status || '');
  if (hideClosed && statusFilter !== 'closed' && (s === 'closed' || s === 'cancel')) return false;
  if (statusFilter === 'all') return true;
  if (statusFilter === 'closed') return s === 'closed' || s === 'cancel';
  return s === statusFilter;
}

// 可参与批量关闭：本人可操作且尚未关闭/取消；
// 关联需求的任务在「已完成」状态下也允许关闭（后端按指派人放行）。
// 父任务与禅道一致：子任务未全部完成前不能关闭，只有 done 状态才可关。
function isBatchClosable(t) {
  const s = String(t.status || '');
  if (s === 'closed' || s === 'cancel') return false;
  if (t.is_parent && s !== 'done') return false;
  if (t.can_operate) return true;
  return !!t.show_jump && s === 'done' && !!t.assigned_to_me;
}

function statusBadge(status) {
  const zh = TASK_STATUS_ZH[status] || status || '未知';
  const done = status === 'done';
  const closed = status === 'closed' || status === 'cancel';
  const bg = done ? '#dcfce7' : closed ? '#f1f5f9' : '#eff6ff';
  const fg = done ? '#166534' : closed ? '#94a3b8' : '#1d4ed8';
  const bd = done ? '#bbf7d0' : closed ? '#e2e8f0' : '#bfdbfe';
  return `<span class="badge" style="background:${bg}; color:${fg}; border:1px solid ${bd};">${zh}</span>`;
}

function metaBadge(label, value) {
  if (value === null || value === undefined || value === '') return '';
  return `<span class="badge" style="background:#f8fafc; color:#475569; border:1px solid #e2e8f0;">${escapeHtml(label)}：${escapeHtml(String(value))}</span>`;
}

function renderTaskActions(t) {
  const link = t.linked_requirement;
  // 指派（转派）面向所有用户开放：任何任务都可以指派给指定的人。
  const assignBtn = `<button class="secondary" style="padding:2px 10px; font-size:12px; color:#7c3aed; border-color:#ddd6fe;"
    onclick="event.stopPropagation(); openTaskAssign(${t.task_id})" title="把该任务指派给指定的人（所有人可用）">👤 指派</button>`;
  // 工时记录（查看对所有人开放；本人的记录可在弹窗里修改或删除）
  const effortBtn = `<button class="secondary" style="padding:2px 10px; font-size:12px; color:#0f766e; border-color:#99f6e4;"
    onclick="event.stopPropagation(); window.openTaskEffortModal(${t.task_id})" title="查看、修改或删除该任务已提交的禅道工时记录">🕒 工时记录</button>`;
  const wrap = (parts) => `<div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">${parts.join('')}</div>`;
  // 关联需求且该需求归属本人 → 跳转到需求工作台管理，不在此直接操作禅道。
  // 例外：任务已完成时，除跳转外也允许直接关闭（避免只为关闭再绕一圈）。
  if (t.show_jump && link) {
    const parts = [`<button class="secondary" style="padding:2px 10px; font-size:12px; color:#1d4ed8; border-color:#bfdbfe;"
      onclick="event.stopPropagation(); jumpToLinkedRequirement(${link.id}, ${link.major_version_id || 0})"
      title="跳转到需求工作台中该需求的位置">↪ 跳转到关联需求</button>`];
    if (t.status === 'done' && t.assigned_to_me) {
      parts.push(`<button style="padding:2px 10px; font-size:12px; color:#b91c1c; border-color:#fca5a5;"
        onclick="event.stopPropagation(); taskWorkbenchOperate(${t.task_id}, 'close')">⛔ 关闭</button>`);
    }
    if (t.assigned_to_me && (t.status === 'done' || t.status === 'closed' || t.status === 'cancel')) {
      parts.push(`<button style="padding:2px 10px; font-size:12px; background:#0ea5e9;"
        onclick="event.stopPropagation(); taskWorkbenchOperate(${t.task_id}, 'reactivate')"
        title="重新激活任务，并同步取消需求工作台的测试完成状态">♻ 重新激活</button>`);
    }
    parts.push(effortBtn);
    parts.push(assignBtn);
    return wrap(parts);
  }
  // 不可操作：任务未指派给本人（且不是可跳转的自有需求）。查看工时/指派仍开放。
  if (!t.can_operate) {
    return wrap([`<span class="muted" style="font-size:12px;">该任务未指派给你，无法操作</span>`, effortBtn, assignBtn]);
  }
  const id = t.task_id;
  const status = t.status;
  const btn = (label, action, bg, extra = '') =>
    `<button style="padding:2px 10px; font-size:12px; ${bg ? `background:${bg};` : ''} ${extra}" onclick="event.stopPropagation(); taskWorkbenchOperate(${id}, '${action}')">${label}</button>`;

  // 父任务与禅道一致：状态由子任务驱动，子任务未完成时只能暂停/取消；
  // 子任务全部完成后禅道自动置为已完成，此时可关闭。
  if (t.is_parent) {
    const parts = [];
    const hint = `<span class="muted" style="font-size:12px;">父任务由子任务驱动，子任务全部完成后自动完成</span>`;
    if (status === 'pause') {
      parts.push(btn('▶ 继续', 'start', '#16a34a'));
      parts.push(btn('🚫 取消', 'cancel', '', 'color:#b91c1c; border-color:#fca5a5;'));
    } else if (status === 'closed' || status === 'cancel') {
      parts.push(`<span class="muted" style="font-size:12px;">${status === 'cancel' ? '任务已取消' : '任务已关闭'}</span>`);
      parts.push(btn('♻ 重新激活', 'reactivate', '#0ea5e9'));
    } else if (status === 'done') {
      parts.push(`<button class="secondary" disabled style="padding:2px 10px; font-size:12px; opacity:.7;">✅ 已完成</button>`);
      parts.push(btn('⛔ 关闭', 'close', '', 'color:#b91c1c; border-color:#fca5a5;'));
    } else { // wait / doing
      parts.push(btn('⏸ 暂停', 'pause', '#d97706'));
      parts.push(btn('🚫 取消', 'cancel', '', 'color:#b91c1c; border-color:#fca5a5;'));
      parts.push(hint);
    }
    parts.push(effortBtn);
    parts.push(assignBtn);
    return wrap(parts);
  }

  // 可操作：独立任务，或需求归属他人的衍生任务（本人是任务指派人）。
  const isDone = status === 'done';
  const isClosed = status === 'closed' || status === 'cancel';
  const isDoing = status === 'doing';
  const isPaused = status === 'pause';
  const parts = [];
  if (isClosed) {
    parts.push(`<span class="muted" style="font-size:12px;">任务已关闭</span>`);
    parts.push(btn('♻ 重新激活', 'reactivate', '#0ea5e9'));
    parts.push(effortBtn);
    parts.push(assignBtn);
    return wrap(parts);
  }
  // 开始（未开始或已暂停时；已暂停时「开始」即继续）；进行中显示「暂停」。
  if (isDoing) {
    parts.push(btn('⏸ 暂停', 'pause', '#d97706'));
  } else if (isPaused) {
    parts.push(btn('▶ 继续', 'start', '#16a34a'));
  } else if (!isDone) {
    parts.push(btn('▶ 开始', 'start', '#16a34a'));
  }
  // 设置工时（预计）/ 工时记录（已消耗明细）
  parts.push(`<button class="secondary" style="padding:2px 10px; font-size:12px;" onclick="event.stopPropagation(); taskWorkbenchSetTime(${id})">🕒 设置工时</button>`);
  parts.push(effortBtn);
  // 完成 / 重新激活
  if (isDone) {
    parts.push(`<button class="secondary" disabled style="padding:2px 10px; font-size:12px; opacity:.7;">✅ 已完成</button>`);
    parts.push(btn('♻ 重新激活', 'reactivate', '#0ea5e9'));
  } else {
    parts.push(btn('✅ 完成', 'finish', '#0d9488'));
  }
  // 关闭
  parts.push(btn('⛔ 关闭', 'close', '', 'color:#b91c1c; border-color:#fca5a5;'));
  parts.push(assignBtn);
  return wrap(parts);
}

// 父任务卡片内嵌的子任务列表（含他人的子任务，与禅道层级一致）
function renderChildrenList(t) {
  if (!t.is_parent) return '';
  const kids = t.children || [];
  if (!kids.length) {
    return `<div class="muted" style="font-size:12px; margin-bottom:10px;">该父任务暂无子任务</div>`;
  }
  const rows = kids.map((k) => `
    <div style="display:flex; align-items:center; gap:8px; padding:6px 10px; border-bottom:1px dashed #e2e8f0;">
      <span style="color:#1d4ed8; font-size:12px; flex-shrink:0;">#${k.task_id}</span>
      <span style="font-size:13px; color:#334155; flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(k.name || '')}</span>
      ${statusBadge(k.status)}
      <span class="muted" style="font-size:12px; flex-shrink:0;">👤 ${escapeHtml(k.assigned_to_realname || k.assigned_to || '未指派')}</span>
    </div>`).join('');
  return `<div style="margin-bottom:10px; border:1px solid #e2e8f0; border-radius:8px; overflow:hidden;">
    <div style="background:#f8fafc; padding:6px 10px; font-size:12px; color:#475569; font-weight:600;">子任务（${t.children_done || 0}/${t.children_total || 0} 完成）</div>
    ${rows}
  </div>`;
}

function renderTaskCard(t) {
  const link = t.linked_requirement;
  const previewBtn = renderPreviewBtn('task', t.task_id);
  let linkedBadge;
  if (link) {
    const ownerHint = t.requirement_mine ? '' : `（负责人：${escapeHtml(link.owner_name || '他人')}）`;
    linkedBadge = `<span class="badge" style="background:#faf5ff; color:#7c3aed; border:1px solid #e9d5ff;">关联需求 ${escapeHtml(link.zentao_req_id || '')}${ownerHint}</span>`;
  } else if (t.is_parent) {
    linkedBadge = '';
  } else {
    linkedBadge = `<span class="badge" style="background:#fff7ed; color:#c2410c; border:1px solid #fed7aa;">独立任务（无关联需求）</span>`;
  }
  // 父子层级徽章（与禅道层级一致）：父任务显示子任务完成进度；子任务标注所属父任务。
  let hierarchyBadge = '';
  if (t.is_parent) {
    hierarchyBadge = `<span class="badge" style="background:#eef2ff; color:#4338ca; border:1px solid #c7d2fe;"
      title="父任务：状态由子任务驱动，子任务全部完成后禅道自动完成父任务">👑 父任务 · 子任务 ${t.children_done || 0}/${t.children_total || 0} 完成</span>`;
  } else if (t.parent_info) {
    hierarchyBadge = `<span class="badge" style="background:#f0fdf4; color:#15803d; border:1px solid #bbf7d0;"
      title="所属父任务（指派人：${escapeHtml(t.parent_info.assigned_to_realname || '未知')}）">↳ 父任务 #${t.parent_info.task_id} ${escapeHtml(t.parent_info.name || '')}</span>`;
  }
  const metas = [
    metaBadge('执行', t.execution_name),
    metaBadge('优先级', t.pri),
    metaBadge('预计工时', t.estimate != null ? `${t.estimate}h` : ''),
    metaBadge('已消耗', t.consumed != null ? `${t.consumed}h` : ''),
    metaBadge('剩余', t.left != null ? `${t.left}h` : ''),
    metaBadge('截止', t.deadline),
    metaBadge('指派', t.assigned_to_realname || t.assigned_to),
  ].filter(Boolean).join('');

  const checkbox = isBatchClosable(t)
    ? `<input type="checkbox" data-task-check="${t.task_id}" ${selectedTaskIds.has(t.task_id) ? 'checked' : ''}
        onclick="event.stopPropagation();" onchange="taskWorkbenchToggleSelect(${t.task_id}, this.checked)"
        title="勾选后可在上方「批量关闭」" style="width:16px; height:16px; cursor:pointer; accent-color:#dc2626; flex-shrink:0;">`
    : '';

  return `
    <details class="mine-req-card" data-task-id="${t.task_id}" ${openTaskIds.has(t.task_id) ? 'open' : ''} ontoggle="taskWorkbenchRememberFold(${t.task_id}, this.open)" style="background:#ffffff; transition: all 0.3s;">
      <summary style="outline:none; cursor:pointer; font-size:16px; font-weight:bold; color:#0f172a; border-bottom:1px solid #e2e8f0; padding-bottom:12px; display:flex; justify-content:space-between; align-items:center; gap:8px; list-style:none;">
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
          ${checkbox}
          <span style="color:#1d4ed8;">禅道任务 #${t.task_id}</span>
          <span>${escapeHtml(t.name || '')}</span>
          ${previewBtn}
          ${statusBadge(t.status)}
          ${hierarchyBadge}
          ${linkedBadge}
        </div>
        <span style="font-size:12px; color:#94a3b8; font-weight:normal;">(点击标题可收起/展开)</span>
      </summary>
      <div style="margin-top:12px;">
        <div class="row" style="margin-bottom:10px; gap:6px; flex-wrap:wrap;">${metas || '<span class="muted">暂无更多信息</span>'}</div>
        ${renderChildrenList(t)}
        <div class="row" style="gap:8px; flex-wrap:wrap; align-items:center;">${renderTaskActions(t)}</div>
      </div>
    </details>`;
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
  const total = taskWorkbenchData.length;
  const linked = taskWorkbenchData.filter((t) => t.linked_requirement).length;
  if (summaryEl) {
    const hints = [];
    if (statusFilter !== 'all') {
      hints.push(`筛选「${{ wait: '未开始', doing: '进行中', pause: '已暂停', done: '已完成', closed: '已关闭' }[statusFilter] || statusFilter}」`);
    }
    if (hideClosed && statusFilter !== 'closed') hints.push('已隐藏已关闭');
    const filterHint = hints.length
      ? `，当前${hints.join('、')}显示 <b style="color:#1d4ed8;">${visible.length}</b> 个`
      : '';
    summaryEl.innerHTML = total
      ? `📋 你名下共有 <b style="color:#1d4ed8;">${total}</b> 个任务（关联需求 ${linked} 个，独立任务 ${total - linked} 个）${filterHint}`
      : '🎉 当前账号名下暂无任务';
    summaryEl.style.display = 'block';
  }

  container.innerHTML = visible.length
    ? visible.map((t) => renderTaskCard(t)).join('')
    : (total
        ? '<div class="muted" style="padding:24px; text-align:center;">当前筛选条件下没有任务，可切换状态筛选查看其他任务。</div>'
        : '<div class="muted" style="padding:24px; text-align:center;">当前账号名下暂无禅道任务（可点击「刷新」从禅道拉取最新）。</div>');

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
    btn.textContent = `⛔ 批量关闭选中 (${selectedTaskIds.size})`;
    btn.disabled = selectedTaskIds.size === 0;
    btn.style.opacity = selectedTaskIds.size === 0 ? '.55' : '';
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
  showLoading('正在同步禅道，请稍候…');
  let msg = '操作已同步禅道';
  let msgType = 'success';
  try {
    const res = await (await api(`/workbench/tasks/${taskId}/operate`, {
      method: 'POST', headers: window.H, body: ({ action, ...(consumed !== undefined ? { consumed } : {}) }),
    })).json();
    if (!res.ok) {
      msg = '禅道操作有异常：' + (res.errors || []).join('；');
      msgType = 'error';
    }
  } catch (err) {
    msg = err.message || '操作失败';
    msgType = 'error';
  }
  setLoadingText(msgType === 'success' ? '禅道已同步，正在刷新任务列表…' : '正在刷新任务列表…');
  try { await loadTaskWorkbench(); } catch (e) { console.warn('task workbench reload failed', e); }
  hideLoading();
  window.showMessage && window.showMessage(msg, msgType);
}

export async function taskWorkbenchSetTime(taskId) {
  const input = window.prompt('设置该任务的预计工时（小时，0~999）：', '4');
  if (input === null) return;
  const hours = Number(input);
  if (!Number.isFinite(hours) || hours <= 0 || hours > 999) {
    window.showMessage && window.showMessage('工时需在 0~999 小时之间', 'error');
    return;
  }
  showLoading('正在同步禅道，请稍候…');
  let msg = '工时已更新';
  let msgType = 'success';
  try {
    const res = await (await api(`/workbench/tasks/${taskId}/operate`, {
      method: 'POST', headers: window.H, body: ({ action: 'set_time', hours }),
    })).json();
    if (!res.ok) {
      msg = '禅道操作有异常：' + (res.errors || []).join('；');
      msgType = 'error';
    }
  } catch (err) {
    msg = err.message || '设置失败';
    msgType = 'error';
  }
  setLoadingText(msgType === 'success' ? '禅道已同步，正在刷新任务列表…' : '正在刷新任务列表…');
  try { await loadTaskWorkbench(); } catch (e) { console.warn('task workbench reload failed', e); }
  hideLoading();
  window.showMessage && window.showMessage(msg, msgType);
}

export function taskWorkbenchRememberFold(taskId, open) {
  if (open) openTaskIds.add(taskId);
  else openTaskIds.delete(taskId);
  window.scheduleWorkbenchViewportResize?.();
}

// ── 任务指派（面向所有用户开放，支持姓名/账号模糊搜索）──────────────────────

const assignCtx = { taskId: null, items: [], picked: '', source: 'workbench' };

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
  assignCtx.taskId = taskId;
  assignCtx.items = [];
  assignCtx.picked = '';
  assignCtx.source = source;
  const { modal, input, list, hint, label } = assignEls();
  if (!modal || !input || !list) return;
  if (label) label.textContent = `#${taskId}`;
  if (hint) hint.textContent = '';
  input.value = '';
  list.innerHTML = '<div class="muted" style="padding:8px 10px; font-size:12px;">正在加载可指派人…</div>';
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
    assignCtx.items = data.users || [];
    if (hint) {
      const cur = assignCtx.items.find((u) => u.account === data.current);
      hint.textContent = data.current ? `当前指派：${cur ? `${cur.realname}（${data.current}）` : data.current}` : '当前未指派';
    }
    renderAssignList();
  } catch (err) {
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
  const target = assignCtx.items.find((u) => u.account === assignCtx.picked);
  showLoading('正在同步禅道指派，请稍候…');
  let msg = `任务 #${assignCtx.taskId} 已指派给 ${target ? target.realname : assignCtx.picked}，已同步禅道`;
  let msgType = 'success';
  try {
    const res = await (await api(`/workbench/tasks/${assignCtx.taskId}/operate`, {
      method: 'POST', headers: window.H, body: ({ action: 'assign', assigned_to: assignCtx.picked }),
    })).json();
    if (res.ok) {
      closeTaskAssignModal();
    } else {
      msg = '禅道指派有异常：' + (res.errors || []).join('；');
      msgType = 'error';
    }
  } catch (err) {
    msg = err.message || '指派失败';
    msgType = 'error';
  }
  // 刷新数据：任务工作台始终刷新；从任务看板发起时同时刷新看板的禅道数据。
  // 加载条挂到重载完成、提示放在重载之后，条消失时界面已与禅道一致。
  setLoadingText(msgType === 'success' ? '禅道已同步，正在刷新任务列表…' : '正在刷新任务列表…');
  try { await loadTaskWorkbench(); } catch (e) { console.warn('task workbench reload failed', e); }
  hideLoading();
  window.showMessage && window.showMessage(msg, msgType);
  if (assignCtx.source === 'board' && window.OmniQATaskBoardTab?.reloadZentaoFromMirror) {
    window.OmniQATaskBoardTab.reloadZentaoFromMirror().catch?.(() => {});
  }
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
