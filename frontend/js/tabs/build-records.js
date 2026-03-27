import { api } from '../api.js';
import { closeModal, openModal } from '../components/modal.js';

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

const STATUS_META = {
  SUCCESS: { text: 'Success', bg: '#dcfce7', color: '#166534' },
  FAILURE: { text: 'Failure', bg: '#fee2e2', color: '#b91c1c' },
  ABORTED: { text: 'Aborted', bg: '#e2e8f0', color: '#334155' },
  UNSTABLE: { text: 'Unstable', bg: '#fef3c7', color: '#92400e' },
};

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

function getStatusText(status) {
  const value = String(status || '').toUpperCase();
  return STATUS_META[value]?.text || value || '-';
}

function buildStatusBadge(status) {
  const value = String(status || '').toUpperCase();
  const meta = STATUS_META[value] || { text: value || '-', bg: '#e2e8f0', color: '#334155' };
  return `<span class="badge" style="border-radius:999px; background:${meta.bg}; color:${meta.color}; border:none; padding:4px 10px;">${meta.text}</span>`;
}

export function jobNameToMajorLabel(jobName) {
  const text = String(jobName || '').trim();
  const match = text.match(/^s(\d{3,})$/i);
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

function setLoading(message = 'Loading build records...') {
  const wrap = document.getElementById('buildRecordsCardList');
  const empty = document.getElementById('buildRecordsEmpty');
  const moreWrap = document.getElementById('buildRecordsMoreWrap');
  if (wrap) wrap.innerHTML = `<div style="text-align:center; padding:24px; color:#64748b;">${escapeHtml(message)}</div>`;
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
  select.innerHTML = ['<option value="">All Majors</option>']
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

function getFilteredRecords() {
  const selectedJob = getSelectedJobName();
  const selectedStatus = getSelectedStatus();
  const { from, to } = getDateRange();
  return state.records
    .filter((row) => !selectedJob || row.job_name === selectedJob)
    .filter((row) => !selectedStatus || String(row.build_status || '').toUpperCase() === selectedStatus)
    .filter((row) => recordInDateRange(row, from, to))
    .sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime());
}

function buildCardHtml(row) {
  const summary = (row.change_log || '').trim();
  const summaryText = summary ? `${summary.slice(0, 140)}${summary.length > 140 ? '...' : ''}` : 'No changelog';
  const linkHtml = row.build_url
    ? `<a href="${escapeHtml(row.build_url)}" target="_blank" rel="noopener noreferrer" style="word-break:break-all;">${escapeHtml(row.build_url)}</a>`
    : '<span class="muted">No Jenkins URL</span>';

  return `
    <div class="card build-record-card" data-build-record-id="${Number(row.id)}" style="margin-bottom:0; border:1px solid #e2e8f0; box-shadow:none;">
      <div class="row" style="justify-content:space-between; align-items:flex-start; gap:14px; margin:0;">
        <div style="min-width:0; flex:1;">
          <div style="font-size:18px; font-weight:800; color:#0f172a;">${escapeHtml(jobNameToMajorLabel(row.job_name))}</div>
          <div class="muted" style="margin-top:4px; font-size:14px;">${escapeHtml(row.version_name || '-')}</div>
        </div>
        <div>${buildStatusBadge(row.build_status)}</div>
      </div>
      <div class="row" style="margin-top:14px; flex-wrap:wrap; gap:16px;">
        <div style="min-width:160px;"><div class="muted" style="font-size:12px;">Build #</div><div style="margin-top:4px; color:#0f172a; font-weight:700;">#${escapeHtml(row.build_number)}</div></div>
        <div style="min-width:180px;"><div class="muted" style="font-size:12px;">Status</div><div style="margin-top:4px; color:#334155;">${escapeHtml(getStatusText(row.build_status))}</div></div>
        <div style="min-width:220px;"><div class="muted" style="font-size:12px;">Created</div><div style="margin-top:4px; color:#334155;">${escapeHtml(formatDateTime(row.created_at))}</div></div>
        <div style="min-width:220px;"><div class="muted" style="font-size:12px;">Updated</div><div style="margin-top:4px; color:#334155;">${escapeHtml(formatDateTime(row.updated_at))}</div></div>
      </div>
      <div class="row" style="margin-top:12px; flex-wrap:wrap; gap:16px;">
        <div style="min-width:180px; flex:1;"><div class="muted" style="font-size:12px;">Branch</div><div style="margin-top:4px; color:#334155;">${escapeHtml(row.branch || '-')}</div></div>
        <div style="min-width:280px; flex:2;"><div class="muted" style="font-size:12px;">Jenkins URL</div><div style="margin-top:4px;">${linkHtml}</div></div>
      </div>
      <div style="margin-top:12px;"><div class="muted" style="font-size:12px;">Changelog</div><div title="${escapeHtml(summary || 'No changelog')}" style="margin-top:6px; color:#334155; line-height:1.65; background:#f8fafc; border-radius:10px; padding:10px 12px;">${escapeHtml(summaryText)}</div></div>
      <div class="row" style="justify-content:flex-end; margin-top:12px;"><button class="secondary" onclick="openBuildRecordLogModal(${row.id})">View log</button></div>
    </div>
  `;
}

function renderCards() {
  const wrap = document.getElementById('buildRecordsCardList');
  const empty = document.getElementById('buildRecordsEmpty');
  const majorBtn = document.getElementById('buildRecordsMajorLogBtn');
  const moreWrap = document.getElementById('buildRecordsMoreWrap');
  const moreBtn = document.getElementById('buildRecordsLoadMoreBtn');
  const moreMeta = document.getElementById('buildRecordsLoadMoreMeta');
  if (!wrap || !empty || !majorBtn || !moreWrap || !moreBtn || !moreMeta) return;

  const selectedJob = getSelectedJobName();
  const rows = getFilteredRecords();
  const visibleRows = rows.slice(0, state.visibleCount);
  majorBtn.disabled = !selectedJob;

  if (!rows.length) {
    wrap.innerHTML = '';
    empty.classList.remove('hidden');
    empty.innerText = selectedJob ? 'No records for selected major' : 'No records';
    moreWrap.classList.add('hidden');
    return;
  }

  empty.classList.add('hidden');
  wrap.innerHTML = visibleRows.map((row) => buildCardHtml(row)).join('');

  if (rows.length > visibleRows.length) {
    moreWrap.classList.remove('hidden');
    moreMeta.innerText = `Showing ${visibleRows.length} / ${rows.length}`;
    moreBtn.disabled = false;
  } else {
    moreWrap.classList.add('hidden');
    moreMeta.innerText = `Showing ${visibleRows.length} / ${rows.length}`;
    moreBtn.disabled = true;
  }
}

function openLogModal(title, content) {
  const titleEl = document.getElementById('buildRecordLogTitle');
  const bodyEl = document.getElementById('buildRecordLogBody');
  if (!titleEl || !bodyEl) return;
  titleEl.innerText = title;
  bodyEl.innerText = content || 'No changelog';
  openModal('buildRecordLogModal');
}

function upsertRecordInState(item) {
  const row = { ...item };
  const idx = state.records.findIndex((r) => Number(r.id) === Number(row.id));
  if (idx >= 0) state.records[idx] = { ...state.records[idx], ...row };
  else state.records.unshift(row);
}

function isBuildListNearTop() {
  const first = document.querySelector('#buildRecordsCardList .build-record-card[data-build-record-id]');
  if (!first) return true;
  const rect = first.getBoundingClientRect();
  return rect.top >= 0 && rect.top <= Math.max(260, Math.round(window.innerHeight * 0.45));
}

function updateBuildTopNotice() {
  const el = document.getElementById('buildRecordsTopNotice');
  if (!el) return;
  if (state.unseenTopNewCount > 0) {
    el.innerText = `There are ${state.unseenTopNewCount} new build records`;
    el.classList.remove('hidden');
  } else {
    el.innerText = 'There are 0 new build records';
    el.classList.add('hidden');
  }
}

function clearBuildTopNotice() {
  state.unseenTopNewCount = 0;
  updateBuildTopNotice();
}

function pulseBuildCard(recordId, tone = 'blue') {
  if (!window.OmniQASSE || typeof window.OmniQASSE.pulseBoundaryGlow !== 'function') return;
  const el = document.querySelector(`.build-record-card[data-build-record-id='${Number(recordId)}']`);
  if (!el) return;
  window.OmniQASSE.pulseBoundaryGlow(el, tone);
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
  createdItems.forEach((item) => pulseBuildCard(item.id, 'purple'));
  updatedItems.forEach((item) => pulseBuildCard(item.id, 'green'));
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
    renderCards();
    clearBuildTopNotice();
  } catch (err) {
    setLoading(err.message || 'Load failed');
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

export function openBuildRecordLogModal(recordId) {
  const row = state.records.find((item) => Number(item.id) === Number(recordId));
  if (!row) {
    window.showMessage && window.showMessage('Record not found', 'error');
    return;
  }
  openLogModal(`${jobNameToMajorLabel(row.job_name)} / ${row.version_name || 'Version log'}`, row.change_log || 'No changelog');
}

export async function openMajorBuildLogModal() {
  const jobName = getSelectedJobName();
  if (!jobName) {
    window.showMessage && window.showMessage('Please select a major version first', 'error');
    return;
  }
  const title = `${jobNameToMajorLabel(jobName)} Major Changelog`;
  openLogModal(title, 'Loading...');
  try {
    const params = new URLSearchParams({ job_name: jobName });
    const result = await (await api(`/api/admin/build-records/major-log?${params.toString()}`)).json();
    const content = result?.data?.major_log || 'No major changelog';
    openLogModal(title, content);
  } catch (err) {
    closeModal('buildRecordLogModal');
    window.showMessage && window.showMessage(err.message || 'Load failed', 'error');
  }
}

export function closeBuildRecordLogModal() {
  closeModal('buildRecordLogModal');
}

export function revealBuildRealtimeNew() {
  clearBuildTopNotice();
  renderCards();
  const first = document.querySelector('#buildRecordsCardList .build-record-card[data-build-record-id]');
  if (first) {
    first.scrollIntoView({ behavior: 'smooth', block: 'start' });
    pulseBuildCard(first.getAttribute('data-build-record-id'), 'purple');
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
};

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
