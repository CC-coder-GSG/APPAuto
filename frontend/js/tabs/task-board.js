import { operateTask } from '../components/task-operation.js';
import { confirmTaskAction } from '../components/task-dialog.js?v=20261126-zai-1';
import { confirmDialog } from '../components/dialog.js?v=20261126-zai-1';
import { api } from '../api.js';
import { renderTaskStoryChangeBadge } from '../utils.js?v=20261126-hc-1';
import { showLoading, hideLoading } from '../components/common.js';

const STATUS_META = {
  todo:         { label: '待处理', color: '#475569', bg: '#f1f5f9', border: '#cbd5e1' },
  in_progress:  { label: '进行中', color: '#1d4ed8', bg: '#eff6ff', border: '#bfdbfe' },
  done:         { label: '已完成', color: '#15803d', bg: '#f0fdf4', border: '#bbf7d0' },
  done_unclosed:{ label: '已完成但未关闭', color: '#047857', bg: '#ecfdf5', border: '#a7f3d0' },
  deferred:     { label: '延期',   color: '#a16207', bg: '#fefce8', border: '#fde68a' },
};

const PRIORITY_META = {
  low:    { label: '低',   bg: '#f1f5f9', color: '#475569' },
  normal: { label: '普通', bg: '#e0f2fe', color: '#0369a1' },
  high:   { label: '高',   bg: '#fef3c7', color: '#92400e' },
  urgent: { label: '紧急', bg: '#fee2e2', color: '#991b1b' },
};

// 「已完成但未关闭」是禅道任务补充列：done 任务可与普通日期归类列重复展示，closed 后退出。
// 平台侧遗留的 blocked 任务并入进行中列展示，避免消失。
const STATUS_ORDER = ['todo', 'in_progress', 'done', 'done_unclosed', 'deferred'];

// 2026-11-27 看板改版（weave.css R26 .tbd-*）：列 / 指标 / 成员负载统一用这组状态色
const COL_DOT = { todo: '#a8a29e', in_progress: '#2563eb', done: '#16a34a', done_unclosed: '#0d9488', deferred: '#ca8a04' };
const STATUS_LABEL_EXTRA = { blocked: '阻塞' };

