import { api } from '../api.js';
import { escapeHtml, renderPreviewBtn } from '../utils.js';
import { showLoading, hideLoading } from '../components/common.js';

// 任务工作台：展示当前账号名下的禅道任务。
//  - 关联本平台需求的任务：不显示禅道操作按钮，只提供「跳转到需求工作台对应位置」。
//  - 未关联需求的任务：提供开始 / 设置工时 / 完成 / 关闭 / 重新激活等禅道操作。
// 卡片外观与需求工作台的需求卡片保持一致（<details.mine-req-card>：可折叠、预览、
// 跳转、彩色边框流光特效）。

let taskWorkbenchData = [];
// 记住已展开的任务卡片，刷新/操作后重新渲染时保持展开，避免每次都被收起。
const openTaskIds = new Set();

const TASK_STATUS_ZH = { wait: '未开始', doing: '进行中', done: '已完成', pause: '已暂停', cancel: '已取消', closed: '已关闭' };

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
  // 关联需求且该需求归属本人 → 跳转到需求工作台管理，不在此直接操作禅道。
  if (t.show_jump && link) {
    return `<button class="secondary" style="padding:2px 10px; font-size:12px; color:#1d4ed8; border-color:#bfdbfe;"
      onclick="event.stopPropagation(); jumpToLinkedRequirement(${link.id}, ${link.major_version_id || 0})"
      title="跳转到需求工作台中该需求的位置">↪ 跳转到关联需求</button>`;
  }
  // 不可操作：任务未指派给本人（且不是可跳转的自有需求）。
  if (!t.can_operate) {
    return `<span class="muted" style="font-size:12px;">该任务未指派给你，无法操作</span>`;
  }
  // 可操作：独立任务，或需求归属他人的衍生任务（本人是任务指派人）。
  const id = t.task_id;
  const status = t.status;
  const isDone = status === 'done';
  const isClosed = status === 'closed' || status === 'cancel';
  const isDoing = status === 'doing';
  const btn = (label, action, bg, extra = '') =>
    `<button style="padding:2px 10px; font-size:12px; ${bg ? `background:${bg};` : ''} ${extra}" onclick="event.stopPropagation(); taskWorkbenchOperate(${id}, '${action}')">${label}</button>`;
  const isPaused = status === 'pause';
  const parts = [];
  if (isClosed) {
    parts.push(`<span class="muted" style="font-size:12px;">任务已关闭</span>`);
    parts.push(btn('♻ 重新激活', 'reactivate', '#0ea5e9'));
    return `<div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">${parts.join('')}</div>`;
  }
  // 开始（未开始或已暂停时；已暂停时「开始」即继续）；进行中显示「暂停」。
  if (isDoing) {
    parts.push(btn('⏸ 暂停', 'pause', '#d97706'));
  } else if (isPaused) {
    parts.push(btn('▶ 继续', 'start', '#16a34a'));
  } else if (!isDone) {
    parts.push(btn('▶ 开始', 'start', '#16a34a'));
  }
  // 设置工时
  parts.push(`<button class="secondary" style="padding:2px 10px; font-size:12px;" onclick="event.stopPropagation(); taskWorkbenchSetTime(${id})">🕒 设置工时</button>`);
  // 完成 / 重新激活
  if (isDone) {
    parts.push(`<button class="secondary" disabled style="padding:2px 10px; font-size:12px; opacity:.7;">✅ 已完成</button>`);
    parts.push(btn('♻ 重新激活', 'reactivate', '#0ea5e9'));
  } else {
    parts.push(btn('✅ 完成', 'finish', '#0d9488'));
  }
  // 关闭
  parts.push(btn('⛔ 关闭', 'close', '', 'color:#b91c1c; border-color:#fca5a5;'));
  return `<div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">${parts.join('')}</div>`;
}

