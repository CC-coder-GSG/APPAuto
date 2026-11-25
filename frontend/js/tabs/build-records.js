import { api } from '../api.js';
import { closeModal, openModal } from '../components/modal.js';
import { openDrawer, getDrawer } from '../components/side-drawer.js';

// 2026-11-25 排版 v2：状态统计条（可点筛选）+ 按日期分组的紧凑构建行；点行在右侧抽屉看完整详情
// （变更日志 / Jenkins 链接 / 小版本归档与禅道写回状态 / 重试 / 切换归属）。
// 大版本日志与切换归属弹窗沿用操作时间线弹窗的样式（.atl-*）。
const INITIAL_VISIBLE_COUNT = 12;
const LOAD_MORE_STEP = 12;

const state = {
  records: [],
  visibleCount: INITIAL_VISIBLE_COUNT,
  unseenTopNewCount: 0,
};

let buildSseBound = false;
let buildRealtimeFlushTimer = null;
const buildPending = {
  created: [],
  updated: new Map(),
};

const ICON_PATH = {
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
  alert: '<path d="M12 8v5"/><path d="M12 16.5h.01"/><path d="m10.3 3.9-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3.1l-8-14a2 2 0 0 0-3.4 0Z"/>',
  dot: '<circle cx="12" cy="12" r="3.5"/>',
  external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10"/><path d="M18 10c0 4-5 4-11 7"/>',
};
const svg = (name, cls = 'brc-svg') => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICON_PATH[name] || ICON_PATH.dot}</svg>`;

const STATUS_META = {
  SUCCESS: { text: '成功', tone: 'green', icon: 'check' },
  FAILURE: { text: '失败', tone: 'red', icon: 'x' },
  ABORTED: { text: '已中断', tone: 'gray', icon: 'stop' },
  UNSTABLE: { text: '不稳定', tone: 'amber', icon: 'alert' },
};
const STATUS_ORDER = ['SUCCESS', 'FAILURE', 'ABORTED', 'UNSTABLE'];
const WEEK_ZH = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatDateTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}

function toDateInputValue(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const shanghai = new Date(date.toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }));
  const year = shanghai.getFullYear();
  const month = String(shanghai.getMonth() + 1).padStart(2, '0');
  const day = String(shanghai.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function timeHM(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false });
}

function relTime(value) {
  const diff = (Date.now() - new Date(value).getTime()) / 1000;
  if (Number.isNaN(diff)) return '';
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

function dayLabel(day) {
  if (!day) return '时间未知';
  const today = toDateInputValue(new Date().toISOString());
  const yesterday = toDateInputValue(new Date(Date.now() - 86400000).toISOString());
  const [y, m, d] = day.split('-').map(Number);
  const week = WEEK_ZH[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  if (day === today) return `今天 · ${m}月${d}日`;
  if (day === yesterday) return `昨天 · ${m}月${d}日`;
  return `${day} · ${week}`;
}

function statusMeta(status) {
  const value = String(status || '').toUpperCase();
  return STATUS_META[value] || { text: value || '-', tone: 'gray', icon: 'dot' };
}

function getStatusText(status) {
  return statusMeta(status).text;
}

function buildStatusBadge(status) {
  const meta = statusMeta(status);
  return `<span class="st-pill st-pill--${meta.tone}">${escapeHtml(meta.text)}</span>`;
}

export function jobNameToMajorLabel(jobName) {
  const text = String(jobName || '').trim();
  // Allow optional suffix after - or _ (e.g. s40311-1, s40311_free -> V4.0.3.11)
  const match = text.match(/^s(\d{3,})(?:[-_].*)?$/i);
  if (!match) return text || '-';
  const digits = match[1];
  const a = digits[0];
  const b = digits[1];
  const c = digits[2];
  const rest = digits.slice(3);
  return rest ? `V${a}.${b}.${c}.${rest}` : `V${a}.${b}.${c}`;
}

function getSelectedJobName() { return document.getElementById('buildRecordsMajorFilter')?.value || ''; }
function getSelectedStatus() { return document.getElementById('buildRecordsStatusFilter')?.value || ''; }

function getDateRange() {
  return {
    from: document.getElementById('buildRecordsDateFrom')?.value || '',
    to: document.getElementById('buildRecordsDateTo')?.value || '',
  };
}

function setLoading(message = '加载构建记录中...') {
  const wrap = document.getElementById('buildRecordsCardList');
  const empty = document.getElementById('buildRecordsEmpty');
  const moreWrap = document.getElementById('buildRecordsMoreWrap');
  if (wrap) wrap.innerHTML = `<div class="brc-loading">${escapeHtml(message)}</div>`;
  if (empty) empty.classList.add('hidden');
  if (moreWrap) moreWrap.classList.add('hidden');
}

function fillMajorFilterOptions() {
  const select = document.getElementById('buildRecordsMajorFilter');
  if (!select) return;
  const selected = select.value || '';
  const seen = new Set();
  const jobNames = [];
  for (const row of state.records) {
    if (!row.job_name || seen.has(row.job_name)) continue;
    seen.add(row.job_name);
    jobNames.push(row.job_name);
  }
  select.innerHTML = ['<option value="">全部大版本</option>']
    .concat(jobNames.map((jobName) => `<option value="${escapeHtml(jobName)}">${escapeHtml(jobNameToMajorLabel(jobName))}</option>`))
    .join('');
  if (selected && Array.from(select.options).some((o) => o.value === selected)) select.value = selected;
}

function recordInDateRange(row, from, to) {
  const raw = row.created_at || row.updated_at;
  if (!raw) return true;
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return true;
  const day = toDateInputValue(date.toISOString());
  if (from && day < from) return false;
  if (to && day > to) return false;
  return true;
}

function rowMatchesCurrentFilters(row) {
  const selectedJob = getSelectedJobName();
  const selectedStatus = getSelectedStatus();
  const { from, to } = getDateRange();
  if (selectedJob && row.job_name !== selectedJob) return false;
  if (selectedStatus && String(row.build_status || '').toUpperCase() !== selectedStatus) return false;
  return recordInDateRange(row, from, to);
}

// 统计条按「大版本 + 日期」过滤，不受状态筛选影响，便于看到分布并切换
function getScopeRecords() {
  const selectedJob = getSelectedJobName();
  const { from, to } = getDateRange();
  return state.records
    .filter((row) => !selectedJob || row.job_name === selectedJob)
    .filter((row) => recordInDateRange(row, from, to));
}

function getFilteredRecords() {
  const selectedStatus = getSelectedStatus();
  return getScopeRecords()
    .filter((row) => !selectedStatus || String(row.build_status || '').toUpperCase() === selectedStatus)
    .sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime());
}

const ARCHIVE_STATUS_META = {
  ok:                 { tone: 'green', short: '已归档', label: '已归档到小版本' },
  skipped:            { tone: 'gray', short: '归档跳过', label: '已跳过（重复）' },
  not_success:        { tone: 'gray', short: '未归档', label: '非 SUCCESS，跳过' },
  empty_version_name: { tone: 'amber', short: '未归档', label: '版本名为空，跳过' },
  no_parent:          { tone: 'amber', short: '归档待处理', label: '未找到父大版本' },
  error:              { tone: 'red', short: '归档异常', label: '归档异常' },
};

const ZENTAO_PUSH_META = {
  ok:                 { tone: 'green', short: '已写禅道', label: '已写入禅道' },
  no_binding:         { tone: 'amber', short: '禅道待写回', label: '禅道无可用绑定' },
  no_execution:       { tone: 'amber', short: '禅道待写回', label: '本地大版本未绑定执行' },
  not_success:        { tone: 'gray', short: '未写禅道', label: '非 SUCCESS，未推送' },
  empty_version_name: { tone: 'amber', short: '未写禅道', label: '版本名为空，未推送' },
  error:              { tone: 'red', short: '禅道写回异常', label: '推送禅道异常' },
};

// 哪些状态允许"重试写入禅道"。ok 不需要，not_success/empty_version_name 是结构性原因，重试也没意义。
const ZENTAO_PUSH_RETRYABLE = new Set(['error', 'no_binding', 'no_execution']);
const FLAG_ICON = { green: 'check', red: 'x', amber: 'alert', gray: 'dot' };

function flagChip(kind, status, message) {
  if (!status) return '';
  const map = kind === 'archive' ? ARCHIVE_STATUS_META : ZENTAO_PUSH_META;
  const meta = map[status] || { tone: 'gray', short: status, label: status };
  const prefix = kind === 'archive' ? '小版本归档' : '禅道写回';
  const tip = `${prefix}：${meta.label}${message ? `（${message}）` : ''}`;
  return `<span class="brc-flag brc-flag--${meta.tone}" title="${escapeHtml(tip)}">${svg(FLAG_ICON[meta.tone])}${escapeHtml(meta.short)}</span>`;
}

function changeLogPreview(text) {
  const lines = String(text || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return '';
  return lines.slice(0, 2).join('　');
}

function buildCardHtml(row) {
  const meta = statusMeta(row.build_status);
  const preview = changeLogPreview(row.change_log);
  const lines = String(row.change_log || '').split(/\r?\n/).filter((l) => l.trim()).length;
  const retry = ZENTAO_PUSH_RETRYABLE.has(row.zentao_push_status)
    ? `<button type="button" class="secondary brc-retry" data-zentao-retry-btn="${Number(row.id)}" onclick="retryZentaoPush(${Number(row.id)}, this)" title="${escapeHtml(row.zentao_push_message || '')}">重试写入禅道</button>`
    : '';
  const jenkins = row.build_url
    ? `<a class="brc-jenkins" href="${escapeHtml(row.build_url)}" target="_blank" rel="noopener noreferrer" title="在 Jenkins 中打开 #${escapeHtml(row.build_number)}">${svg('external')}</a>`
    : '';
  return `
    <div class="brc-row brc-row--${meta.tone}" role="listitem" data-build-record-id="${Number(row.id)}">
      <button type="button" class="fbk-row-hit" onclick="openBuildRecordLogModal(${Number(row.id)})" aria-label="查看构建 ${escapeHtml(row.version_name || row.build_number)} 详情"></button>
      <span class="brc-ic" title="构建${escapeHtml(meta.text)}">${svg(meta.icon)}</span>
      <div class="brc-main">
        <div class="brc-title" title="${escapeHtml(jobNameToMajorLabel(row.job_name))} · ${escapeHtml(row.version_name || '-')}">
          <b>${escapeHtml(jobNameToMajorLabel(row.job_name))}</b>
          <span class="brc-ver">${escapeHtml(row.version_name || '-')}</span>
        </div>
        <div class="brc-log${preview ? '' : ' is-empty'}" title="${escapeHtml(row.change_log || '')}">${preview ? escapeHtml(preview) : '暂无变更日志'}${lines > 2 ? `<span class="brc-more-lines">+${lines - 2} 条</span>` : ''}</div>
      </div>
      <div class="brc-build">
        <span class="brc-no">#${escapeHtml(row.build_number)}</span>
        ${row.branch ? `<span class="brc-branch" title="分支 ${escapeHtml(row.branch)}">${svg('branch')}<span>${escapeHtml(row.branch)}</span></span>` : ''}
      </div>
      <div class="brc-flags">${flagChip('archive', row.auto_archive_status, row.auto_archive_message)}${flagChip('zentao', row.zentao_push_status, row.zentao_push_message)}</div>
      <div class="brc-time" title="${escapeHtml(formatDateTime(row.created_at))}"><b>${escapeHtml(timeHM(row.created_at))}</b><span>${escapeHtml(relTime(row.created_at))}</span></div>
      <div class="brc-actions">${retry}${jenkins}</div>
    </div>
  `;
}

function renderStats() {
  const wrap = document.getElementById('buildRecordsStats');
  if (!wrap) return;
  const scope = getScopeRecords();
  const selected = getSelectedStatus();
  const count = (s) => scope.filter((r) => String(r.build_status || '').toUpperCase() === s).length;
  const stat = (value, label, n, tone) => `
    <button type="button" class="brc-stat brc-stat--${tone}${selected === value ? ' is-on' : ''}" aria-pressed="${selected === value}"
      onclick="window.OmniQABuildRecordsTab.setBuildStatusFilter('${value}')">
      <span class="brc-stat-label">${label}</span><b>${n}</b>
    </button>`;
  const latest = scope.slice().sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))[0];
  const success = count('SUCCESS');
  const rate = scope.length ? Math.round((success / scope.length) * 100) : 0;
  wrap.innerHTML = `
    <div class="brc-stat-group" role="group" aria-label="按构建状态筛选">
      ${stat('', '全部', scope.length, 'all')}
      ${STATUS_ORDER.map((s) => stat(s, STATUS_META[s].text, count(s), STATUS_META[s].tone)).join('')}
    </div>
    <div class="brc-stat-side">
      <span>成功率 <b>${rate}%</b></span>
      ${latest ? `<span title="${escapeHtml(formatDateTime(latest.created_at))}">最近构建 <b>${escapeHtml(relTime(latest.created_at))}</b></span>` : ''}
    </div>`;
}

export function setBuildStatusFilter(value) {
  const select = document.getElementById('buildRecordsStatusFilter');
  if (select) {
    select.value = value || '';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }
  state.visibleCount = INITIAL_VISIBLE_COUNT;
  renderCards();
}

function renderCards() {
  const wrap = document.getElementById('buildRecordsCardList');
  const empty = document.getElementById('buildRecordsEmpty');
  const majorBtn = document.getElementById('buildRecordsMajorLogBtn');
  const moreWrap = document.getElementById('buildRecordsMoreWrap');
  const moreBtn = document.getElementById('buildRecordsLoadMoreBtn');
  const moreMeta = document.getElementById('buildRecordsLoadMoreMeta');
  if (!wrap || !empty || !majorBtn || !moreWrap || !moreBtn || !moreMeta) return;

  renderStats();
  const selectedJob = getSelectedJobName();
  const rows = getFilteredRecords();
  const visibleRows = rows.slice(0, state.visibleCount);
  majorBtn.disabled = !selectedJob;
  majorBtn.title = selectedJob ? '' : '先在左侧选择一个大版本';

  if (!rows.length) {
    wrap.innerHTML = '';
    empty.classList.remove('hidden');
    empty.innerHTML = `<svg class="fbk-empty-ic" aria-hidden="true"><use href="#i-build"/></svg><strong>${selectedJob ? '所选大版本暂无构建记录' : '暂无构建记录'}</strong><span>调整上方的大版本、状态或日期范围试试。</span>`;
    moreWrap.classList.add('hidden');
    return;
  }

  empty.classList.add('hidden');
  const groups = [];
  visibleRows.forEach((row) => {
    const day = toDateInputValue(row.created_at);
    let g = groups[groups.length - 1];
    if (!g || g.day !== day) { g = { day, rows: [] }; groups.push(g); }
    g.rows.push(row);
  });
  wrap.innerHTML = groups.map((g) => `
    <div class="brc-day"><span>${escapeHtml(dayLabel(g.day))}</span><em>${g.rows.length} 次构建</em></div>
    ${g.rows.map((row) => buildCardHtml(row)).join('')}`).join('');
  markActiveBuild();

  if (rows.length > visibleRows.length) {
    moreWrap.classList.remove('hidden');
    moreMeta.innerText = `显示 ${visibleRows.length} / ${rows.length} 条`;
    moreBtn.disabled = false;
  } else {
    moreWrap.classList.add('hidden');
    moreMeta.innerText = `共 ${rows.length} 条`;
    moreBtn.disabled = true;
  }
}

function markActiveBuild() {
  const d = getDrawer('build-detail');
  const id = d ? Number(d.root.dataset.recordId) : 0;
  document.querySelectorAll('#buildRecordsCardList .brc-row').forEach((el) => {
    el.classList.toggle('is-active', Number(el.dataset.buildRecordId) === id);
  });
}

function openLogModal(title, content, sub = '') {
  const titleEl = document.getElementById('buildRecordLogTitle');
  const bodyEl = document.getElementById('buildRecordLogBody');
  const subEl = document.getElementById('buildRecordLogSub');
  if (!titleEl || !bodyEl) return;
  titleEl.innerText = title;
  bodyEl.innerText = content || '暂无变更日志';
  if (subEl) subEl.innerText = sub;
  openModal('buildRecordLogModal');
  bindDialogKeys();
}

let dialogKeysBound = false;
function bindDialogKeys() {
  if (dialogKeysBound) return;
  dialogKeysBound = true;
  window.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const open = ['buildRecordReassignModal', 'buildRecordLogModal']
      .map((id) => document.getElementById(id))
      .find((el) => el && !el.classList.contains('hidden') && el.style.display !== 'none');
    if (!open) return;
    event.preventDefault();
    event.stopPropagation();
    if (open.id === 'buildRecordReassignModal') closeBuildRecordReassignModal();
    else closeBuildRecordLogModal();
  }, true);
}

function upsertRecordInState(item) {
  const row = { ...item };
  const idx = state.records.findIndex((r) => Number(r.id) === Number(row.id));
  if (idx >= 0) state.records[idx] = { ...state.records[idx], ...row };
  else state.records.unshift(row);
}

function isBuildListNearTop() {
  const first = document.querySelector('#buildRecordsCardList .brc-row[data-build-record-id]');
  if (!first) return true;
  const rect = first.getBoundingClientRect();
  return rect.top >= 0 && rect.top <= Math.max(260, Math.round(window.innerHeight * 0.45));
}

const _buildTopBannerCount = { value: 0 };

function updateBuildTopNotice() {
  // Legacy DOM notice
  const el = document.getElementById('buildRecordsTopNotice');
  if (el) {
    if (state.unseenTopNewCount > 0) {
      el.innerText = `有 ${state.unseenTopNewCount} 条新构建记录`;
      el.classList.remove('hidden');
    } else {
      el.classList.add('hidden');
    }
  }
  // Position-aware banner
  if (!window.OmniQASSE?.showPositionBanner) return;
  _buildTopBannerCount.value = state.unseenTopNewCount;
  if (state.unseenTopNewCount <= 0) return;
  const cardList = document.getElementById('buildRecordsCardList');
  if (!cardList) return;
  window.OmniQASSE.showPositionBanner({
    scrollContainer: cardList.closest('[style*="overflow"]') || cardList.parentElement,
    anchorEl: cardList,
    position: 'top',
    countRef: _buildTopBannerCount,
    labelFn: (n) => `⬆ 有 ${n} 条新构建记录，点击查看`,
    onClickScroll: () => revealBuildRealtimeNew(),
    bannerId: 'build-new',
  });
}

function clearBuildTopNotice() {
  state.unseenTopNewCount = 0;
  _buildTopBannerCount.value = 0;
  updateBuildTopNotice();
}

function pulseBuildCard(recordId, tone = 'blue') {
  if (!window.OmniQASSE || typeof window.OmniQASSE.pulseBoundaryGlow !== 'function') return;
  const el = document.querySelector(`.brc-row[data-build-record-id='${Number(recordId)}']`);
  if (!el) return;
  window.OmniQASSE.pulseBoundaryGlow(el, tone);
}

function refreshOpenDetail(recordId) {
  const d = getDrawer('build-detail');
  if (d && Number(d.root.dataset.recordId) === Number(recordId)) openBuildRecordLogModal(recordId, { keepScroll: true });
}

function flushBuildRealtimeQueue() {
  buildRealtimeFlushTimer = null;
  const tab = document.getElementById('tab-build-records');
  if (!tab || tab.classList.contains('hidden')) return;

  const createdItems = buildPending.created.splice(0);
  const updatedItems = Array.from(buildPending.updated.values());
  buildPending.updated.clear();
  if (!createdItems.length && !updatedItems.length) return;

  createdItems.forEach((item) => upsertRecordInState(item));
  updatedItems.forEach((item) => upsertRecordInState(item));
  updatedItems.forEach((item) => refreshOpenDetail(item.id));

  if (!isBuildListNearTop()) {
    const unseenCreated = createdItems.filter((item) => rowMatchesCurrentFilters(item)).length;
    if (unseenCreated > 0) {
      state.unseenTopNewCount += unseenCreated;
      updateBuildTopNotice();
    }
    return;
  }

  renderCards();
  if (createdItems.length > 0) clearBuildTopNotice();
  createdItems.forEach((item) => pulseBuildCard(item.id, 'blue'));
  updatedItems.forEach((item) => {
    // 成功 → green, 失败 → purple, 其他 → teal
    const status = String(item.build_status || '').toUpperCase();
    const tone = status === 'SUCCESS' ? 'green' : status === 'FAILURE' ? 'purple' : 'teal';
    pulseBuildCard(item.id, tone);
  });
}

function enqueueBuildRealtime(kind, item) {
  if (!item?.id) return;
  if (kind === 'created') buildPending.created.push(item);
  else buildPending.updated.set(Number(item.id), item);
  if (buildRealtimeFlushTimer) clearTimeout(buildRealtimeFlushTimer);
  buildRealtimeFlushTimer = setTimeout(flushBuildRealtimeQueue, 280);
}

export async function loadBuildRecordsBoard() {
  state.visibleCount = INITIAL_VISIBLE_COUNT;
  setLoading();
  try {
    const data = await (await api('/api/admin/build-records?limit=100&offset=0')).json();
    state.records = data.items || [];
    fillMajorFilterOptions();
    // 回填记忆的筛选项（大版本选项依赖刚加载的数据，需在渲染前回填）
    window.SelectMemory && window.SelectMemory.applyMany(['buildRecordsMajorFilter', 'buildRecordsStatusFilter']);
    renderCards();
    clearBuildTopNotice();
  } catch (err) {
    setLoading(err.message || '加载失败');
    throw err;
  }
}

export function onBuildRecordsMajorFilterChange() {
  state.visibleCount = INITIAL_VISIBLE_COUNT;
  renderCards();
}

export function onBuildRecordsFilterChange() {
  state.visibleCount = INITIAL_VISIBLE_COUNT;
  renderCards();
}

export function loadMoreBuildRecords() {
  state.visibleCount += LOAD_MORE_STEP;
  renderCards();
}

function statusCard(kind, row) {
  const status = kind === 'archive' ? row.auto_archive_status : row.zentao_push_status;
  const message = kind === 'archive' ? row.auto_archive_message : row.zentao_push_message;
  const title = kind === 'archive' ? '小版本归档' : '禅道写回';
  if (!status) {
    return `<div class="brc-auto brc-auto--gray"><span class="brc-auto-ic">${svg('dot')}</span><div><b>${title}</b><span>未执行</span></div></div>`;
  }
  const map = kind === 'archive' ? ARCHIVE_STATUS_META : ZENTAO_PUSH_META;
  const meta = map[status] || { tone: 'gray', label: status };
  const retry = kind === 'zentao' && ZENTAO_PUSH_RETRYABLE.has(status)
    ? `<button type="button" class="secondary brc-retry" data-zentao-retry-btn="${Number(row.id)}" onclick="retryZentaoPush(${Number(row.id)}, this)">重试写入禅道</button>`
    : '';
  return `<div class="brc-auto brc-auto--${meta.tone}">
      <span class="brc-auto-ic">${svg(FLAG_ICON[meta.tone])}</span>
      <div><b>${title}：${escapeHtml(meta.label)}</b>${message ? `<span>${escapeHtml(message)}</span>` : ''}</div>
      ${retry}
    </div>`;
}

// 名称沿用旧的「查看日志」入口（全局包装函数、SSE 等都调它），现在打开的是右侧详情抽屉
export function openBuildRecordLogModal(recordId, { keepScroll = false } = {}) {
  const row = state.records.find((item) => Number(item.id) === Number(recordId));
  if (!row) {
    window.showMessage && window.showMessage('记录不存在', 'error');
    return;
  }
  const meta = statusMeta(row.build_status);
  const log = String(row.change_log || '').trim();
  const d = openDrawer({
    key: 'build-detail',
    size: 'lg',
    keepScroll,
    onClose: () => markActiveBuild(),
    head: `
      <div class="sd-eyebrow">${buildStatusBadge(row.build_status)}<span class="fbk-no">#${escapeHtml(row.build_number)}</span><span>${escapeHtml(jobNameToMajorLabel(row.job_name))}</span></div>
      <h3 class="sd-title">${escapeHtml(row.version_name || '未命名版本')}</h3>
      <div class="sd-sub">${escapeHtml(formatDateTime(row.created_at))} 上报 · ${escapeHtml(relTime(row.created_at))}</div>`,
    tools: `${row.build_url ? `<a class="brc-tool-link" href="${escapeHtml(row.build_url)}" target="_blank" rel="noopener noreferrer">${svg('external')}Jenkins</a>` : ''}
      ${row.auto_archive_minor_version_id ? `<button type="button" class="secondary sd-tool" onclick="openBuildRecordReassignModal(${Number(row.id)})">切换归属</button>` : ''}`,
    body: `
      <dl class="sd-facts">
        <div><dt>大版本</dt><dd>${escapeHtml(jobNameToMajorLabel(row.job_name))}</dd></div>
        <div><dt>构建号</dt><dd>#${escapeHtml(row.build_number)}</dd></div>
        <div><dt>构建状态</dt><dd>${escapeHtml(meta.text)}</dd></div>
        <div><dt>分支</dt><dd>${escapeHtml(row.branch || '-')}</dd></div>
        <div><dt>Jenkins Job</dt><dd>${escapeHtml(row.job_name || '-')}</dd></div>
        <div><dt>最近更新</dt><dd>${escapeHtml(formatDateTime(row.updated_at))}</dd></div>
      </dl>

      <section class="sd-sec">
        <h4 class="sd-sec-title"><svg class="ui-ic" aria-hidden="true"><use href="#i-build"/></svg>变更日志</h4>
        ${log ? `<pre class="brc-log-full">${escapeHtml(log)}</pre>` : '<span class="sd-empty">本次构建没有上报变更日志</span>'}
      </section>

      <section class="sd-sec">
        <h4 class="sd-sec-title"><svg class="ui-ic" aria-hidden="true"><use href="#i-refresh"/></svg>自动处理</h4>
        <div class="brc-autos">${statusCard('archive', row)}${statusCard('zentao', row)}</div>
      </section>

      ${row.build_url ? `<section class="sd-sec">
        <h4 class="sd-sec-title"><svg class="ui-ic" aria-hidden="true"><use href="#i-jenkins"/></svg>Jenkins 链接</h4>
        <a class="brc-url" href="${escapeHtml(row.build_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.build_url)}</a>
      </section>` : ''}`,
  });
  d.root.dataset.recordId = String(row.id);
  markActiveBuild();
}

export async function openMajorBuildLogModal() {
  const jobName = getSelectedJobName();
  if (!jobName) {
    window.showMessage && window.showMessage('请先选择一个大版本', 'error');
    return;
  }
  const title = `${jobNameToMajorLabel(jobName)} 大版本变更日志`;
  const count = state.records.filter((r) => r.job_name === jobName).length;
  const sub = `Jenkins Job：${jobName} · 汇总该大版本下 ${count} 次构建`;
  openLogModal(title, '加载中...', sub);
  try {
    const params = new URLSearchParams({ job_name: jobName });
    const result = await (await api(`/api/admin/build-records/major-log?${params.toString()}`)).json();
    const content = result?.data?.major_log || '暂无大版本变更日志';
    openLogModal(title, content, sub);
  } catch (err) {
    closeModal('buildRecordLogModal');
    window.showMessage && window.showMessage(err.message || '加载失败', 'error');
  }
}

export function closeBuildRecordLogModal() {
  closeModal('buildRecordLogModal');
}

export function revealBuildRealtimeNew() {
  clearBuildTopNotice();
  renderCards();
  const first = document.querySelector('#buildRecordsCardList .brc-row[data-build-record-id]');
  if (first) {
    first.scrollIntoView({ behavior: 'smooth', block: 'start' });
    pulseBuildCard(first.getAttribute('data-build-record-id'), 'purple');
  }
}

function ensureReassignModal() {
  let modal = document.getElementById('buildRecordReassignModal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'buildRecordReassignModal';
  modal.className = 'hidden atl-root';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'buildRecordReassignTitle');
  modal.style.cssText = 'display:none; z-index:10000;';
  modal.addEventListener('click', (e) => { if (e.target === modal) closeBuildRecordReassignModal(); });
  modal.innerHTML = `
    <div class="atl-card atl-card--sm">
      <header class="atl-head">
        <span class="atl-icon" aria-hidden="true"><svg><use href="#i-build"/></svg></span>
        <div class="atl-head-text">
          <h3 id="buildRecordReassignTitle">切换归属</h3>
          <div class="atl-sub" id="buildRecordReassignMeta"></div>
        </div>
        <button type="button" class="atl-close" onclick="closeBuildRecordReassignModal()" aria-label="关闭" title="关闭（Esc）"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
      </header>
      <div class="atl-body atl-body--form">
        <label class="brc-field"><span>目标大版本<small>仅同一软件下</small></span><select id="buildRecordReassignSelect"></select></label>
        <div class="brc-note">
          禅道侧会通过 PUT 把当前 build 直接移到目标执行下（保留 stories / bugs / history）。
          禅道 IPD 4.3 不支持 API 删除 build，所以这里不会出现「保留两份」的选项。
        </div>
      </div>
      <footer class="atl-foot">
        <button type="button" class="secondary" onclick="closeBuildRecordReassignModal()">取消</button>
        <button type="button" onclick="submitBuildRecordReassign()">确认切换</button>
      </footer>
    </div>`;
  document.body.appendChild(modal);
  return modal;
}

export async function openBuildRecordReassignModal(recordId) {
  const row = state.records.find((r) => Number(r.id) === Number(recordId));
  if (!row) {
    window.showMessage && window.showMessage('记录不存在', 'error');
    return;
  }
  if (!row.auto_archive_minor_version_id) {
    window.showMessage && window.showMessage('该记录尚未归档到本地小版本，无法切换', 'error');
    return;
  }
  const modal = ensureReassignModal();
  modal.dataset.recordId = String(recordId);
  const meta = document.getElementById('buildRecordReassignMeta');
  if (meta) {
    meta.innerHTML = `<span class="atl-target">${escapeHtml(jobNameToMajorLabel(row.job_name))} · ${escapeHtml(row.version_name || '-')}</span><span class="atl-total">#${escapeHtml(row.build_number)}</span>`;
  }
  const select = document.getElementById('buildRecordReassignSelect');
  if (select) {
    select.innerHTML = '<option value="">加载中...</option>';
    try {
      const versions = window.versions || [];
      const majors = versions.filter((v) => v.version_type === 'major');
      if (!majors.length) {
        select.innerHTML = '<option value="">暂无大版本</option>';
      } else {
        select.innerHTML = majors.map((m) => `<option value="${m.id}">${escapeHtml(m.version_no)}</option>`).join('');
      }
    } catch (err) {
      select.innerHTML = '<option value="">加载失败</option>';
    }
  }
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  bindDialogKeys();
  modal.querySelector('#buildRecordReassignSelect')?.focus({ preventScroll: true });
}

