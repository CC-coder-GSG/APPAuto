import { api } from '../api.js';

const STATUS_META = {
  todo:         { label: '待处理', color: '#475569', bg: '#f1f5f9', border: '#cbd5e1' },
  in_progress:  { label: '进行中', color: '#1d4ed8', bg: '#eff6ff', border: '#bfdbfe' },
  blocked:      { label: '阻塞',   color: '#b91c1c', bg: '#fef2f2', border: '#fecaca' },
  done:         { label: '已完成', color: '#15803d', bg: '#f0fdf4', border: '#bbf7d0' },
  deferred:     { label: '延期',   color: '#a16207', bg: '#fefce8', border: '#fde68a' },
};

const PRIORITY_META = {
  low:    { label: '低',   bg: '#f1f5f9', color: '#475569' },
  normal: { label: '普通', bg: '#e0f2fe', color: '#0369a1' },
  high:   { label: '高',   bg: '#fef3c7', color: '#92400e' },
  urgent: { label: '紧急', bg: '#fee2e2', color: '#991b1b' },
};

const STATUS_ORDER = ['todo', 'in_progress', 'blocked', 'done', 'deferred'];

const state = {
  date: null,
  data: null,
  candidates: [],
  editingTaskId: null,
  canManage: false,
  loaded: false,
  sseBound: false,
};

function todayISO() {
  const d = new Date();
  const tz = d.getTimezoneOffset() * 60000;
  return new Date(d - tz).toISOString().slice(0, 10);
}

function ensureDateInput() {
  const input = document.getElementById('taskBoardDate');
  if (!input) return null;
  if (!input.value) input.value = todayISO();
  state.date = input.value;
  return input;
}

function formatDateTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return iso;
  }
}

function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function fetchCandidates() {
  try {
    const data = await (await api('/task-board/team-candidates')).json();
    state.candidates = Array.isArray(data) ? data : [];
  } catch (err) {
    console.warn('[task-board] team candidates fetch failed', err);
    state.candidates = [];
  }
  populateAssigneeSelects();
}

function populateAssigneeSelects() {
  const filter = document.getElementById('taskBoardAssigneeFilter');
  const form = document.getElementById('taskBoardFormAssignee');
  if (filter) {
    const current = filter.value;
    filter.innerHTML = '<option value="">全部</option>' +
      state.candidates.map((u) => `<option value="${u.id}">${escapeHtml(u.display_name || u.username)}</option>`).join('');
    filter.value = current || '';
  }
  if (form) {
    const current = form.value;
    form.innerHTML = '<option value="">不指派</option>' +
      state.candidates.map((u) => `<option value="${u.id}">${escapeHtml(u.display_name || u.username)}</option>`).join('');
    form.value = current || '';
  }
}

function buildQuery() {
  const params = new URLSearchParams();
  if (state.date) params.set('board_date', state.date);
  const assignee = document.getElementById('taskBoardAssigneeFilter')?.value;
  if (assignee) params.set('assignee_id', String(assignee));
  const status = document.getElementById('taskBoardStatusFilter')?.value;
  if (status) params.set('status', status);
  const mine = document.getElementById('taskBoardMineOnly')?.checked;
  if (mine) params.set('mine', 'true');
  const archived = document.getElementById('taskBoardShowArchived')?.checked;
  if (archived) params.set('include_archived', 'true');
  return params.toString();
}

export async function load() {
  ensureDateInput();
  if (!state.candidates.length) await fetchCandidates();
  const qs = buildQuery();
  try {
    const data = await (await api('/task-board/tasks' + (qs ? `?${qs}` : ''))).json();
    state.data = data;
    state.canManage = !!data.can_manage;
    render(data);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载任务看板失败', 'error');
  }
}

function render(data) {
  const createBtn = document.getElementById('taskBoardCreateBtn');
  if (createBtn) createBtn.classList.toggle('hidden', !state.canManage);

  renderSummary(data);
  renderColumns(data);
  renderAssigneeSummary(data);
}

