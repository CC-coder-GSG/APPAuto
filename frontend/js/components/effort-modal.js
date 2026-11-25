import { runZentaoOperation, operationPending, prepareOperationModal } from './zentao-operation.js';
import { api } from '../api.js';
import { escapeHtml } from '../utils.js';
import { confirmTaskDialog } from './task-dialog.js';

// 工时记录弹窗（任务看板 / 任务工作台共用，2026-07-17 工时口径升级配套）。
// 展示某禅道任务已提交的工时记录（暂停/完成时平台按天自动结算 + 手工记录），
// 本人的记录可修改或删除（后端用本人网页凭据提交并回读禅道，保持工时归属）。
// 跨 tab 复用 → 弹窗直接挂 body（项目惯例），不依赖任何 tab 的 DOM。

let ctx = { taskId: null, efforts: [], canEditAny: false, editingId: null, deletingId: null };

const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
const round2 = (v) => Math.round((Number(v) || 0) * 100) / 100;
const avatar = (name) => `<span class="fbk-avatar" aria-hidden="true">${escapeHtml(String(name || '?').trim().slice(0, 1).toUpperCase() || '?')}</span>`;
const ICON_EDIT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';
const ICON_DEL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>';

function dayLabel(date) {
  const d = new Date(`${date}T00:00:00`);
  return Number.isNaN(d.getTime()) ? escapeHtml(date || '未知日期') : `${escapeHtml(date)} <span>${WEEK[d.getDay()]}</span>`;
}

function ensureModal() {
  if (document.getElementById('taskEffortModal')) return;
  const wrap = document.createElement('div');
  wrap.id = 'taskEffortModal';
  wrap.className = 'atl-root efm-root';
  wrap.setAttribute('role', 'dialog');
  wrap.setAttribute('aria-modal', 'true');
  wrap.setAttribute('aria-labelledby', 'taskEffortTitle');
  wrap.style.display = 'none';
  wrap.innerHTML = `
    <div class="atl-card efm-card">
      <header class="atl-head">
        <span class="atl-icon" aria-hidden="true"><svg><use href="#i-clock"/></svg></span>
        <div class="atl-head-text">
          <h3 id="taskEffortTitle">工时记录</h3>
          <div class="atl-sub"><span class="atl-target" id="taskEffortTaskLabel"></span><span class="atl-total">数据来自禅道</span></div>
        </div>
        <button type="button" class="atl-close" onclick="window.closeTaskEffortModal()" aria-label="关闭" title="关闭（Esc）"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
      </header>
      <div class="atl-op-slot"></div>
      <div class="efm-summary" id="taskEffortTotal"></div>
      <div class="efm-hint" id="taskEffortHint"></div>
      <div class="atl-body efm-body" id="taskEffortBody"></div>
      <footer class="atl-foot">
        <button type="button" class="secondary" onclick="window.reloadTaskEffortModal()"><svg class="ui-ic" aria-hidden="true"><use href="#i-refresh"/></svg>刷新</button>
        <button type="button" class="secondary" onclick="window.closeTaskEffortModal()">关闭</button>
      </footer>
    </div>`;
  wrap.addEventListener('click', (e) => { if (e.target === wrap) window.closeTaskEffortModal(); });
  wrap.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.isComposing && e.target.closest?.('.efm-edit') && e.target.tagName === 'INPUT') {
      e.preventDefault();
      window.saveTaskEffortEdit(ctx.editingId);
    }
  });
  // Esc：正在编辑时先退出编辑，否则关闭弹窗（捕获阶段，只作用于本弹窗）
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || wrap.style.display === 'none') return;
    if (document.getElementById('taskOpDialog')?.style.display === 'flex') return;
    if (document.querySelector('#omniqaCalendar:not([hidden]), #omniqaSelectMenu:not([hidden])')) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (ctx.editingId) window.cancelTaskEffortEdit();
    else window.closeTaskEffortModal();
  }, true);
  document.body.appendChild(wrap);
}

function renderSummary() {
  const totalEl = document.getElementById('taskEffortTotal');
  if (!totalEl) return;
  if (!ctx.efforts.length) { totalEl.innerHTML = ''; return; }
  const total = ctx.efforts.reduce((s, e) => s + (Number(e.consumed) || 0), 0);
  const mine = ctx.efforts.filter((e) => e.can_edit);
  const mineTotal = mine.reduce((s, e) => s + (Number(e.consumed) || 0), 0);
  const people = new Set(ctx.efforts.map((e) => e.account || '')).size;
  const days = new Set(ctx.efforts.map((e) => e.date || '')).size;
  const stat = (k, v, cls = '') => `<div class="efm-stat${cls}"><b>${v}</b><span>${k}</span></div>`;
  totalEl.innerHTML = [
    stat('合计工时', `${round2(total)}<small>h</small>`, ' efm-stat--main'),
    stat('记录', `${ctx.efforts.length}<small>条</small>`),
    stat('涉及天数', `${days}<small>天</small>`),
    stat('参与人', `${people}<small>人</small>`),
    mine.length ? stat('我的工时', `${round2(mineTotal)}<small>h</small>`) : '',
  ].join('');
}