const ic = (name, cls = '') => `<svg class="ui-ic${cls ? ` ${cls}` : ''}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const pad2 = (n) => String(n).padStart(2, '0');

function initialOf(name) {
  return escapeHtml(String(name || '?').trim().slice(0, 1).toUpperCase() || '?');
}

function shortDay(v) {
  const d = v ? String(v).slice(0, 10) : '';
  return d ? `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}` : '—';
}

function fmtDue(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const day = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const time = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  return day === todayISO() ? `今天 ${time}` : `${d.getMonth() + 1}/${d.getDate()} ${time}`;
}

function candidateName(id) {
  const u = state.candidates.find((x) => Number(x.id) === Number(id));
  return u ? (u.display_name || u.username) : '';
}

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
  viewMode: 'day',        // 看板视图：day（按日分列）| month（月历，按任务起止时间铺排）
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
  // done_unclosed 是禅道任务补充列，平台任务无此状态（后端是 SAEnum，传了会报错）；
  // 筛选该列时平台侧改为「不可能匹配」在 render 里置空。
  if (status && status !== 'done_unclosed') params.set('status', status);
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

  // 筛选「已完成但未关闭」时平台任务不可能匹配（平台无该状态），列与汇总置空
  const statusFilter = document.getElementById('taskBoardStatusFilter')?.value;
  const effective = statusFilter === 'done_unclosed' ? { ...data, columns: {}, summary: {} } : data;

  const zt = zentaoForBoard();
  renderSummary(effective, zt);
  renderMembers(effective, zt);
  renderColumns(effective, zt);
  renderMonthView();
  bindBoard();
}

// 指标条：点一下即按该状态筛选，再点「当日任务」清除
function renderSummary(data, ztItems = []) {
  const wrap = document.getElementById('taskBoardSummary');
  if (!wrap) return;
  const s = data.summary || {};
  // 补充列允许重复展示，但总数只统计普通日期归类，避免同一任务重复累计。
  const ztCount = { total: ztItems.filter((t) => !t._supplemental).length, todo: 0, in_progress: 0, done: 0, done_unclosed: 0, deferred: 0 };
  ztItems.forEach((t) => {
    const k = t._boardCol || ZT_BOARD_MAP[t.status];
    if (k && k in ztCount) ztCount[k] += 1;
  });
  const current = document.getElementById('taskBoardStatusFilter')?.value || '';
  const total = (s.total || 0) + ztCount.total;
  const doneAll = (s.done || 0) + ztCount.done;
  const rate = total ? Math.round(doneAll * 100 / total) : 0;
  const isToday = (state.date || todayISO()) === todayISO();
  const tiles = [
    { key: 'todo', value: s.todo || 0 },
    // 平台侧遗留 blocked 任务并入进行中统计
    { key: 'in_progress', value: (s.in_progress || 0) + (s.blocked || 0) },
    { key: 'done', value: s.done || 0 },
    { key: 'done_unclosed', value: 0 },
    { key: 'deferred', value: s.deferred || 0 },
  ];
  wrap.innerHTML = `
    <button type="button" class="tbd-kpi tbd-kpi--total${current ? '' : ' is-active'}" data-tb-status="" aria-pressed="${!current}">
      <span class="tbd-kpi-label">${isToday ? '今日任务' : `${shortDay(state.date)} 任务`}</span>
      <b>${total}</b>
      <span class="tbd-kpi-rate"><span class="tbd-kpi-bar"><i style="width:${rate}%"></i></span><small>完成 ${rate}%</small></span>
    </button>
    ${tiles.map((t) => {
      const zt = ztCount[t.key] || 0;
      const on = current === t.key;
      return `<button type="button" class="tbd-kpi${on ? ' is-active' : ''}" data-tb-status="${t.key}" aria-pressed="${on}" style="--dot:${COL_DOT[t.key]}" title="${on ? '再点一次取消筛选' : '只看这一列'}">
        <span class="tbd-kpi-label"><i class="tbd-dot"></i>${STATUS_META[t.key].label}</span>
        <b>${t.value + zt}</b>
        <small>${zt ? `含禅道 ${zt}` : '&nbsp;'}</small>
      </button>`;
    }).join('')}`;
}

// 成员负载：平台任务 + 当日归列的禅道任务（补充列不重复计）；点成员即按人筛选
function renderMembers(data, ztItems = []) {
  const wrap = document.getElementById('taskBoardMembers');
  if (!wrap) return;
  const columns = data.columns || {};
  const buckets = new Map();
  const add = (key, name, col, clickable) => {
    const b = buckets.get(key) || { key, name: name || '未指派', clickable, todo: 0, in_progress: 0, done: 0, deferred: 0, total: 0 };
    if (col in b) b[col] += 1;
    b.total += 1;
    buckets.set(key, b);
  };
  ['todo', 'in_progress', 'done', 'deferred'].forEach((k) => {
    const items = k === 'in_progress' ? [...(columns.in_progress || []), ...(columns.blocked || [])] : (columns[k] || []);
    items.forEach((t) => add(t.assignee_id ? `u${t.assignee_id}` : 'none', t.assignee_name, k, !!t.assignee_id));
  });
  ztItems.filter((t) => !t._supplemental).forEach((t) => {
    const uid = Number(t.assignee_user_id || 0);
    const key = uid ? `u${uid}` : (t.assigned_to ? `zt:${t.assigned_to}` : 'none');
    add(key, (uid && candidateName(uid)) || t.assigned_to_realname || t.assigned_to, t._boardCol, !!uid);
  });
  const list = [...buckets.values()].sort((a, b) => b.total - a.total || String(a.name).localeCompare(String(b.name), 'zh'));
  const filtered = document.getElementById('taskBoardAssigneeFilter')?.value || '';
  if (!list.length) { wrap.innerHTML = ''; wrap.hidden = true; return; }
  wrap.hidden = false;
  wrap.innerHTML = `
    <div class="tbd-members-head"><b>成员负载</b><small>当天的平台任务与禅道任务</small>${filtered ? `<button type="button" class="tbd-link" data-tb-member="">${ic('x-circle')}查看全部成员</button>` : ''}</div>
    <div class="tbd-member-list">${list.map((m) => {
      const id = m.key.startsWith('u') ? m.key.slice(1) : '';
      const open = m.total - m.done;
      const seg = (k) => (m[k] ? `<i style="width:${(m[k] * 100) / m.total}%; background:${COL_DOT[k]}"></i>` : '');
      const on = id && filtered === id;
      return `<button type="button" class="tbd-member${on ? ' is-active' : ''}" ${m.clickable ? `data-tb-member="${id}"` : 'disabled'} title="${m.clickable ? (on ? '再点一次取消筛选' : '只看这个人的任务') : '禅道账号未绑定平台用户，无法按人筛选'}">
        <span class="tbd-avatar">${initialOf(m.name)}</span>
        <span class="tbd-member-main"><b>${escapeHtml(m.name)}</b><span class="tbd-stack">${seg('done')}${seg('in_progress')}${seg('todo')}${seg('deferred')}</span><small>完成 ${m.done} / ${m.total}${m.deferred ? ` · 延期 ${m.deferred}` : ''}</small></span>
        <span class="tbd-member-num"><b>${open}</b><small>未完成</small></span>
      </button>`;
    }).join('')}</div>`;
}

function renderColumns(data, ztItems = []) {
  const root = document.getElementById('taskBoardColumns');
  if (!root) return;
  const columns = data.columns || {};
  const ztByStatus = {};
  ztItems.forEach((t) => {
    const k = t._boardCol || ZT_BOARD_MAP[t.status];
    if (k) (ztByStatus[k] = ztByStatus[k] || []).push(t);
  });
  const statusFilter = document.getElementById('taskBoardStatusFilter')?.value || '';
  root.innerHTML = STATUS_ORDER.map((statusKey) => {
    const meta = STATUS_META[statusKey];
    // 平台侧遗留 blocked 任务并入进行中列（阻塞列已删除）
    const items = statusKey === 'in_progress'
      ? [...(columns.in_progress || []), ...(columns.blocked || [])]
      : (columns[statusKey] || []);
    const zt = ztByStatus[statusKey] || [];
    const count = items.length + zt.length;
    const dim = statusFilter && statusFilter !== statusKey;
    return `
      <section class="tbd-col tbd-col--${statusKey}${dim ? ' is-dim' : ''}" style="--dot:${COL_DOT[statusKey]}">
        <header class="tbd-col-head"><i class="tbd-dot"></i><b>${meta.label}</b><span class="tbd-col-count">${count}</span></header>
        <div class="tbd-col-body">
          ${count === 0 ? `<div class="tbd-col-empty">${statusKey === 'done_unclosed' ? '没有待关闭的禅道任务' : '暂无任务'}</div>`
            : items.map(renderCard).join('') + zt.map(renderZentaoBoardCard).join('')}
        </div>
      </section>
    `;
  }).join('');
}

function renderCard(task) {
  const pri = PRIORITY_META[task.priority] || PRIORITY_META.normal;
  const assignee = task.assignee_name ? escapeHtml(task.assignee_name) : '未指派';
  return `
    <article data-task-card="${task.id}" class="tbd-card tbd-card--p-${task.priority || 'normal'}${task.overdue ? ' is-overdue' : ''}${task.archived ? ' is-archived' : ''}" tabindex="0" role="button" aria-label="查看任务：${escapeHtml(task.title)}">
      ${task.priority !== 'normal' || task.overdue || task.archived ? `<div class="tbd-card-top">
        ${task.priority !== 'normal' ? `<span class="tbd-pri tbd-pri--${task.priority}">${pri.label}优先级</span>` : ''}
        ${task.overdue ? `<span class="tbd-chip tbd-chip--red">${ic('alert')}已逾期</span>` : ''}
        ${task.archived ? '<span class="tbd-chip">已归档</span>' : ''}
      </div>` : ''}
      <div class="tbd-card-title">${escapeHtml(task.title)}</div>
      ${task.target_label ? `<div class="tbd-card-link">${ic('external')}<span>${escapeHtml(task.target_label)}</span></div>` : ''}
      <div class="tbd-card-meta">
        <span class="tbd-who"><span class="tbd-avatar tbd-avatar--sm">${initialOf(task.assignee_name || '未')}</span>${assignee}</span>
        ${task.due_at ? `<span class="tbd-due${task.overdue ? ' is-late' : ''}">${ic('clock')}${escapeHtml(fmtDue(task.due_at))}</span>` : ''}
      </div>
    </article>
  `;
}

// 看板交互统一委托：指标筛选 / 成员筛选 / 打开平台任务 / 月历跳转到某天
function bindBoard() {
  const tab = document.getElementById('tab-task-board');
  if (!tab || tab.dataset.tbdBound) return;
  tab.dataset.tbdBound = '1';
  const setFilter = (id, value) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = value;
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  tab.addEventListener('click', (e) => {
    const kpi = e.target.closest('[data-tb-status]');
    if (kpi) {
      const current = document.getElementById('taskBoardStatusFilter')?.value || '';
      const next = kpi.dataset.tbStatus;
      setFilter('taskBoardStatusFilter', next && next === current ? '' : next);
      return;
    }
    const member = e.target.closest('[data-tb-member]');
    if (member) {
      const current = document.getElementById('taskBoardAssigneeFilter')?.value || '';
      const next = member.dataset.tbMember;
      setFilter('taskBoardAssigneeFilter', next && next === current ? '' : next);
      return;
    }
    const day = e.target.closest('[data-tb-day]');
    if (day) {
      const input = document.getElementById('taskBoardDate');
      if (input) input.value = day.dataset.tbDay;
      state.date = day.dataset.tbDay;
      toggleViewMode('day');
      return;
    }
    if (e.target.closest('button, a, input, select')) return;
    const card = e.target.closest('[data-task-card]');
    const idAttr = card?.getAttribute('data-task-card') || '';
    if (card && idAttr && !idAttr.startsWith('zt-')) openEdit(Number(idAttr));
  });
  tab.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest?.('[data-task-card]');
    if (!card || e.target !== card) return;
    const idAttr = card.getAttribute('data-task-card') || '';
    if (idAttr.startsWith('zt-')) return;
    e.preventDefault();
    openEdit(Number(idAttr));
  });
}

// ─── modal ──────────────────────────────────────────────────────────────

let modalCloseTimer = 0;

function bindModal() {
  const modal = document.getElementById('taskBoardModal');
  if (!modal || modal.dataset.tbdBound) return;
  modal.dataset.tbdBound = '1';
  modal.addEventListener('click', (e) => {
    if (e.target.closest('[data-tb-close]')) { closeModal(); return; }
    const mode = e.target.closest('[data-tb-mode]');
    if (mode) {
      const sel = document.getElementById('taskBoardCreateMode');
      if (sel) sel.value = mode.dataset.tbMode;
      toggleCreateMode();
    }
  });
  // 捕获阶段：组合框下拉 / 确认框打开时 Esc 先交给它们
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || modal.classList.contains('hidden')) return;
    if (document.querySelector('.zt-combo-list:not(.hidden)')) return;
    const dlg = document.getElementById('taskOpDialog');
    if (dlg && dlg.offsetParent !== null) return;
    if (document.querySelector('#omniqaSelectMenu:not([hidden]), #omniqaCalendar:not([hidden])')) return;
    e.preventDefault();
    closeModal();
  }, true);
}

function openModal() {
  const modal = document.getElementById('taskBoardModal');
  if (!modal) return;
  bindModal();
  clearTimeout(modalCloseTimer);
  const wasOpen = !modal.classList.contains('hidden');
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  const html = document.documentElement;
  if (!html.classList.contains('sd-locked')) html.classList.toggle('sd-gutter', window.innerWidth > html.clientWidth);
  html.classList.add('sd-locked', 'tbd-locked');
  if (!wasOpen) {
    modal.querySelector('.tbd-modal-body')?.scrollTo(0, 0);
    requestAnimationFrame(() => requestAnimationFrame(() => modal.classList.add('is-open')));
    modal.querySelector('.tbd-modal-panel')?.focus({ preventScroll: true });
  }
}

export function closeModal() {
  const modal = document.getElementById('taskBoardModal');
  if (!modal) return;
  modal.classList.remove('is-open');
  state.editingTaskId = null;
  const html = document.documentElement;
  html.classList.remove('tbd-locked');
  if (!document.querySelector('.sd-root:not([hidden])')) html.classList.remove('sd-locked', 'sd-gutter');
  const done = () => { modal.classList.add('hidden'); modal.style.display = 'none'; };
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) done();
  else modalCloseTimer = setTimeout(done, 200);
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

// 模糊搜索组合框：输入过滤 + 点击/回车选择。下拉列表很长（人员/需求/父任务），
// 原生 select 一个个翻不现实。value 只在明确选择时落定；失焦时唯一匹配自动选中，
// 匹配不到则清空文本，保证提交值与所见一致。
const ztCombos = {};

function ztComboInit(key, inputId, listId) {
  if (ztCombos[key]) return ztCombos[key];
  const input = document.getElementById(inputId);
  const list = document.getElementById(listId);
  if (!input || !list) return null;
  const combo = { input, list, items: [], value: '' };
  ztCombos[key] = combo;

  const matchOf = (q) => combo.items.filter((it) => (it.label + ' ' + it.value).toLowerCase().includes(q));
  const render = () => {
    const q = input.value.trim().toLowerCase();
    const matched = q ? matchOf(q) : combo.items;
    const rows = matched.slice(0, 100);
    list.innerHTML = rows.length
      ? rows.map((it) => `<div class="zt-combo-item" data-value="${escapeHtml(String(it.value))}">${escapeHtml(it.label)}</div>`).join('') +
        (matched.length > 100 ? `<div class="muted" style="padding:6px 10px; font-size:12px;">还有 ${matched.length - 100} 条，继续输入缩小范围…</div>` : '')
      : '<div class="muted" style="padding:6px 10px; font-size:12px;">无匹配项</div>';
  };
  const pick = (value) => {
    const found = combo.items.find((it) => String(it.value) === String(value));
    combo.value = found ? String(found.value) : '';
    input.value = found ? found.label : '';
    list.classList.add('hidden');
  };

  input.addEventListener('input', () => { combo.value = ''; render(); list.classList.remove('hidden'); });
  input.addEventListener('focus', () => { render(); list.classList.remove('hidden'); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { list.classList.add('hidden'); return; }
    if (e.key === 'Enter') {
      e.preventDefault();
      const first = list.querySelector('[data-value]');
      if (first && !list.classList.contains('hidden')) pick(first.getAttribute('data-value'));
    }
  });
  input.addEventListener('blur', () => setTimeout(() => {
    list.classList.add('hidden');
    if (combo.value) return;             // 已经点选过
    const q = input.value.trim().toLowerCase();
    if (!q) { combo.value = ''; return; } // 留空 = 不选
    const matched = matchOf(q);
    if (matched.length === 1) pick(matched[0].value);
    else { input.value = ''; combo.value = ''; } // 含糊不清就清空，避免提交错人/错需求
  }, 150));
  // mousedown 先于 blur 触发，保证点击项能生效
  list.addEventListener('mousedown', (e) => {
    const item = e.target.closest('[data-value]');
    if (!item) return;
    e.preventDefault();
    pick(item.getAttribute('data-value'));
  });
  return combo;
}

function ztComboSetItems(key, items) {
  const c = ztCombos[key];
  if (!c) return;
  c.items = Array.isArray(items) ? items : [];
  c.value = '';
  c.input.value = '';
}

function ztComboValue(key) {
  return ztCombos[key] ? ztCombos[key].value : '';
}

function ztInitCombos() {
  ztComboInit('parent', 'taskBoardZtParentInput', 'taskBoardZtParentList');
  ztComboInit('assignee', 'taskBoardZtAssigneeInput', 'taskBoardZtAssigneeList');
  ztComboInit('story', 'taskBoardZtStoryInput', 'taskBoardZtStoryList');
}

function resetZtForm() {
  const ids = ['taskBoardZtName', 'taskBoardZtEstStarted', 'taskBoardZtDeadline', 'taskBoardZtEstimate', 'taskBoardZtDesc'];
  ids.forEach((id) => { const el = document.getElementById(id); if (el) el.value = ''; });
  const type = document.getElementById('taskBoardZtType');
  if (type) type.value = 'test';
  const pri = document.getElementById('taskBoardZtPri');
  if (pri) pri.value = '3';
  ztInitCombos();
  ['parent', 'assignee', 'story'].forEach((key) => ztComboSetItems(key, ztCombos[key] ? ztCombos[key].items : []));
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
  document.querySelectorAll('#taskBoardModeSwitch [data-tb-mode]').forEach((b) => {
    const on = b.dataset.tbMode === mode;
    b.classList.toggle('is-active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
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
  ztInitCombos();
  if (!majorId) {
    ['parent', 'assignee', 'story'].forEach((key) => ztComboSetItems(key, []));
    state.ztOptionsMajor = null;
    return;
  }
  if (hint) hint.textContent = '正在加载执行的人员/父任务/需求…';
  try {
    const data = await (await api(`/task-board/zentao-task-form-options?major_version_id=${majorId}`)).json();
    state.ztOptionsMajor = majorId;
    ztComboSetItems('assignee', (data.assignable || []).map((u) => ({
      value: u.account,
      label: `${u.realname || u.account}（${u.account}）`,
    })));
    ztComboSetItems('parent', (data.parents || []).map((p) => ({
      value: p.id,
      label: `#${p.id} ${p.name || ''}${p.is_parent ? '（父任务）' : ''}`,
    })));
    ztComboSetItems('story', (data.stories || []).map((s) => ({
      value: s.id,
      label: `#${s.id} ${s.title || ''}`,
    })));
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
  if (!estStarted || !deadline) {
    window.showMessage && window.showMessage('请填写预计开始和截止日期', 'error');
    return;
  }
  if (!document.getElementById('taskBoardZtEstStarted').checkValidity()
      || !document.getElementById('taskBoardZtDeadline').checkValidity()) {
    window.showMessage && window.showMessage('请选择有效的预计开始和截止日期', 'error');
    return;
  }
  if (estStarted && deadline && estStarted > deadline) {
    window.showMessage && window.showMessage('预计开始不能晚于截止日期', 'error');
    return;
  }
  const estimateRaw = document.getElementById('taskBoardZtEstimate')?.value;
  const payload = {
    major_version_id: Number(majorId),
    name,
    task_type: document.getElementById('taskBoardZtType')?.value || 'test',
    assigned_to: ztComboValue('assignee') || null,
    parent_task_id: Number(ztComboValue('parent')) || null,
    story: Number(ztComboValue('story')) || null,
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
  const kicker = document.getElementById('taskBoardModalKicker');
  if (kicker) kicker.textContent = '任务看板';
  openModal();
  setTimeout(() => document.getElementById('taskBoardFormTitle')?.focus({ preventScroll: true }), 80);
}