function renderSummary(data) {
  const wrap = document.getElementById('taskBoardSummary');
  if (!wrap) return;
  const s = data.summary || {};
  const cards = [
    { key: 'total', label: '今日任务', value: s.total || 0, bg: '#f1f5f9', color: '#1e293b' },
    { key: 'todo', label: '待处理', value: s.todo || 0, bg: STATUS_META.todo.bg, color: STATUS_META.todo.color },
    { key: 'in_progress', label: '进行中', value: s.in_progress || 0, bg: STATUS_META.in_progress.bg, color: STATUS_META.in_progress.color },
    { key: 'blocked', label: '阻塞', value: s.blocked || 0, bg: STATUS_META.blocked.bg, color: STATUS_META.blocked.color },
    { key: 'done', label: '已完成', value: s.done || 0, bg: STATUS_META.done.bg, color: STATUS_META.done.color },
    { key: 'deferred', label: '延期', value: s.deferred || 0, bg: STATUS_META.deferred.bg, color: STATUS_META.deferred.color },
  ];
  wrap.innerHTML = cards.map((c) => `
    <div style="min-width:120px; padding:10px 14px; background:${c.bg}; color:${c.color}; border-radius:8px; border:1px solid rgba(0,0,0,.06);">
      <div style="font-size:11px; letter-spacing:0.04em;">${c.label}</div>
      <div style="font-size:22px; font-weight:700;">${c.value}</div>
    </div>
  `).join('');
}

function renderColumns(data) {
  const root = document.getElementById('taskBoardColumns');
  if (!root) return;
  const columns = data.columns || {};
  root.innerHTML = STATUS_ORDER.map((statusKey) => {
    const meta = STATUS_META[statusKey];
    const items = columns[statusKey] || [];
    return `
      <div style="background:${meta.bg}; border:1px solid ${meta.border}; border-radius:10px; padding:10px; min-height:180px; display:flex; flex-direction:column;">
        <div class="row" style="justify-content:space-between; align-items:center; margin-bottom:8px;">
          <div style="font-weight:700; color:${meta.color};">${meta.label}</div>
          <div style="font-size:12px; color:${meta.color}; background:#fff; border-radius:999px; padding:2px 8px; border:1px solid ${meta.border};">${items.length}</div>
        </div>
        <div style="display:flex; flex-direction:column; gap:8px;">
          ${items.length === 0 ? '<div class="muted" style="text-align:center; padding:16px 0; font-size:12px;">暂无任务</div>' : items.map(renderCard).join('')}
        </div>
      </div>
    `;
  }).join('');

  root.querySelectorAll('[data-task-card]').forEach((el) => {
    el.addEventListener('click', () => openEdit(Number(el.getAttribute('data-task-card'))));
  });
}

function renderCard(task) {
  const priority = PRIORITY_META[task.priority] || PRIORITY_META.normal;
  const overdueChip = task.overdue
    ? `<span class="badge" style="background:#fee2e2; color:#991b1b; font-size:11px;">已逾期</span>`
    : '';
  const archivedChip = task.archived
    ? `<span class="badge" style="background:#e2e8f0; color:#475569; font-size:11px;">已归档</span>`
    : '';
  const assignee = task.assignee_name ? escapeHtml(task.assignee_name) : '未指派';
  const due = task.due_at ? `截止 ${escapeHtml(formatDateTime(task.due_at))}` : '';
  const targetLine = task.target_label
    ? `<div class="muted" style="font-size:11px; margin-top:4px;">🔗 ${escapeHtml(task.target_label)}</div>`
    : '';
  return `
    <div data-task-card="${task.id}" style="background:#fff; border:1px solid rgba(15,23,42,.08); border-radius:8px; padding:10px; cursor:pointer; box-shadow:0 1px 2px rgba(15,23,42,.04);">
      <div class="row" style="justify-content:space-between; gap:6px; flex-wrap:wrap; margin-bottom:6px;">
        <span class="badge" style="background:${priority.bg}; color:${priority.color}; font-size:11px;">${priority.label}</span>
        ${overdueChip}
        ${archivedChip}
      </div>
      <div style="font-weight:600; color:#0f172a; line-height:1.4;">${escapeHtml(task.title)}</div>
      ${targetLine}
      <div class="muted" style="font-size:11px; margin-top:6px; display:flex; justify-content:space-between; gap:6px;">
        <span>👤 ${assignee}</span>
        <span>${due}</span>
      </div>
    </div>
  `;
}