function renderRow(e) {
  if (ctx.editingId === e.id) {
    return `
      <div class="efm-row efm-edit">
        <label class="efm-f"><span>日期</span><input id="effortEditDate" type="date" value="${escapeHtml(e.date)}"></label>
        <label class="efm-f efm-f--h"><span>工时（h）</span><input id="effortEditConsumed" type="number" min="0.1" max="999" step="0.1" value="${e.consumed}"></label>
        <label class="efm-f efm-f--work"><span>工作内容</span><input id="effortEditWork" value="${escapeHtml(e.work || '')}" placeholder="工作内容"></label>
        <div class="efm-edit-actions">
          <button type="button" class="secondary" onclick="window.cancelTaskEffortEdit()">取消</button>
          <button type="button" onclick="window.saveTaskEffortEdit(${e.id})">保存</button>
        </div>
      </div>`;
  }
  const canMaintain = !!e.can_edit && ctx.canEditAny;
  const isDeleting = ctx.deletingId === e.id;
  const actions = canMaintain
    ? `<div class="efm-actions">
        <button type="button" class="efm-act" ${isDeleting ? 'disabled' : ''} onclick="window.startTaskEffortEdit(${e.id})" title="修改这条工时记录" aria-label="修改">${ICON_EDIT}<span>修改</span></button>
        <button type="button" class="efm-act efm-act--del" ${isDeleting ? 'disabled' : ''} onclick="window.deleteTaskEffort(${e.id})" title="从禅道删除这条工时记录" aria-label="删除">${ICON_DEL}<span>${isDeleting ? '正在同步…' : '删除'}</span></button>
      </div>`
    : `<span class="efm-ro" title="${e.can_edit ? '未绑定禅道网页登录凭据，暂不可修改' : '仅记录人本人可修改或删除'}"><svg class="ui-ic" aria-hidden="true"><use href="#i-lock"/></svg>${e.can_edit ? '只读' : '仅本人可改'}</span>`;
  return `
    <div class="efm-row${e.can_edit ? ' is-mine' : ''}${isDeleting ? ' is-busy' : ''}">
      <span class="efm-h">${round2(e.consumed)}<small>h</small></span>
      <span class="efm-who">${avatar(e.realname || e.account)}<span>${escapeHtml(e.realname || e.account || '未知')}</span></span>
      <span class="efm-work${e.work ? '' : ' is-empty'}">${escapeHtml(e.work || '未填写工作内容')}</span>
      ${actions}
    </div>`;
}

function renderRows() {
  const body = document.getElementById('taskEffortBody');
  if (!body) return;
  renderSummary();
  if (!ctx.efforts.length) {
    body.innerHTML = '<div class="atl-empty"><svg class="atl-empty-ic" aria-hidden="true"><use href="#i-clock"/></svg><strong>暂无工时记录</strong>暂停或完成任务时，平台会按天自动结算并提交到禅道</div>';
    return;
  }
  // 按日期分组（新 → 旧），组头带当日小计
  const groups = new Map();
  [...ctx.efforts]
    .sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')) || Number(b.id) - Number(a.id))
    .forEach((e) => {
      const key = e.date || '';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(e);
    });
  body.innerHTML = [...groups.entries()].map(([date, rows]) => `
    <section class="efm-day">
      <h4 class="efm-day-label">${dayLabel(date)}<em>${round2(rows.reduce((s, e) => s + (Number(e.consumed) || 0), 0))}h</em></h4>
      ${rows.map(renderRow).join('')}
    </section>`).join('');
  if (ctx.editingId) {
    const input = document.getElementById('effortEditConsumed');
    input?.focus();
    input?.select?.();
  }
}

function renderState(text, isError = false) {
  const body = document.getElementById('taskEffortBody');
  if (body) body.innerHTML = `<div class="atl-empty${isError ? ' efm-error' : ''}">${isError ? '<svg class="atl-empty-ic" aria-hidden="true"><use href="#i-alert"/></svg>' : '<span class="wb-spin" aria-hidden="true"></span>'}<span>${escapeHtml(text)}</span></div>`;
}

async function loadEfforts() {
  const snapshot = ctx;
  const hint = document.getElementById('taskEffortHint');
  renderState('正在从禅道拉取工时记录…');
  try {
    const res = await (await api(`/workbench/tasks/${ctx.taskId}/efforts`)).json();
    if (ctx !== snapshot) return;
    if (!res.ok) {
      renderState(res.error || '拉取工时记录失败', true);
      return;
    }
    ctx.efforts = res.efforts || [];
    ctx.canEditAny = !!res.can_edit_any;
    if (hint) {
      hint.classList.toggle('is-readonly', !ctx.canEditAny);
      hint.textContent = ctx.canEditAny
        ? '暂停 / 完成任务时平台按天自动结算并提交禅道；本人的记录可修改或删除，结果以禅道回读为准。'
        : '未绑定禅道网页登录凭据，仅可查看（修改或删除工时必须以本人身份同步禅道）。';
    }
    renderRows();
  } catch (err) {
    if (ctx !== snapshot) return;
    renderState(err.message || '拉取工时记录失败', true);
  }
}

