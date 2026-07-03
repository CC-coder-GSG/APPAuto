import { api } from '../api.js';
import { showLoading, hideLoading } from '../components/common.js';

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
  zentao: [],          // 禅道任务镜像（scope=all，一份数据供看板列与底部面板共用）
  zentaoLoaded: false,
  createMode: 'platform', // 新建任务模式：platform | zentao
  ztOptionsMajor: null,   // 已加载表单选项的大版本 id（避免重复拉取）
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
  // 回填记忆的筛选项（指派人 + 状态）
  window.SelectMemory && window.SelectMemory.applyMany(['taskBoardAssigneeFilter', 'taskBoardStatusFilter']);
  const qs = buildQuery();
  try {
    const boardPromise = api('/task-board/tasks' + (qs ? `?${qs}` : '')).then((r) => r.json());
    // 禅道镜像拉不到不影响平台任务看板
    await fetchZentaoTasks().catch((err) => console.warn('[task-board] zentao tasks fetch failed', err));
    const data = await boardPromise;
    state.data = data;
    state.canManage = !!data.can_manage;
    render(data);
    renderZentaoPanel();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载任务看板失败', 'error');
  }
}

function render(data) {
  const createBtn = document.getElementById('taskBoardCreateBtn');
  if (createBtn) createBtn.classList.toggle('hidden', !state.canManage);

  const zt = zentaoForBoard();
  renderSummary(data, zt);
  renderColumns(data, zt);
  renderAssigneeSummary(data);
}

function renderSummary(data, ztItems = []) {
  const wrap = document.getElementById('taskBoardSummary');
  if (!wrap) return;
  const s = data.summary || {};
  const ztCount = { total: ztItems.length, todo: 0, in_progress: 0, blocked: 0, done: 0, deferred: 0 };
  ztItems.forEach((t) => {
    const k = ZT_BOARD_MAP[t.status];
    if (k && k in ztCount) ztCount[k] += 1;
  });
  const cards = [
    { key: 'total', label: '今日任务', value: s.total || 0, bg: '#f1f5f9', color: '#1e293b' },
    { key: 'todo', label: '待处理', value: s.todo || 0, bg: STATUS_META.todo.bg, color: STATUS_META.todo.color },
    { key: 'in_progress', label: '进行中', value: s.in_progress || 0, bg: STATUS_META.in_progress.bg, color: STATUS_META.in_progress.color },
    { key: 'blocked', label: '阻塞', value: s.blocked || 0, bg: STATUS_META.blocked.bg, color: STATUS_META.blocked.color },
    { key: 'done', label: '已完成', value: s.done || 0, bg: STATUS_META.done.bg, color: STATUS_META.done.color },
    { key: 'deferred', label: '延期', value: s.deferred || 0, bg: STATUS_META.deferred.bg, color: STATUS_META.deferred.color },
  ];
  wrap.innerHTML = cards.map((c) => {
    const zt = ztCount[c.key] || 0;
    return `
    <div style="min-width:120px; padding:10px 14px; background:${c.bg}; color:${c.color}; border-radius:8px; border:1px solid rgba(0,0,0,.06);">
      <div style="font-size:11px; letter-spacing:0.04em;">${c.label}</div>
      <div style="font-size:22px; font-weight:700;">${c.value + zt}</div>
      ${zt > 0 ? `<div style="font-size:10px; opacity:.75;">含禅道 ${zt}</div>` : ''}
    </div>
  `;
  }).join('');
}