function renderAssigneeSummary(data) {
  const wrap = document.getElementById('taskBoardAssigneeSummary');
  if (!wrap) return;
  const rows = data.assignee_summary || [];
  if (!rows.length) { wrap.innerHTML = ''; return; }
  wrap.innerHTML = `
    <div class="card" style="box-shadow:none; border:1px solid #e2e8f0; background:#f8fafc;">
      <h3 style="margin-top:0; color:#1e293b;">成员任务概览</h3>
      <table class="qa-table">
        <thead><tr><th>成员</th><th style="width:120px;">未完成</th><th style="width:120px;">已完成</th><th style="width:120px;">合计</th></tr></thead>
        <tbody>
          ${rows.map((r) => `<tr>
            <td>${escapeHtml(r.assignee_name || '未指派')}</td>
            <td>${r.open_count}</td>
            <td>${r.done_count}</td>
            <td>${r.total}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>
  `;
}

// ─── modal ──────────────────────────────────────────────────────────────

function openModal() {
  const modal = document.getElementById('taskBoardModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
}

export function closeModal() {
  const modal = document.getElementById('taskBoardModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.style.display = 'none';
  state.editingTaskId = null;
}

function resetForm() {
  document.getElementById('taskBoardFormTitle').value = '';
  document.getElementById('taskBoardFormDescription').value = '';
  document.getElementById('taskBoardFormAssignee').value = '';
  document.getElementById('taskBoardFormPriority').value = 'normal';
  document.getElementById('taskBoardFormBoardDate').value = state.date || todayISO();
  document.getElementById('taskBoardFormDueAt').value = '';
  document.getElementById('taskBoardFormTargetType').value = '';
  document.getElementById('taskBoardFormTargetId').value = '';
  document.getElementById('taskBoardModalExtra').classList.add('hidden');
  document.getElementById('taskBoardModalArchiveBtn').classList.add('hidden');
  document.getElementById('taskBoardModalCarryBtn').classList.add('hidden');
}

export async function openCreate() {
  if (!state.canManage) {
    window.showMessage && window.showMessage('当前账号无任务派发权限', 'error');
    return;
  }
  if (!state.candidates.length) await fetchCandidates();
  state.editingTaskId = null;
  resetForm();
  document.getElementById('taskBoardModalTitle').innerText = '新建任务';
  openModal();
}

export async function openEdit(taskId) {
  if (!state.candidates.length) await fetchCandidates();
  try {
    const detail = await (await api(`/task-board/tasks/${taskId}`)).json();
    state.editingTaskId = taskId;
    document.getElementById('taskBoardModalTitle').innerText = `任务详情 #${detail.id}`;
    document.getElementById('taskBoardFormTitle').value = detail.title || '';
    document.getElementById('taskBoardFormDescription').value = detail.description || '';
    document.getElementById('taskBoardFormAssignee').value = detail.assignee_id ? String(detail.assignee_id) : '';
    document.getElementById('taskBoardFormPriority').value = detail.priority || 'normal';
    document.getElementById('taskBoardFormBoardDate').value = detail.board_date || todayISO();
    document.getElementById('taskBoardFormDueAt').value = detail.due_at ? detail.due_at.slice(0, 16) : '';
    document.getElementById('taskBoardFormTargetType').value = detail.target_type || '';
    document.getElementById('taskBoardFormTargetId').value = detail.target_id || '';

    document.getElementById('taskBoardModalExtra').classList.remove('hidden');
    document.getElementById('taskBoardModalStatus').value = detail.status || 'todo';
    document.getElementById('taskBoardModalProgress').value = '';
    document.getElementById('taskBoardModalArchiveBtn').classList.toggle('hidden', !state.canManage || detail.archived);
    document.getElementById('taskBoardModalCarryBtn').classList.toggle('hidden', !state.canManage || detail.status === 'done');
    renderTimeline(detail);
    openModal();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载任务详情失败', 'error');
  }
}

function renderTimeline(detail) {
  const tl = document.getElementById('taskBoardModalTimeline');
  if (!tl) return;
  const updates = detail.updates || [];
  if (!updates.length) {
    tl.innerHTML = '<div class="muted">暂无进展记录。</div>';
    return;
  }
  tl.innerHTML = updates.map((u) => `
    <div style="padding:6px 0; border-bottom:1px dashed #e2e8f0;">
      <div style="font-size:12px; color:#475569;">${escapeHtml(u.author_name || '系统')} · ${escapeHtml(formatDateTime(u.created_at))} · 状态：${escapeHtml(STATUS_META[u.status_snapshot]?.label || u.status_snapshot || '-')}</div>
      <div style="font-size:13px; margin-top:2px;">${escapeHtml(u.content)}</div>
    </div>
  `).join('');
}