export async function openEdit(taskId) {
  if (!state.candidates.length) await fetchCandidates();
  try {
    const detail = await (await api(`/task-board/tasks/${taskId}`)).json();
    state.editingTaskId = taskId;
    // 编辑的是平台任务：隐藏创建模式切换，固定平台表单
    setCreateMode('platform');
    document.getElementById('taskBoardModeSwitch')?.classList.add('hidden');
    document.getElementById('taskBoardModalTitle').innerText = detail.title || `任务 #${detail.id}`;
    const kicker = document.getElementById('taskBoardModalKicker');
    if (kicker) kicker.textContent = `平台任务 #${detail.id} · ${STATUS_META[detail.status]?.label || STATUS_LABEL_EXTRA[detail.status] || detail.status || ''}`;
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
    tl.innerHTML = '<div class="tbd-tl-empty">暂无进展记录</div>';
    return;
  }
  tl.innerHTML = `<div class="tbd-tl-title">变更时间线<small>${updates.length} 条</small></div><ol class="tbd-tl">${updates.map((u) => {
    const label = STATUS_META[u.status_snapshot]?.label || STATUS_LABEL_EXTRA[u.status_snapshot] || u.status_snapshot || '';
    return `<li style="--dot:${COL_DOT[u.status_snapshot] || '#a8a29e'}">
      <i class="tbd-dot"></i>
      <div class="tbd-tl-main">
        <div class="tbd-tl-meta"><b>${escapeHtml(u.author_name || '系统')}</b><span>${escapeHtml(formatDateTime(u.created_at))}</span>${label ? `<span class="tbd-tl-status">${escapeHtml(label)}</span>` : ''}</div>
        <p>${escapeHtml(u.content)}</p>
      </div>
    </li>`;
  }).join('')}</ol>`;
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
  if (!(await confirmDialog({ icon: 'i-file', title: '归档任务', message: '归档后该任务默认不再出现在看板上。', okText: '归档' }))) return;
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
  if (!(await confirmDialog({ icon: 'i-clock', title: '顺延任务', message: `把该任务顺延到 ${target}。`, okText: '确认顺延' }))) return;
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
  // 月视图下 ◀▶ 按月翻页（跳到目标月 1 号，避免 31 号翻到下下月）
  if (state.viewMode === 'month') {
    d.setDate(1);
    d.setMonth(d.getMonth() + Number(days || 0));
  } else {
    d.setDate(d.getDate() + Number(days || 0));
  }
  input.value = d.toISOString().slice(0, 10);
  state.date = input.value;
  load();
}