function renderColumns(data, ztItems = []) {
  const root = document.getElementById('taskBoardColumns');
  if (!root) return;
  const columns = data.columns || {};
  const ztByStatus = {};
  ztItems.forEach((t) => {
    const k = ZT_BOARD_MAP[t.status];
    if (k) (ztByStatus[k] = ztByStatus[k] || []).push(t);
  });
  root.innerHTML = STATUS_ORDER.map((statusKey) => {
    const meta = STATUS_META[statusKey];
    const items = columns[statusKey] || [];
    const zt = ztByStatus[statusKey] || [];
    const count = items.length + zt.length;
    return `
      <div style="background:${meta.bg}; border:1px solid ${meta.border}; border-radius:10px; padding:10px; min-height:180px; display:flex; flex-direction:column;">
        <div class="row" style="justify-content:space-between; align-items:center; margin-bottom:8px;">
          <div style="font-weight:700; color:${meta.color};">${meta.label}</div>
          <div style="font-size:12px; color:${meta.color}; background:#fff; border-radius:999px; padding:2px 8px; border:1px solid ${meta.border};">${count}</div>
        </div>
        <div style="display:flex; flex-direction:column; gap:8px;">
          ${count === 0 ? '<div class="muted" style="text-align:center; padding:16px 0; font-size:12px;">暂无任务</div>'
            : items.map(renderCard).join('') + zt.map(renderZentaoBoardCard).join('')}
        </div>
      </div>
    `;
  }).join('');

  root.querySelectorAll('[data-task-card]').forEach((el) => {
    const idAttr = el.getAttribute('data-task-card') || '';
    if (idAttr.startsWith('zt-')) return; // 禅道任务卡：无平台详情弹窗
    el.addEventListener('click', () => openEdit(Number(idAttr)));
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
  resetZtForm();
}

// ─── 新建禅道任务（模式切换 + 表单）─────────────────────────────────────

function resetZtForm() {
  const ids = ['taskBoardZtName', 'taskBoardZtEstStarted', 'taskBoardZtDeadline', 'taskBoardZtEstimate', 'taskBoardZtDesc'];
  ids.forEach((id) => { const el = document.getElementById(id); if (el) el.value = ''; });
  const type = document.getElementById('taskBoardZtType');
  if (type) type.value = 'test';
  const pri = document.getElementById('taskBoardZtPri');
  if (pri) pri.value = '3';
  ['taskBoardZtParent', 'taskBoardZtAssignee', 'taskBoardZtStory'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = `<option value="">${id === 'taskBoardZtParent' ? '无（顶层任务）' : id === 'taskBoardZtAssignee' ? '不指派' : '无'}</option>`;
  });
  const hint = document.getElementById('taskBoardZtHint');
  if (hint) hint.textContent = '';
  state.ztOptionsMajor = null;
}

function setCreateMode(mode) {
  state.createMode = mode;
  const sel = document.getElementById('taskBoardCreateMode');
  if (sel) sel.value = mode;
  document.getElementById('taskBoardFormZentao')?.classList.toggle('hidden', mode !== 'zentao');
  document.getElementById('taskBoardFormPlatform')?.classList.toggle('hidden', mode === 'zentao');
}

export function toggleCreateMode() {
  const mode = document.getElementById('taskBoardCreateMode')?.value || 'platform';
  setCreateMode(mode);
  if (mode === 'zentao') {
    populateZtExecSelect();
    const majorId = document.getElementById('taskBoardZtExec')?.value;
    if (majorId && String(state.ztOptionsMajor) !== String(majorId)) loadZtFormOptions();
  }
}

// 所属执行 = 当前软件下绑定了禅道执行的大版本（与禅道项目同步）
function populateZtExecSelect() {
  const sel = document.getElementById('taskBoardZtExec');
  if (!sel) return;
  const majors = (window.versions || (window.state && window.state.versions) || [])
    .filter((v) => v.version_type === 'major' && v.zentao_execution_id);
  const current = sel.value;
  sel.innerHTML = '<option value="">请选择执行</option>' +
    majors.map((v) => `<option value="${v.id}">${escapeHtml(v.version_no)}</option>`).join('');
  sel.value = current || '';
  if (!sel.value && majors.length === 1) {
    sel.value = String(majors[0].id); // 只有一个执行时直接选中
    loadZtFormOptions();
  }
}

export async function loadZtFormOptions() {
  const majorId = document.getElementById('taskBoardZtExec')?.value;
  const hint = document.getElementById('taskBoardZtHint');
  const parentSel = document.getElementById('taskBoardZtParent');
  const assigneeSel = document.getElementById('taskBoardZtAssignee');
  const storySel = document.getElementById('taskBoardZtStory');
  if (!majorId) {
    if (parentSel) parentSel.innerHTML = '<option value="">无（顶层任务）</option>';
    if (assigneeSel) assigneeSel.innerHTML = '<option value="">不指派</option>';
    if (storySel) storySel.innerHTML = '<option value="">无</option>';
    state.ztOptionsMajor = null;
    return;
  }
  if (hint) hint.textContent = '正在加载执行的人员/父任务/需求…';
  try {
    const data = await (await api(`/task-board/zentao-task-form-options?major_version_id=${majorId}`)).json();
    state.ztOptionsMajor = majorId;
    if (assigneeSel) {
      assigneeSel.innerHTML = '<option value="">不指派</option>' +
        (data.assignable || []).map((u) => `<option value="${escapeHtml(u.account)}">${escapeHtml(u.realname || u.account)}</option>`).join('');
    }
    if (parentSel) {
      parentSel.innerHTML = '<option value="">无（顶层任务）</option>' +
        (data.parents || []).map((p) => `<option value="${p.id}">#${p.id} ${escapeHtml(p.name || '')}${p.is_parent ? '（父任务）' : ''}</option>`).join('');
    }
    if (storySel) {
      storySel.innerHTML = '<option value="">无</option>' +
        (data.stories || []).map((s) => `<option value="${s.id}">#${s.id} ${escapeHtml(s.title || '')}</option>`).join('');
    }
    if (hint) hint.textContent = (data.errors && data.errors.length) ? `⚠ ${data.errors.join('；')}` : '';
  } catch (err) {
    if (hint) hint.textContent = err.message || '加载表单选项失败';
    state.ztOptionsMajor = null;
  }
}

async function submitZentao() {
  const majorId = document.getElementById('taskBoardZtExec')?.value;
  const name = document.getElementById('taskBoardZtName')?.value.trim();
  if (!majorId) {
    window.showMessage && window.showMessage('请选择所属执行', 'error');
    return;
  }
  if (!name) {
    window.showMessage && window.showMessage('任务名称不能为空', 'error');
    return;
  }
  const estStarted = document.getElementById('taskBoardZtEstStarted')?.value || null;
  const deadline = document.getElementById('taskBoardZtDeadline')?.value || null;
  if (estStarted && deadline && estStarted > deadline) {
    window.showMessage && window.showMessage('预计开始不能晚于截止日期', 'error');
    return;
  }
  const estimateRaw = document.getElementById('taskBoardZtEstimate')?.value;
  const payload = {
    major_version_id: Number(majorId),
    name,
    task_type: document.getElementById('taskBoardZtType')?.value || 'test',
    assigned_to: document.getElementById('taskBoardZtAssignee')?.value || null,
    parent_task_id: Number(document.getElementById('taskBoardZtParent')?.value) || null,
    story: Number(document.getElementById('taskBoardZtStory')?.value) || null,
    est_started: estStarted,
    deadline,
    estimate: estimateRaw ? Number(estimateRaw) : null,
    pri: Number(document.getElementById('taskBoardZtPri')?.value) || 3,
    desc: document.getElementById('taskBoardZtDesc')?.value.trim() || null,
  };
  showLoading('正在创建禅道任务，请稍候…');
  try {
    const res = await (await api('/task-board/zentao-tasks/create', { method: 'POST', body: payload })).json();
    let msg = `禅道任务 #${res.task_id} 已创建`;
    if (res.warnings && res.warnings.length) msg += `（${res.warnings.join('；')}）`;
    window.showMessage && window.showMessage(msg, (res.warnings && res.warnings.length) ? 'info' : 'success');
    closeModal();
    await load();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '创建禅道任务失败', 'error');
  } finally {
    hideLoading();
  }
}