window.openTaskEffortModal = async function openTaskEffortModal(taskId) {
  ensureModal();
  ctx = { taskId, editingId:null, deletingId:null, efforts:[], canEditAny:false };
  document.querySelector("#taskEffortModal .wb-operation-status")?.remove();
  const label = document.getElementById('taskEffortTaskLabel');
  if (label) label.textContent = `任务 #${taskId}`;
  const totalEl = document.getElementById('taskEffortTotal');
  if (totalEl) totalEl.innerHTML = '';
  const hint = document.getElementById('taskEffortHint');
  if (hint) hint.textContent = '';
  const modal = document.getElementById('taskEffortModal');
  prepareOperationModal(modal);
  modal.style.display = 'flex';
  await loadEfforts();
};

window.closeTaskEffortModal = function closeTaskEffortModal() {
  const modal = document.getElementById('taskEffortModal');
  if (modal) modal.style.display = 'none';
};

window.reloadTaskEffortModal = function reloadTaskEffortModal() {
  ctx.editingId = null;
  ctx.deletingId = null;
  return loadEfforts();
};

window.startTaskEffortEdit = function startTaskEffortEdit(effortId) {
  ctx.editingId = effortId;
  renderRows();
};

window.cancelTaskEffortEdit = function cancelTaskEffortEdit() {
  ctx.editingId = null;
  renderRows();
};

async function maintainEffort(effortId, method, payload) {
  const snapshot = ctx;
  const taskId = snapshot.taskId;
  let originalStatus = null;
  return runZentaoOperation({
    key: `task:${taskId}`, label: `任务 #${taskId} ${method === 'DELETE' ? '删除' : '修改'}工时`,
    modal: 'taskEffortModal', isCurrent: () => ctx === snapshot,
    restore: () => {
      ctx = snapshot;
      document.getElementById('taskEffortTaskLabel').textContent = `任务 #${taskId}`;
      renderRows();
    },
    run: async () => {
      try { originalStatus = (await (await api(`/zentao/task/${taskId}/detail`)).json()).status; } catch { /* backend verifies before writing */ }
      return (await api(`/workbench/tasks/${taskId}/efforts/${effortId}`, { method, body: payload })).json();
    },
    verify: async () => {
      const result = await (await api(`/workbench/tasks/${taskId}/efforts`)).json();
      if (!result.ok) return null;
      const row = result.efforts.find(e => Number(e.id) === Number(effortId));
      const applied = method === 'DELETE' ? !row : row && row.date === payload.date && Number(row.consumed) === payload.consumed && (row.work || '') === (payload.work || '');
      if (!applied) return null; // A timed-out write may still be executing upstream.
      const task = await (await api(`/zentao/task/${taskId}/detail`)).json();
      return originalStatus && task.status === originalStatus ? { confirmed:true, data:result } : null;
    },
    onSuccess: result => {
      snapshot.efforts = result?.efforts || snapshot.efforts;
      snapshot.canEditAny = result?.can_edit_any ?? snapshot.canEditAny;
      snapshot.editingId = snapshot.deletingId = null;
      if (ctx === snapshot) renderRows();
      document.dispatchEvent(new CustomEvent('zentao-efforts-updated', { detail:{ taskId } }));
    },
  });
}

window.saveTaskEffortEdit = async function saveTaskEffortEdit(effortId) {
  const date = document.getElementById('effortEditDate')?.value;
  const consumed = Number(document.getElementById('effortEditConsumed')?.value);
  const work = document.getElementById('effortEditWork')?.value || '';
  if (!date || !Number.isFinite(consumed) || consumed <= 0 || consumed > 999) {
    window.showMessage?.('请选择日期并填写 0～999 小时之间的有效工时', 'error'); return;
  }
  return maintainEffort(effortId, 'PUT', { date, consumed, work });
};

window.deleteTaskEffort = async function deleteTaskEffort(effortId) {
  const effort = ctx.efforts.find(e => Number(e.id) === Number(effortId));
  if (!effort?.can_edit || !ctx.canEditAny || operationPending(`task:${ctx.taskId}`)) return;
  const ok = await confirmTaskDialog({
    icon: 'i-alert', tone: 'red', title: '删除工时记录', danger: true, okText: '确认删除',
    sub: `${effort.date} · ${effort.consumed}h${effort.work ? ` · ${effort.work}` : ''}`,
    message: '删除会同步到禅道，成功后不可撤销。',
  });
  if (!ok) return;
  return maintainEffort(effortId, 'DELETE');
};