function readForm() {
  const title = document.getElementById('taskBoardFormTitle').value.trim();
  const description = document.getElementById('taskBoardFormDescription').value.trim() || null;
  const assigneeRaw = document.getElementById('taskBoardFormAssignee').value;
  const priority = document.getElementById('taskBoardFormPriority').value || 'normal';
  const boardDate = document.getElementById('taskBoardFormBoardDate').value || null;
  const dueAtRaw = document.getElementById('taskBoardFormDueAt').value;
  const targetType = document.getElementById('taskBoardFormTargetType').value || null;
  const targetIdRaw = document.getElementById('taskBoardFormTargetId').value;
  return {
    title,
    description,
    assignee_id: assigneeRaw ? Number(assigneeRaw) : null,
    priority,
    board_date: boardDate,
    due_at: dueAtRaw || null,
    target_type: targetType,
    target_id: targetIdRaw ? Number(targetIdRaw) : null,
  };
}

export async function submit() {
  const payload = readForm();
  if (!payload.title) {
    window.showMessage && window.showMessage('任务标题不能为空', 'error');
    return;
  }
  try {
    if (state.editingTaskId) {
      await api(`/task-board/tasks/${state.editingTaskId}`, { method: 'PATCH', body: payload });
      window.showMessage && window.showMessage('任务已更新', 'success');
    } else {
      await api('/task-board/tasks', { method: 'POST', body: payload });
      window.showMessage && window.showMessage('任务已创建', 'success');
    }
    closeModal();
    await load();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '保存任务失败', 'error');
  }
}

export async function submitStatus() {
  if (!state.editingTaskId) return;
  const status = document.getElementById('taskBoardModalStatus').value;
  const progress = document.getElementById('taskBoardModalProgress').value.trim() || null;
  try {
    await api(`/task-board/tasks/${state.editingTaskId}/status`, {
      method: 'PATCH', body: { status, progress },
    });
    window.showMessage && window.showMessage('状态已更新', 'success');
    await openEdit(state.editingTaskId);
    await load();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '更新状态失败', 'error');
  }
}

export async function archive() {
  if (!state.editingTaskId) return;
  if (!confirm('确认归档该任务？归档后默认不会出现在看板。')) return;
  try {
    await api(`/task-board/tasks/${state.editingTaskId}/archive`, { method: 'POST', body: {} });
    window.showMessage && window.showMessage('任务已归档', 'success');
    closeModal();
    await load();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '归档失败', 'error');
  }
}

export async function carryOver() {
  if (!state.editingTaskId) return;
  const d = new Date(state.date || todayISO());
  d.setDate(d.getDate() + 1);
  const target = d.toISOString().slice(0, 10);
  if (!confirm(`确认把该任务顺延到 ${target} 吗？`)) return;
  try {
    await api(`/task-board/tasks/${state.editingTaskId}/carry-over`, {
      method: 'POST', body: { to_date: target },
    });
    window.showMessage && window.showMessage('任务已顺延', 'success');
    closeModal();
    await load();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '顺延失败', 'error');
  }
}

export function shiftDate(days) {
  const input = ensureDateInput();
  if (!input) return;
  const d = new Date(input.value || todayISO());
  d.setDate(d.getDate() + Number(days || 0));
  input.value = d.toISOString().slice(0, 10);
  state.date = input.value;
  load();
}

export function gotoToday() {
  const input = ensureDateInput();
  if (!input) return;
  input.value = todayISO();
  state.date = input.value;
  load();
}

export async function activate() {
  ensureDateInput();
  if (!state.candidates.length) await fetchCandidates();
  await load();
  state.loaded = true;
}

function bindSSE() {
  if (state.sseBound) return;
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;
  const handler = (msg) => {
    const tab = document.getElementById('tab-task-board');
    if (!tab || tab.classList.contains('hidden')) return;
    // Re-fetch matching the current filters/date; the SSE event carries a
    // detail payload but the user's filters may exclude it, so always re-query.
    load().catch(() => {});
    if (state.editingTaskId) {
      const payloadTask = msg?.payload?.task;
      if (payloadTask && payloadTask.id === state.editingTaskId) {
        renderTimeline(payloadTask);
      }
    }
  };
  ['task_board_created', 'task_board_updated', 'task_board_status_changed', 'task_board_archived'].forEach((evt) => {
    window.OmniQASSE.subscribe(evt, handler);
  });
  state.sseBound = true;
}

window.OmniQATaskBoardTab = {
  activate,
  load,
  openCreate,
  openEdit,
  closeModal,
  submit,
  submitStatus,
  archive,
  carryOver,
  shiftDate,
  gotoToday,
};

bindSSE();