export function closeBuildRecordReassignModal() {
  const modal = document.getElementById('buildRecordReassignModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.style.display = 'none';
}

export async function retryZentaoPush(recordId, btnEl) {
  const id = Number(recordId);
  if (!id) return;
  const button = btnEl || document.querySelector(`[data-zentao-retry-btn='${id}']`);
  const originalText = button ? button.innerText : '';
  if (button) {
    button.disabled = true;
    button.innerText = '重试中...';
  }
  try {
    const res = await api(`/api/admin/build-records/${id}/retry-zentao-push`, {
      method: 'POST',
      headers: window.H,
    });
    const data = await res.json();
    const item = data?.item;
    if (item?.id) {
      upsertRecordInState(item);
      renderCards();
      refreshOpenDetail(item.id);
      pulseBuildCard(item.id, data.success ? 'green' : 'purple');
    } else if (button) {
      button.disabled = false;
      button.innerText = originalText || '重试写入禅道';
    }
    const tone = data.success ? 'success' : 'error';
    const fallbackMsg = data.success ? '写入禅道成功' : (data.message || '写入禅道仍未成功');
    window.showMessage && window.showMessage(fallbackMsg, tone);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '重试失败', 'error');
    if (button) {
      button.disabled = false;
      button.innerText = originalText || '重试写入禅道';
    }
  }
}