export async function openCreate() {
  if (!state.canManage) {
    window.showMessage && window.showMessage('当前账号无任务派发权限', 'error');
    return;
  }
  if (!state.candidates.length) await fetchCandidates();
  state.editingTaskId = null;
  resetForm();
  setCreateMode('platform');
  document.getElementById('taskBoardModeSwitch')?.classList.remove('hidden');
  document.getElementById('taskBoardModalTitle').innerText = '新建任务';
  openModal();
}

export async function openEdit(taskId) {
  if (!state.candidates.length) await fetchCandidates();
  try {
    const detail = await (await api(`/task-board/tasks/${taskId}`)).json();
    state.editingTaskId = taskId;
    // 编辑的是平台任务：隐藏创建模式切换，固定平台表单
    setCreateMode('platform');
    document.getElementById('taskBoardModeSwitch')?.classList.add('hidden');
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
  // 新建 + 禅道模式 → 走禅道创建
  if (!state.editingTaskId && state.createMode === 'zentao') {
    await submitZentao();
    return;
  }
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

// ─── 禅道任务面板（能力 D）────────────────────────────────────────────────
const ZT_STATUS_META = {
  wait:   { label: '未开始', bg: '#f1f5f9', color: '#475569' },
  doing:  { label: '进行中', bg: '#eff6ff', color: '#1d4ed8' },
  done:   { label: '已完成', bg: '#f0fdf4', color: '#15803d' },
  pause:  { label: '已暂停', bg: '#fefce8', color: '#a16207' },
  cancel: { label: '已取消', bg: '#f1f5f9', color: '#94a3b8' },
  closed: { label: '已关闭', bg: '#f1f5f9', color: '#94a3b8' },
};

// 禅道状态 → 看板分栏（暂停归入"阻塞"栏；已取消/已关闭不进看板，只在下方面板可见）
const ZT_BOARD_MAP = { wait: 'todo', doing: 'in_progress', pause: 'blocked', done: 'done' };

function myUserId() {
  return Number((window.currentUser && window.currentUser.id) || 0);
}

function ztIsMine(t) {
  const me = myUserId();
  return me > 0 && Number(t.assignee_user_id || 0) === me;
}

async function fetchZentaoTasks(opts = {}) {
  const params = new URLSearchParams({ scope: 'all' });
  if (opts.refresh && opts.majorId) {
    params.set('refresh', 'true');
    params.set('major_version_id', String(opts.majorId));
  }
  const data = await (await api('/task-board/zentao-tasks?' + params.toString())).json();
  state.zentao = Array.isArray(data.tasks) ? data.tasks : [];
  state.zentaoLoaded = true;
  return data;
}

// 看板归类：按看板日期（任务日期跨度覆盖当日）+ 看板顶部筛选（指派人/状态/只看我的）
function zentaoForBoard() {
  const date = state.date || todayISO();
  const assignee = document.getElementById('taskBoardAssigneeFilter')?.value;
  const statusFilter = document.getElementById('taskBoardStatusFilter')?.value;
  const mineOnly = document.getElementById('taskBoardMineOnly')?.checked;
  return state.zentao.filter((t) => {
    const mapped = ZT_BOARD_MAP[t.status];
    if (!mapped) return false;
    if (statusFilter && mapped !== statusFilter) return false;
    if (assignee && Number(assignee) !== Number(t.assignee_user_id || 0)) return false;
    if (mineOnly && !ztIsMine(t)) return false;
    const start = t.est_started || t.deadline;
    const end = t.deadline || t.est_started;
    const finishedDay = t.finished_date ? String(t.finished_date).slice(0, 10) : null;
    if (start) {
      if (start <= date && date <= end) return true;
      // 完成日在跨度外（如逾期完成）：完成当天也算
      return t.status === 'done' && finishedDay === date;
    }
    if (finishedDay) return finishedDay === date;
    return date === todayISO(); // 完全没有日期信息的任务：只出现在今天的看板
  });
}

// 禅道任务操作按钮（看板卡片与底部面板共用）：像任务工作台那样开始/暂停/完成/关闭。
// 开始/暂停/完成仅指派人本人；关闭已完成任务额外放开给管理员
// （与后端 /workbench/tasks/{id}/operate 权限一致）。
function ztActionsHtml(t) {
  const mine = ztIsMine(t);
  const isAdmin = !!(window.currentUser && window.currentUser.role === 'admin');
  if (!mine && !(isAdmin && t.status === 'done')) return '';
  const id = t.task_id;
  const btn = (label, action, style) =>
    `<button style="padding:2px 8px; font-size:11px; ${style}" onclick="event.stopPropagation(); window.OmniQATaskBoardTab.ztOperate(${id}, '${action}')">${label}</button>`;
  const parts = [];
  if (mine) {
    if (t.status === 'wait') parts.push(btn('▶ 开始', 'start', 'background:#16a34a;'));
    if (t.status === 'doing') {
      parts.push(btn('⏸ 暂停', 'pause', 'background:#d97706;'));
      parts.push(btn('✅ 完成', 'finish', 'background:#0d9488;'));
    }
    if (t.status === 'pause') {
      parts.push(btn('▶ 继续', 'start', 'background:#16a34a;'));
      parts.push(btn('✅ 完成', 'finish', 'background:#0d9488;'));
    }
  }
  if (t.status === 'done') parts.push(btn('⛔ 关闭', 'close', 'background:#fff; color:#b91c1c; border:1px solid #fca5a5;'));
  return parts.length ? `<div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">${parts.join('')}</div>` : '';
}

export async function ztOperate(taskId, action) {
  const confirmText = { finish: '确认将该任务标记为完成？', close: '确认关闭该任务？' }[action];
  if (confirmText && !window.confirm(confirmText)) return;
  showLoading('正在同步禅道，请稍候…');
  try {
    const res = await (await api(`/workbench/tasks/${taskId}/operate`, {
      method: 'POST', body: { action },
    })).json();
    if (res.ok) window.showMessage && window.showMessage('操作已同步禅道', 'success');
    else window.showMessage && window.showMessage('禅道操作有异常：' + (res.errors || []).join('；'), 'error');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '操作失败', 'error');
  } finally {
    hideLoading();
  }
  try { await fetchZentaoTasks(); } catch { /* 保留旧数据渲染 */ }
  if (state.data) render(state.data);
  renderZentaoPanel();
}

// 看板列中的禅道任务卡
function renderZentaoBoardCard(t) {
  const meta = ZT_STATUS_META[t.status] || { label: t.status || '', bg: '#f1f5f9', color: '#475569' };
  const person = escapeHtml(t.assigned_to_realname || t.assigned_to || '未指派');
  const span = (t.est_started || t.deadline) ? `${t.est_started || '—'} → ${t.deadline || '—'}` : '';
  const preview = window.OmniQAUtils && window.OmniQAUtils.renderPreviewBtn
    ? window.OmniQAUtils.renderPreviewBtn('task', t.task_id)
    : '';
  const actions = ztActionsHtml(t);
  return `
    <div data-task-card="zt-${t.task_id}" style="background:#fff; border:1px solid rgba(15,23,42,.08); border-left:3px solid ${meta.color}; border-radius:8px; padding:10px; box-shadow:0 1px 2px rgba(15,23,42,.04);">
      <div class="row" style="justify-content:space-between; gap:6px; flex-wrap:wrap; margin-bottom:6px;">
        <span class="badge" style="background:#f0fdfa; color:#0f766e; border:1px solid #99f6e4; font-size:11px;">🐲 禅道 #${t.task_id}</span>
        <span class="badge" style="background:${meta.bg}; color:${meta.color}; font-size:11px;">${meta.label}</span>
      </div>
      <div style="font-weight:600; color:#0f172a; line-height:1.4;">${escapeHtml(t.name || '')} ${preview}</div>
      <div class="muted" style="font-size:11px; margin-top:6px; display:flex; justify-content:space-between; gap:6px; flex-wrap:wrap;">
        <span>👤 ${person}</span>
        ${span ? `<span>🗓️ ${escapeHtml(span)}</span>` : ''}
      </div>
      ${actions ? `<div style="margin-top:8px;">${actions}</div>` : ''}
    </div>
  `;
}

function populateZentaoExecSelect() {
  const sel = document.getElementById('taskBoardZentaoExec');
  if (!sel) return;
  const majors = (window.versions || (window.state && window.state.versions) || [])
    .filter((v) => v.version_type === 'major' && v.zentao_execution_id);
  const current = sel.value;
  sel.innerHTML = '<option value="">全部执行</option>' +
    majors.map((v) => `<option value="${v.id}">${escapeHtml(v.version_no)}</option>`).join('');
  sel.value = current || '';
}

function populateZentaoAssigneeSelect() {
  const sel = document.getElementById('taskBoardZentaoAssignee');
  if (!sel) return;
  const current = sel.value;
  const seen = new Map();
  state.zentao.forEach((t) => {
    const key = (t.assigned_to || '').trim();
    if (!key || seen.has(key)) return;
    seen.set(key, t.assigned_to_realname || t.assigned_to);
  });
  sel.innerHTML = '<option value="">全部人员</option>' +
    [...seen.entries()]
      .sort((a, b) => String(a[1]).localeCompare(String(b[1]), 'zh'))
      .map(([acc, name]) => `<option value="${escapeHtml(acc)}">${escapeHtml(name)}</option>`)
      .join('');
  sel.value = current || '';
  if (sel.value !== (current || '')) sel.value = ''; // 选中人已不在列表：回落到全部
}

export async function loadZentao() {
  const panel = document.getElementById('taskBoardZentaoPanel');
  if (!panel || !panel.open) return;  // 折叠时不渲染
  populateZentaoExecSelect();
  const hint = document.getElementById('taskBoardZentaoHint');
  if (!state.zentaoLoaded) {
    try {
      await fetchZentaoTasks();
    } catch (err) {
      if (hint) hint.textContent = err.message || '加载禅道任务失败';
      return;
    }
  }
  populateZentaoAssigneeSelect();
  window.SelectMemory && window.SelectMemory.applyMany(
    ['taskBoardZentaoScope', 'taskBoardZentaoExec', 'taskBoardZentaoStatus', 'taskBoardZentaoAssignee']);
  renderZentaoPanel();
}

// 底部面板：纯客户端筛选（范围/执行/状态/人员都作用在同一份镜像数据上）
export function renderZentaoPanel() {
  const panel = document.getElementById('taskBoardZentaoPanel');
  if (!panel || !panel.open) return;
  const hint = document.getElementById('taskBoardZentaoHint');
  const scope = document.getElementById('taskBoardZentaoScope')?.value || 'mine';
  const majorId = document.getElementById('taskBoardZentaoExec')?.value || '';
  const status = document.getElementById('taskBoardZentaoStatus')?.value || '';
  const person = document.getElementById('taskBoardZentaoAssignee')?.value || '';
  let execId = null;
  if (majorId) {
    const majors = window.versions || (window.state && window.state.versions) || [];
    const v = majors.find((x) => String(x.id) === String(majorId));
    execId = v && v.zentao_execution_id ? Number(v.zentao_execution_id) : -1; // 无映射 → 空结果
  }
  const tasks = state.zentao.filter((t) => {
    if (scope === 'mine' && !ztIsMine(t)) return false;
    if (execId != null && Number(t.execution_id || 0) !== execId) return false;
    if (status && t.status !== status) return false;
    if (person && (t.assigned_to || '').trim() !== person) return false;
    return true;
  });
  renderZentaoTasks(tasks);
  if (hint) hint.textContent = `共 ${tasks.length} 个任务`;
}

export async function refreshZentao() {
  const majorId = document.getElementById('taskBoardZentaoExec')?.value || '';
  const hint = document.getElementById('taskBoardZentaoHint');
  if (hint) hint.textContent = majorId ? '正在从禅道同步…' : '正在从禅道同步全部执行，可能较慢…';
  try {
    if (majorId) {
      await fetchZentaoTasks({ refresh: true, majorId });
    } else {
      await api('/task-board/zentao-tasks/sync', { method: 'POST', body: {} });
      await fetchZentaoTasks();
    }
    populateZentaoAssigneeSelect();
    renderZentaoPanel();
    if (state.data) render(state.data);
    if (hint) hint.textContent = `同步完成，共 ${state.zentao.length} 个任务（缓存）`;
  } catch (err) {
    if (hint) hint.textContent = err.message || '同步禅道任务失败';
  }
}

function renderZentaoTasks(tasks) {
  const root = document.getElementById('taskBoardZentaoList');
  if (!root) return;
  if (!tasks.length) {
    root.innerHTML = '<div class="muted" style="padding:16px 0; text-align:center; font-size:13px;">暂无禅道任务（可点「⟳ 同步禅道」刷新缓存）</div>';
    return;
  }
  // 按指派人分组
  const groups = {};
  tasks.forEach((t) => {
    const key = t.assigned_to_realname || t.assigned_to || '未指派';
    (groups[key] = groups[key] || []).push(t);
  });
  root.innerHTML = Object.entries(groups).map(([person, items]) => `
    <div style="margin-bottom:14px;">
      <div style="font-weight:700; color:#334155; margin-bottom:6px;">👤 ${escapeHtml(person)} <span class="muted" style="font-weight:400; font-size:12px;">(${items.length})</span></div>
      <div style="display:flex; flex-direction:column; gap:6px;">
        ${items.map(renderZentaoTaskRow).join('')}
      </div>
    </div>
  `).join('');
}

function renderZentaoTaskRow(t) {
  const meta = ZT_STATUS_META[t.status] || { label: t.status || '', bg: '#f1f5f9', color: '#475569' };
  // 持续多天：显示日期跨度
  const span = (t.est_started || t.deadline)
    ? `${t.est_started || '—'} → ${t.deadline || '—'}`
    : '';
  const spanIsMultiDay = t.est_started && t.deadline && t.est_started !== t.deadline;
  const hours = [];
  if (t.estimate != null) hours.push(`预计 ${t.estimate}h`);
  if (t.consumed != null && t.consumed > 0) hours.push(`已耗 ${t.consumed}h`);
  if (t.left != null && t.left > 0) hours.push(`剩 ${t.left}h`);
  const preview = window.OmniQAUtils && window.OmniQAUtils.renderPreviewBtn
    ? window.OmniQAUtils.renderPreviewBtn('task', t.task_id)
    : '';
  const actions = ztActionsHtml(t);
  return `
    <div style="background:#fff; border:1px solid #e2e8f0; border-left:3px solid ${meta.color}; border-radius:8px; padding:8px 10px;">
      <div class="row" style="justify-content:space-between; gap:6px; flex-wrap:wrap;">
        <div style="font-weight:600; color:#0f172a;">${escapeHtml(t.name)} ${preview}</div>
        <div class="row" style="gap:6px; flex-wrap:wrap; align-items:center;">
          ${actions}
          <span class="badge" style="background:${meta.bg}; color:${meta.color};">${meta.label}</span>
        </div>
      </div>
      <div class="muted" style="font-size:11px; margin-top:4px; display:flex; gap:10px; flex-wrap:wrap;">
        <span>🏷️ ${escapeHtml(t.execution_name || '')}</span>
        ${span ? `<span style="${spanIsMultiDay ? 'color:#1d4ed8; font-weight:600;' : ''}">🗓️ ${escapeHtml(span)}${spanIsMultiDay ? '（多日）' : ''}</span>` : ''}
        ${hours.length ? `<span>⏱ ${hours.join(' · ')}</span>` : ''}
      </div>
    </div>
  `;
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
  loadZentao,
  refreshZentao,
  renderZentaoPanel,
  ztOperate,
  toggleCreateMode,
  loadZtFormOptions,
};

bindSSE();