// 日视图 ⇄ 月视图切换：月视图隐藏分列看板与成员概览，显示月历
export function toggleViewMode(mode) {
  state.viewMode = mode === 'month' ? 'month' : 'day';
  const isMonth = state.viewMode === 'month';
  document.querySelectorAll('#taskBoardViewMode [data-tb-view]').forEach((b) => {
    const on = b.dataset.tbView === state.viewMode;
    b.classList.toggle('is-active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  const prev = document.getElementById('taskBoardPrevBtn');
  const next = document.getElementById('taskBoardNextBtn');
  if (prev) { prev.title = isMonth ? '上个月' : '上一天'; prev.setAttribute('aria-label', prev.title); }
  if (next) { next.title = isMonth ? '下个月' : '下一天'; next.setAttribute('aria-label', next.title); }
  document.getElementById('taskBoardSummary')?.classList.toggle('hidden', isMonth);
  document.getElementById('taskBoardMembers')?.classList.toggle('hidden', isMonth);
  document.querySelector('#tab-task-board .tbd-board-wrap')?.classList.toggle('hidden', isMonth);
  document.getElementById('taskBoardMonthView')?.classList.toggle('hidden', !isMonth);
  // 日期可能被月历点选改过：重新按当前日期拉取
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
  window.OmniQASSE.subscribe('zentao_task_changed', () => {
    const tab = document.getElementById('tab-task-board');
    if (!tab || tab.classList.contains('hidden')) return;
    reloadZentaoFromMirror().catch(() => {});
  });
  state.sseBound = true;
}

// ─── 禅道任务面板（能力 D）────────────────────────────────────────────────
const ZT_STATUS_META = {
  wait:   { label: '未开始', bg: '#f1f5f9', color: '#475569' },
  doing:  { label: '进行中', bg: '#eff6ff', color: '#1d4ed8' },
  changed:{ label: '进行中', bg: '#eff6ff', color: '#1d4ed8' },
  done:   { label: '已完成', bg: '#f0fdf4', color: '#15803d' },
  pause:  { label: '已暂停', bg: '#fefce8', color: '#a16207' },
  cancel: { label: '已取消', bg: '#f1f5f9', color: '#94a3b8' },
  closed: { label: '已关闭', bg: '#f1f5f9', color: '#94a3b8' },
};

// 禅道状态 → 普通看板分栏（暂停归入进行中；关闭/取消任务不进入普通列）。
// done 任务还会由 zentaoForBoard 复制到「已完成但未关闭」补充列。
const ZT_BOARD_MAP = { wait: 'todo', doing: 'in_progress', changed: 'in_progress', pause: 'in_progress', done: 'done' };

// 按看板日期推导禅道任务归列（延期与历史回看都在这里算）：
// - 未完成(wait/doing/pause)且看板日期已过截止 → 「延期」列，此后每天持续出现直到完成；
// - 逾期完成的任务：截止日之后、完成日之前的日期仍归「延期」列，完成日起按现状归列；
// - 完成日之前的日期按「当日时点」推导（实际开始日前=待处理，之后=进行中）。
// 归列只依赖禅道的时间事实（real_started / finished_date / deadline），
// 所以之后改任务状态不会改写既往日期看板的归列（与设计一致）。
function ztColumnForDate(t, date) {
  const mapped = ZT_BOARD_MAP[t.status];
  if (!mapped) return null;
  const deadline = dayOf(t.deadline);
  const finishedDay = dayOf(t.finished_date);
  const startedDay = dayOf(t.real_started);
  const isFinished = t.status === 'done' || t.status === 'closed' || t.status === 'cancel';
  if (isFinished) {
    if (!finishedDay || date >= finishedDay) return mapped;
    // date < 完成日：当时尚未完成，按当时的时点推导
    if (deadline && date > deadline) return 'deferred';
    return startedDay && date >= startedDay ? 'in_progress' : 'todo';
  }
  if (deadline && date > deadline) return 'deferred';
  if (startedDay && date < startedDay) return 'todo';
  return mapped;
}

function myUserId() {
  return Number((window.currentUser && window.currentUser.id) || 0);
}

function ztIsMine(t) {
  const me = myUserId();
  const mappedToMe = me > 0 && Number(t.assignee_user_id || 0) === me;
  const myAccount = String(window.currentUser?.zentao_account || '').trim().toLowerCase();
  const taskAccount = String(t.assigned_to || '').trim().toLowerCase();
  return mappedToMe || (!!myAccount && myAccount === taskAccount);
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

// 看板归类：按看板日期（任务日期跨度覆盖当日 + 延期滞留）+ 看板顶部筛选（指派人/状态/只看我的）
// 返回的任务带 _boardCol（按看板日期推导的归列），渲染与汇总都用它。
function zentaoForBoard() {
  const date = state.date || todayISO();
  const assignee = document.getElementById('taskBoardAssigneeFilter')?.value;
  const statusFilter = document.getElementById('taskBoardStatusFilter')?.value;
  const mineOnly = document.getElementById('taskBoardMineOnly')?.checked;
  const out = [];
  state.zentao.forEach((t) => {
    const assigneeMatched = !assignee || Number(assignee) === Number(t.assignee_user_id || 0);
    const mineMatched = !mineOnly || ztIsMine(t);

    // 补充列不受看板日期限制：任务只要在禅道处于 done，就持续展示到被关闭为止。
    // 无状态筛选时允许它与普通日期归类列重复；显式筛选该列时只展示补充项。
    if (
      t.status === 'done'
      && assigneeMatched
      && mineMatched
      && (!statusFilter || statusFilter === 'done_unclosed')
    ) {
      out.push({ ...t, _boardCol: 'done_unclosed', _supplemental: true });
    }
    if (statusFilter === 'done_unclosed') return;

    const col = ztColumnForDate(t, date);
    if (!col) return;
    if (statusFilter && col !== statusFilter) return;
    if (!assigneeMatched || !mineMatched) return;
    const start = t.est_started || t.deadline;
    const end = t.deadline || t.est_started;
    const deadline = dayOf(t.deadline);
    const finishedDay = dayOf(t.finished_date);
    const isFinished = t.status === 'done' || t.status === 'closed' || t.status === 'cancel';
    const visible = (() => {
      // 延期滞留：过截止仍未完成的任务，在截止日之后每天都出现（归延期列）；
      // 逾期完成的任务补齐「截止日 → 完成日」之间的日期，保证历史回看一致。
      if (deadline && date > deadline) {
        if (!isFinished) return true;
        if (finishedDay && date < finishedDay) return true;
      }
      if (start) {
        if (start <= date && date <= end) return true;
        // 完成/关闭日在跨度外（如逾期完成）：完成当天也算
        return isFinished && finishedDay === date;
      }
      if (finishedDay) return finishedDay === date;
      return date === todayISO(); // 完全没有日期信息的任务：只出现在今天的看板
    })();
    if (visible) out.push({ ...t, _boardCol: col });
  });
  return out;
}

// ─── 月视图：按任务起止时间在月历上铺排 ─────────────────────────────────
// 每个任务按「实际开始(real_started)/计划开始(est_started) → 完成(finished_date)/
// 截止(deadline)/今天」的跨度投射到每一天：开始日标「开始」，结束日标「结束」，
// 中间标「进行中」，当天始末标「开始->结束」并注明工时。数据来自禅道镜像，
// 每次加载/刷新按最新起止时间实时重排。

const MONTH_MARK_META = {
  start:    { label: '开始',   bg: '#dcfce7', color: '#166534' },
  ongoing:  { label: '进行中', bg: '#dbeafe', color: '#1d4ed8' },
  end:      { label: '结束',   bg: '#f3e8ff', color: '#7c3aed' },
  same_day: { label: '开始->结束', bg: '#fef3c7', color: '#92400e' },
  planned:  { label: '计划开始', bg: '#f1f5f9', color: '#64748b' },
  delayed:  { label: '延期',   bg: '#fef9c3', color: '#a16207' },
};

function dayOf(v) {
  return v ? String(v).slice(0, 10) : null;
}

// 单个任务 → { 'YYYY-MM-DD': markKey } 映射（只生成落在 [monthStart, monthEnd] 内的天）
function projectTaskToDays(t, monthStart, monthEnd, today) {
  const marks = {};
  const started = dayOf(t.real_started);
  const planned = dayOf(t.est_started);
  const start = started || planned;
  if (!start) return marks;
  const finished = dayOf(t.finished_date);
  const isFinished = t.status === 'done' || t.status === 'closed' || t.status === 'cancel';
  // 用本地时间拼日期串：toISOString 是 UTC，在东八区会把本地日期偏移一天
  const localDay = (dt) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
  const deadlineDay = dayOf(t.deadline);
  // 未开始（wait）且只有计划时间：计划开始日打「计划开始」标记；
  // 已过截止仍未开始的，截止日之后每天补「延期」直到今天（与日看板延期列口径一致）
  if (!started && !isFinished && t.status === 'wait') {
    if (start >= monthStart && start <= monthEnd) marks[start] = 'planned';
    if (deadlineDay && today > deadlineDay) {
      const cur = new Date(deadlineDay + 'T00:00:00');
      cur.setDate(cur.getDate() + 1);
      const stop = new Date(today + 'T00:00:00');
      while (cur <= stop) {
        const day = localDay(cur);
        if (day >= monthStart && day <= monthEnd && !marks[day]) marks[day] = 'delayed';
        cur.setDate(cur.getDate() + 1);
      }
    }
    return marks;
  }
  // 结束边界：已完成用完成日；进行中/暂停延伸到今天
  const end = isFinished ? (finished || start) : (today >= start ? today : start);
  if (start === end) {
    if (start >= monthStart && start <= monthEnd) marks[start] = isFinished ? 'same_day' : 'start';
    return marks;
  }
  const cur = new Date(start + 'T00:00:00');
  const stop = new Date(end + 'T00:00:00');
  // 过截止未完成的日子标「延期」（含逾期完成任务在完成日前的日子），与日看板延期列口径一致
  const delayedOn = (day) => !!deadlineDay && day > deadlineDay;
  while (cur <= stop) {
    const day = localDay(cur);
    if (day >= monthStart && day <= monthEnd) {
      if (day === start) marks[day] = 'start';
      else if (day === end) marks[day] = isFinished ? 'end' : (delayedOn(day) ? 'delayed' : 'ongoing');
      else marks[day] = delayedOn(day) ? 'delayed' : 'ongoing';
    }
    cur.setDate(cur.getDate() + 1);
  }
  return marks;
}

function monthTaskEntry(t, markKey) {
  const meta = MONTH_MARK_META[markKey];
  const hours = markKey === 'same_day' && t.consumed != null ? ` ${t.consumed}h` : '';
  const person = t.assigned_to_realname || t.assigned_to || '';
  const st = ZT_STATUS_META[t.status] || {};
  // 悬浮信息卡（components/hover-card.js）
  const card = {
    k: '禅道任务', id: `#${t.task_id}`, t: t.name || '', s: st.label || t.status || '',
    tone: { wait: 'gray', doing: 'blue', changed: 'blue', done: 'green', pause: 'amber', cancel: 'gray', closed: 'gray' }[t.status] || 'gray',
    rows: [['指派', person || '未指派'], ['当天', `${meta.label}${hours}`], ['计划', [dayOf(t.est_started), dayOf(t.deadline)].filter(Boolean).join(' → ')]],
  };
  return `<div class="tbd-mentry tbd-mentry--${markKey}" data-hc="${escapeHtml(JSON.stringify(card))}"><b>${meta.label}${hours}</b><span>${escapeHtml(t.name || '')}</span></div>`;
}

// 月视图沿用看板顶部筛选（成员/只看我的/状态），但不按看板日期过滤
function zentaoForMonth() {
  const assignee = document.getElementById('taskBoardAssigneeFilter')?.value;
  const statusFilter = document.getElementById('taskBoardStatusFilter')?.value;
  const mineOnly = document.getElementById('taskBoardMineOnly')?.checked;
  return state.zentao.filter((t) => {
    if (statusFilter === 'done_unclosed' && t.status !== 'done') return false;
    if (statusFilter && statusFilter !== 'done_unclosed' && ZT_BOARD_MAP[t.status] !== statusFilter) return false;
    if (assignee && Number(assignee) !== Number(t.assignee_user_id || 0)) return false;
    if (mineOnly && !ztIsMine(t)) return false;
    return true;
  });
}

function renderMonthView() {
  const root = document.getElementById('taskBoardMonthView');
  if (!root || state.viewMode !== 'month') return;
  const base = new Date((state.date || todayISO()) + 'T00:00:00');
  const year = base.getFullYear();
  const month = base.getMonth();
  const first = new Date(year, month, 1);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const monthStart = `${year}-${String(month + 1).padStart(2, '0')}-01`;
  const monthEnd = `${year}-${String(month + 1).padStart(2, '0')}-${String(daysInMonth).padStart(2, '0')}`;
  const today = todayISO();

  // 汇总：每天 → 该天的任务条目（按标记排序：开始->结束、开始、结束、进行中、计划）
  const byDay = {};
  const markOrder = { same_day: 0, start: 1, end: 2, delayed: 3, ongoing: 4, planned: 5 };
  zentaoForMonth().forEach((t) => {
    const marks = projectTaskToDays(t, monthStart, monthEnd, today);
    Object.entries(marks).forEach(([day, mark]) => {
      (byDay[day] = byDay[day] || []).push({ t, mark });
    });
  });
  Object.values(byDay).forEach((list) => list.sort((a, b) => (markOrder[a.mark] ?? 9) - (markOrder[b.mark] ?? 9) || a.t.task_id - b.t.task_id));

  // 网格：周一开头；月首前置空白补齐
  const lead = (first.getDay() + 6) % 7;
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push('<div class="tbd-mday is-blank" aria-hidden="true"></div>');
  for (let d = 1; d <= daysInMonth; d++) {
    const day = `${year}-${pad2(month + 1)}-${pad2(d)}`;
    const entries = byDay[day] || [];
    const weekday = (lead + d - 1) % 7;
    const shown = entries.slice(0, 5);
    const more = entries.length - shown.length;
    const cls = ['tbd-mday', day === today ? 'is-today' : '', day < today ? 'is-past' : '', weekday >= 5 ? 'is-weekend' : '', day === state.date ? 'is-picked' : ''].filter(Boolean).join(' ');
    cells.push(`
      <div class="${cls}">
        <button type="button" class="tbd-mday-head" data-tb-day="${day}" title="查看 ${month + 1} 月 ${d} 日的看板"><b>${d}</b>${day === today ? '<span class="tbd-mday-today">今天</span>' : ''}${entries.length ? `<small>${entries.length} 项</small>` : ''}</button>
        ${shown.map(({ t, mark }) => monthTaskEntry(t, mark)).join('')}
        ${more > 0 ? `<button type="button" class="tbd-mday-more" data-tb-day="${day}">还有 ${more} 项</button>` : ''}
      </div>`);
  }

  const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
  const legend = Object.entries(MONTH_MARK_META)
    .map(([k, m]) => `<span class="tbd-mlegend tbd-mentry--${k}"><i></i>${m.label}</span>`)
    .join('');
  root.innerHTML = `
    <div class="tbd-month-head">
      <div><b>${year} 年 ${month + 1} 月</b><small>禅道任务月历 · 按实际 / 计划起止时间铺排，点日期查看当天看板</small></div>
      <div class="tbd-month-legend">${legend}</div>
    </div>
    <div class="tbd-month-grid tbd-month-week">${weekdays.map((w, i) => `<div class="${i >= 5 ? 'is-weekend' : ''}">周${w}</div>`).join('')}</div>
    <div class="tbd-month-grid">${cells.join('')}</div>
  `;
}

// 禅道任务操作按钮（看板卡片与底部面板共用）：像任务工作台那样开始/暂停/完成/关闭/重新激活。
// 开始/暂停/完成/重新激活仅指派人本人；关闭已完成任务额外放开给管理员
// （与后端 /workbench/tasks/{id}/operate 权限一致）。
function ztActionsHtml(t) {
  if (t.task_story_change_pending) return renderTaskStoryChangeBadge(t);
  const mine = ztIsMine(t);
  const isAdmin = !!(window.currentUser && window.currentUser.role === 'admin');
  const id = t.task_id;
  const op = (label, action, tone, icon) =>
    `<button type="button" class="tbd-op tbd-op--${tone}" data-op-key="task:${id}" onclick="event.stopPropagation(); window.OmniQATaskBoardTab.ztOperate(${id}, '${action}')">${ic(icon)}${label}</button>`;
  const parts = [];
  if (mine) {
    if (t.status === 'wait') parts.push(op('开始', 'start', 'green', 'play'));
    if (t.status === 'doing' || t.status === 'changed') {
      parts.push(op('完成', 'finish', 'teal', 'check'));
      parts.push(op('暂停', 'pause', 'amber', 'pause'));
    }
    if (t.status === 'pause') {
      parts.push(op('继续', 'start', 'green', 'play'));
      parts.push(op('完成', 'finish', 'teal', 'check'));
    }
    if (t.status === 'done' || t.status === 'closed' || t.status === 'cancel') parts.push(op('重新激活', 'reactivate', 'sky', 'rotate'));
  }
  if ((mine || isAdmin) && t.status === 'done') parts.push(op('关闭', 'close', 'red', 'ban'));
  // 工时记录（查看对所有人开放；本人的记录可在弹窗里修改或删除）与指派（复用任务工作台的指派弹窗）
  const ghost = `<span class="tbd-op-ghosts">
    <button type="button" class="tbd-op tbd-op--ghost" onclick="event.stopPropagation(); window.openTaskEffortModal(${id})" title="查看、修改或删除该任务已提交的禅道工时记录">${ic('timer')}工时</button>
    <button type="button" class="tbd-op tbd-op--ghost" data-op-key="task:${id}" onclick="event.stopPropagation(); window.OmniQATaskWorkbenchTab && window.OmniQATaskWorkbenchTab.openTaskAssign(${id}, 'board')" title="改派给其他人">${ic('user')}指派</button>
  </span>`;
  return `<div class="tbd-acts">${parts.join('')}${ghost}</div>`;
}

// 指派完成后的轻量回刷：只读镜像缓存重渲染，不触发禅道全量同步
export async function reloadZentaoFromMirror() {
  try { await fetchZentaoTasks(); } catch { return; }
  if (state.data) render(state.data);
  renderZentaoPanel();
}

export async function ztOperate(taskId, action) {
  let consumed;
  if (['finish', 'close', 'reactivate', 'cancel'].includes(action)) {
    const task = state.zentao.find((item) => Number(item.task_id) === Number(taskId));
    // 确认与「补填实际工时」合并在同一个对话框里（完成且无计时记录时才要求填工时）
    const res = await confirmTaskAction(action, task, { taskId });
    if (!res) return;
    consumed = res.consumed;
  }
  await operateTask(taskId, { action, ...(consumed !== undefined ? { consumed } : {}) }, {
    onSuccess: async () => {
      await fetchZentaoTasks();
      if (state.data) render(state.data);
      renderZentaoPanel();
    },
  });
}

// 看板列中的禅道任务卡
function renderZentaoBoardCard(t) {
  const meta = ZT_STATUS_META[t.status] || { label: t.status || '' };
  const personName = t.assigned_to_realname || t.assigned_to || '未指派';
  const preview = window.OmniQAUtils && window.OmniQAUtils.renderPreviewBtn
    ? window.OmniQAUtils.renderPreviewBtn('task', t.task_id)
    : '';
  // 延期列标注截至看板日期已延期的天数；逾期完成的任务在已完成列同样带「延期完成」标签
  let delayChip = '';
  const finishedDayChip = dayOf(t.finished_date);
  if (t._boardCol === 'deferred' && t.deadline) {
    const boardDate = state.date || todayISO();
    const days = Math.max(1, Math.round((new Date(boardDate) - new Date(dayOf(t.deadline))) / 86400000));
    delayChip = `<span class="tbd-chip tbd-chip--amber">${ic('clock')}已延期 ${days} 天</span>`;
  } else if ((t._boardCol === 'done' || t._boardCol === 'done_unclosed') && t.deadline && finishedDayChip && finishedDayChip > dayOf(t.deadline)) {
    const days = Math.max(1, Math.round((new Date(finishedDayChip) - new Date(dayOf(t.deadline))) / 86400000));
    delayChip = `<span class="tbd-chip tbd-chip--amber" data-hc="${escapeHtml(JSON.stringify({ k: '延期完成', t: `截止 ${dayOf(t.deadline)}，实际 ${finishedDayChip} 完成`, tone: 'amber' }))}">${ic('clock')}延期 ${days} 天完成</span>`;
  }
  // 列名已经表达了状态；只有和列含义不一致的状态（暂停 / 延期列里的真实状态）才单独标出
  const showStatus = t.status === 'pause' || t._boardCol === 'deferred';
  const est = Number(t.estimate || 0);
  const used = Number(t.consumed || 0);
  const pct = est > 0 ? Math.min(100, Math.round((used * 100) / est)) : 0;
  return `
    <article data-task-card="zt-${t.task_id}" class="tbd-card tbd-card--zt tbd-zst--${t.status}">
      <div class="tbd-card-top">
        <span class="tbd-zt-id">禅道 #${t.task_id}</span>
        ${showStatus ? `<span class="tbd-zst tbd-zst--${t.status}">${meta.label}</span>` : ''}
        ${delayChip}
      </div>
      <div class="tbd-card-title">${escapeHtml(t.name || '')}${preview ? ` ${preview}` : ''}</div>
      <div class="tbd-card-meta">
        <span class="tbd-who"><span class="tbd-avatar tbd-avatar--sm">${initialOf(personName)}</span>${escapeHtml(personName)}</span>
        ${(t.est_started || t.deadline) ? `<span class="tbd-span">${ic('clock')}${shortDay(t.est_started)} → ${shortDay(t.deadline)}</span>` : ''}
      </div>
      ${est > 0 ? `<div class="tbd-hours${used > est ? ' is-over' : ''}"><span class="tbd-hours-bar"><i style="width:${pct}%"></i></span><small>${used || 0} / ${est} h</small></div>` : ''}
      ${ztActionsHtml(t)}
    </article>
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
    root.innerHTML = `<div class="tbd-empty">${ic('layers')}<b>没有符合条件的禅道任务</b><span>换个筛选条件，或点「同步禅道」刷新缓存</span></div>`;
    return;
  }
  // 按指派人分组
  const groups = {};
  tasks.forEach((t) => {
    const key = t.assigned_to_realname || t.assigned_to || '未指派';
    (groups[key] = groups[key] || []).push(t);
  });
  root.innerHTML = Object.entries(groups).map(([person, items]) => `
    <div class="tbd-zgroup">
      <div class="tbd-zgroup-head"><span class="tbd-avatar">${initialOf(person)}</span><b>${escapeHtml(person)}</b><small>${items.length} 个任务</small></div>
      <div class="tbd-zrows">${items.map(renderZentaoTaskRow).join('')}</div>
    </div>
  `).join('');
}

function renderZentaoTaskRow(t) {
  const meta = ZT_STATUS_META[t.status] || { label: t.status || '' };
  const spanIsMultiDay = t.est_started && t.deadline && t.est_started !== t.deadline;
  const hours = [];
  if (t.estimate != null) hours.push(`预计 ${t.estimate}h`);
  if (t.consumed != null && t.consumed > 0) hours.push(`已耗 ${t.consumed}h`);
  if (t.left != null && t.left > 0) hours.push(`剩 ${t.left}h`);
  const preview = window.OmniQAUtils && window.OmniQAUtils.renderPreviewBtn
    ? window.OmniQAUtils.renderPreviewBtn('task', t.task_id)
    : '';
  return `
    <div class="tbd-zrow">
      <span class="tbd-zst tbd-zst--${t.status}">${meta.label}</span>
      <div class="tbd-zrow-main">
        <div class="tbd-zrow-title"><span class="tbd-zt-id">#${t.task_id}</span><b>${escapeHtml(t.name)}</b>${preview}</div>
        <div class="tbd-zrow-meta">
          ${t.execution_name ? `<span>${escapeHtml(t.execution_name)}</span>` : ''}
          ${(t.est_started || t.deadline) ? `<span class="${spanIsMultiDay ? 'is-multi' : ''}">${ic('clock')}${escapeHtml(`${t.est_started || '—'} → ${t.deadline || '—'}`)}${spanIsMultiDay ? '（多日）' : ''}</span>` : ''}
          ${hours.length ? `<span>${ic('timer')}${hours.join(' · ')}</span>` : ''}
        </div>
      </div>
      ${ztActionsHtml(t)}
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
  toggleViewMode,
  loadZentao,
  refreshZentao,
  reloadZentaoFromMirror,
  renderZentaoPanel,
  ztOperate,
  toggleCreateMode,
  loadZtFormOptions,
};

bindSSE();