export async function submitBuildRecordReassign() {
  const modal = document.getElementById('buildRecordReassignModal');
  if (!modal) return;
  const recordId = Number(modal.dataset.recordId || 0);
  const targetMajorId = Number(document.getElementById('buildRecordReassignSelect')?.value || 0);
  if (!recordId || !targetMajorId) {
    window.showMessage && window.showMessage('请选择目标大版本', 'error');
    return;
  }
  try {
    const res = await api(`/api/admin/build-records/${recordId}/reassign-major`, {
      method: 'POST',
      headers: window.H,
      body: { target_major_id: targetMajorId },
    });
    const data = await res.json();
    window.showMessage && window.showMessage(data.message || '切换成功', 'success');
    closeBuildRecordReassignModal();
    await loadBuildRecordsBoard();
    refreshOpenDetail(recordId);
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '切换失败', 'error');
  }
}

window.OmniQABuildRecordsTab = {
  loadBuildRecordsBoard,
  onBuildRecordsMajorFilterChange,
  onBuildRecordsFilterChange,
  loadMoreBuildRecords,
  openBuildRecordLogModal,
  openMajorBuildLogModal,
  closeBuildRecordLogModal,
  revealBuildRealtimeNew,
  jobNameToMajorLabel,
  openBuildRecordReassignModal,
  closeBuildRecordReassignModal,
  submitBuildRecordReassign,
  retryZentaoPush,
  setBuildStatusFilter,
};

window.openBuildRecordReassignModal = openBuildRecordReassignModal;
window.closeBuildRecordReassignModal = closeBuildRecordReassignModal;
window.submitBuildRecordReassign = submitBuildRecordReassign;
window.retryZentaoPush = retryZentaoPush;

function bindBuildRecordSSE() {
  if (buildSseBound) return;
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;

  window.OmniQASSE.subscribe('build_record_created', ({ payload }) => {
    const item = payload?.item;
    if (!item?.id) return;
    enqueueBuildRealtime('created', item);
  });

  window.OmniQASSE.subscribe('build_record_updated', ({ payload }) => {
    const item = payload?.item;
    if (!item?.id) return;
    enqueueBuildRealtime('updated', item);
  });

  buildSseBound = true;
}

bindBuildRecordSSE();