function renderTaskCard(t) {
  const link = t.linked_requirement;
  const previewBtn = renderPreviewBtn('task', t.task_id);
  let linkedBadge;
  if (link) {
    const ownerHint = t.requirement_mine ? '' : `（负责人：${escapeHtml(link.owner_name || '他人')}）`;
    linkedBadge = `<span class="badge" style="background:#faf5ff; color:#7c3aed; border:1px solid #e9d5ff;">关联需求 ${escapeHtml(link.zentao_req_id || '')}${ownerHint}</span>`;
  } else {
    linkedBadge = `<span class="badge" style="background:#fff7ed; color:#c2410c; border:1px solid #fed7aa;">独立任务（无关联需求）</span>`;
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

  return `
    <details class="mine-req-card" data-task-id="${t.task_id}" ${openTaskIds.has(t.task_id) ? 'open' : ''} ontoggle="taskWorkbenchRememberFold(${t.task_id}, this.open)" style="background:#ffffff; transition: all 0.3s;">
      <summary style="outline:none; cursor:pointer; font-size:16px; font-weight:bold; color:#0f172a; border-bottom:1px solid #e2e8f0; padding-bottom:12px; display:flex; justify-content:space-between; align-items:center; gap:8px; list-style:none;">
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
          <span style="color:#1d4ed8;">禅道任务 #${t.task_id}</span>
          <span>${escapeHtml(t.name || '')}</span>
          ${previewBtn}
          ${statusBadge(t.status)}
          ${linkedBadge}
        </div>
        <span style="font-size:12px; color:#94a3b8; font-weight:normal;">(点击标题可收起/展开)</span>
      </summary>
      <div style="margin-top:12px;">
        <div class="row" style="margin-bottom:10px; gap:6px; flex-wrap:wrap;">${metas || '<span class="muted">暂无更多信息</span>'}</div>
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

  const summaryEl = document.getElementById('taskWorkbenchSummary');
  const total = taskWorkbenchData.length;
  const linked = taskWorkbenchData.filter((t) => t.linked_requirement).length;
  if (summaryEl) {
    summaryEl.innerHTML = total
      ? `📋 你名下共有 <b style="color:#1d4ed8;">${total}</b> 个任务（关联需求 ${linked} 个，独立任务 ${total - linked} 个）`
      : '🎉 当前账号名下暂无任务';
    summaryEl.style.display = 'block';
  }

  container.innerHTML = taskWorkbenchData.length
    ? taskWorkbenchData.map((t) => renderTaskCard(t)).join('')
    : '<div class="muted" style="padding:24px; text-align:center;">当前账号名下暂无禅道任务（可点击「刷新」从禅道拉取最新）。</div>';

  // 彩色边框流光特效（与需求工作台一致）
  if (window.OmniQASSE && typeof window.OmniQASSE.mountAttention === 'function') {
    container.querySelectorAll('.mine-req-card[data-task-id]').forEach((el) => {
      window.OmniQASSE.mountAttention(el, { scope: 'task_workbench', key: el.getAttribute('data-task-id'), tone: 'blue', hoverDelayMs: 420 });
    });
  }
  window.scheduleWorkbenchViewportResize?.();
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
  const confirmText = { finish: '确认将该任务标记为完成？', close: '确认关闭该任务？', reactivate: '确认重新激活该任务？' }[action];
  if (confirmText && !window.confirm(confirmText)) return;
  showLoading('正在同步禅道，请稍候…');
  try {
    const res = await (await api(`/workbench/tasks/${taskId}/operate`, {
      method: 'POST', headers: window.H, body: ({ action }),
    })).json();
    if (res.ok) {
      window.showMessage && window.showMessage('操作已同步禅道', 'success');
    } else {
      window.showMessage && window.showMessage('禅道操作有异常：' + (res.errors || []).join('；'), 'error');
    }
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '操作失败', 'error');
  } finally {
    hideLoading();
  }
  await loadTaskWorkbench();
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
  try {
    const res = await (await api(`/workbench/tasks/${taskId}/operate`, {
      method: 'POST', headers: window.H, body: ({ action: 'set_time', hours }),
    })).json();
    if (res.ok) window.showMessage && window.showMessage('工时已更新', 'success');
    else window.showMessage && window.showMessage('禅道操作有异常：' + (res.errors || []).join('；'), 'error');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '设置失败', 'error');
  } finally {
    hideLoading();
  }
  await loadTaskWorkbench();
}

export function taskWorkbenchRememberFold(taskId, open) {
  if (open) openTaskIds.add(taskId);
  else openTaskIds.delete(taskId);
  window.scheduleWorkbenchViewportResize?.();
}

window.OmniQATaskWorkbenchTab = {
  loadTaskWorkbench,
  refreshTaskWorkbench,
  jumpToLinkedRequirement,
  taskWorkbenchOperate,
  taskWorkbenchSetTime,
  taskWorkbenchRememberFold,
};
