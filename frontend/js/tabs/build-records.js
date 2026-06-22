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
  SUCCESS: { text: '成功', bg: '#dcfce7', color: '#166534' },
  FAILURE: { text: '失败', bg: '#fee2e2', color: '#b91c1c' },
  ABORTED: { text: '已中断', bg: '#e2e8f0', color: '#334155' },
  UNSTABLE: { text: '不稳定', bg: '#fef3c7', color: '#92400e' },
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

const ARCHIVE_STATUS_META = {
  ok:                 { icon: '✅', color: '#16a34a', label: '已归档' },
  skipped:            { icon: '⏭', color: '#64748b', label: '已跳过（重复）' },
  not_success:        { icon: '⚫', color: '#94a3b8', label: '非 SUCCESS，跳过' },
  empty_version_name: { icon: '⚠️', color: '#d97706', label: '版本名为空，跳过' },
  no_parent:          { icon: '🔍', color: '#d97706', label: '未找到父大版本' },
  error:              { icon: '❌', color: '#dc2626', label: '归档异常' },
};

const ZENTAO_PUSH_META = {
  ok:                 { icon: '☁️', color: '#16a34a', label: '已写入禅道' },
  no_binding:         { icon: '🔒', color: '#d97706', label: '禅道无可用绑定' },
  no_execution:       { icon: '🔍', color: '#d97706', label: '本地大版本未绑定执行' },
  not_success:        { icon: '⚫', color: '#94a3b8', label: '非 SUCCESS，未推送' },
  empty_version_name: { icon: '⚠️', color: '#d97706', label: '版本名为空，未推送' },
  error:              { icon: '❌', color: '#dc2626', label: '推送禅道异常' },
};

// 哪些状态允许"重试写入禅道"。ok 不需要，not_success/empty_version_name 是结构性原因，重试也没意义。
const ZENTAO_PUSH_RETRYABLE = new Set(['error', 'no_binding', 'no_execution']);

function buildArchiveStatusHtml(row) {
  if (!row.auto_archive_status) return '';
  const meta = ARCHIVE_STATUS_META[row.auto_archive_status] || { icon: '❓', color: '#64748b', label: row.auto_archive_status };
  const msg = row.auto_archive_message || '';
  return `
    <div style="margin-top:10px; display:flex; align-items:flex-start; gap:8px; padding:8px 12px; background:#f8fafc; border-left:3px solid ${meta.color}; border-radius:0 6px 6px 0;">
      <span style="font-size:14px; line-height:1.5;">${meta.icon}</span>
      <div style="font-size:12px; color:#334155; line-height:1.5;">
        <span style="font-weight:600; color:${meta.color};">小版本归档：${escapeHtml(meta.label)}</span>
        ${msg ? `<span class="muted" style="margin-left:6px;">${escapeHtml(msg)}</span>` : ''}
      </div>
    </div>`;
}

function buildZentaoPushStatusHtml(row) {
  if (!row.zentao_push_status) return '';
  const meta = ZENTAO_PUSH_META[row.zentao_push_status] || { icon: '❓', color: '#64748b', label: row.zentao_push_status };
  const msg = row.zentao_push_message || '';
  const canRetry = ZENTAO_PUSH_RETRYABLE.has(row.zentao_push_status);
  const retryBtn = canRetry
    ? `<button class="secondary"
              data-zentao-retry-btn="${Number(row.id)}"
              onclick="retryZentaoPush(${Number(row.id)}, this)"
              style="margin-left:8px; padding:2px 10px; font-size:12px;">重试写入禅道</button>`
    : '';
  return `
    <div style="margin-top:6px; display:flex; align-items:flex-start; gap:8px; padding:8px 12px; background:#f8fafc; border-left:3px solid ${meta.color}; border-radius:0 6px 6px 0;">
      <span style="font-size:14px; line-height:1.5;">${meta.icon}</span>
      <div style="font-size:12px; color:#334155; line-height:1.5; flex:1;">
        <span style="font-weight:600; color:${meta.color};">禅道写回：${escapeHtml(meta.label)}</span>
        ${msg ? `<span class="muted" style="margin-left:6px;">${escapeHtml(msg)}</span>` : ''}
        ${retryBtn}
      </div>
    </div>`;
}

function buildCardHtml(row) {
  const summary = (row.change_log || '').trim();
  const summaryText = summary ? `${summary.slice(0, 140)}${summary.length > 140 ? '...' : ''}` : '暂无变更日志';
  const linkHtml = row.build_url
    ? `<a href="${escapeHtml(row.build_url)}" target="_blank" rel="noopener noreferrer" style="word-break:break-all;">${escapeHtml(row.build_url)}</a>`
    : '<span class="muted">暂无 Jenkins 链接</span>';

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
        <div style="min-width:160px;"><div class="muted" style="font-size:12px;">构建号</div><div style="margin-top:4px; color:#0f172a; font-weight:700;">#${escapeHtml(row.build_number)}</div></div>
        <div style="min-width:180px;"><div class="muted" style="font-size:12px;">状态</div><div style="margin-top:4px; color:#334155;">${escapeHtml(getStatusText(row.build_status))}</div></div>
        <div style="min-width:220px;"><div class="muted" style="font-size:12px;">创建时间</div><div style="margin-top:4px; color:#334155;">${escapeHtml(formatDateTime(row.created_at))}</div></div>
        <div style="min-width:220px;"><div class="muted" style="font-size:12px;">更新时间</div><div style="margin-top:4px; color:#334155;">${escapeHtml(formatDateTime(row.updated_at))}</div></div>
      </div>
      <div class="row" style="margin-top:12px; flex-wrap:wrap; gap:16px;">
        <div style="min-width:180px; flex:1;"><div class="muted" style="font-size:12px;">分支</div><div style="margin-top:4px; color:#334155;">${escapeHtml(row.branch || '-')}</div></div>
        <div style="min-width:280px; flex:2;"><div class="muted" style="font-size:12px;">Jenkins 链接</div><div style="margin-top:4px;">${linkHtml}</div></div>
      </div>
      <div style="margin-top:12px;"><div class="muted" style="font-size:12px;">变更日志</div><div title="${escapeHtml(summary || '暂无变更日志')}" style="margin-top:6px; color:#334155; line-height:1.65; background:#f8fafc; border-radius:10px; padding:10px 12px;">${escapeHtml(summaryText)}</div></div>
      ${buildArchiveStatusHtml(row)}
      ${buildZentaoPushStatusHtml(row)}
      <div class="row" style="justify-content:flex-end; margin-top:12px; gap:8px;">
        ${row.auto_archive_minor_version_id ? `<button class="secondary" onclick="openBuildRecordReassignModal(${row.id})">切换归属</button>` : ''}
        <button class="secondary" onclick="openBuildRecordLogModal(${row.id})">查看日志</button>
      </div>
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
    empty.innerText = selectedJob ? '所选大版本暂无构建记录' : '暂无构建记录';
    moreWrap.classList.add('hidden');
    return;
  }

  empty.classList.add('hidden');
  wrap.innerHTML = visibleRows.map((row) => buildCardHtml(row)).join('');

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

function openLogModal(title, content) {
  const titleEl = document.getElementById('buildRecordLogTitle');
  const bodyEl = document.getElementById('buildRecordLogBody');
  if (!titleEl || !bodyEl) return;
  titleEl.innerText = title;
  bodyEl.innerText = content || '暂无变更日志';
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

export function openBuildRecordLogModal(recordId) {
  const row = state.records.find((item) => Number(item.id) === Number(recordId));
  if (!row) {
    window.showMessage && window.showMessage('记录不存在', 'error');
    return;
  }
  openLogModal(`${jobNameToMajorLabel(row.job_name)} / ${row.version_name || '版本日志'}`, row.change_log || '暂无变更日志');
}

export async function openMajorBuildLogModal() {
  const jobName = getSelectedJobName();
  if (!jobName) {
    window.showMessage && window.showMessage('请先选择一个大版本', 'error');
    return;
  }
  const title = `${jobNameToMajorLabel(jobName)} 大版本变更日志`;
  openLogModal(title, '加载中...');
  try {
    const params = new URLSearchParams({ job_name: jobName });
    const result = await (await api(`/api/admin/build-records/major-log?${params.toString()}`)).json();
    const content = result?.data?.major_log || '暂无大版本变更日志';
    openLogModal(title, content);
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
  const first = document.querySelector('#buildRecordsCardList .build-record-card[data-build-record-id]');
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
  modal.className = 'hidden';
  modal.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,.42); z-index:10000; display:none; align-items:center; justify-content:center;';
  modal.innerHTML = `
    <div style="width:min(560px, 94vw); max-height:90vh; overflow:auto; background:#fff; border-radius:14px; box-shadow:0 16px 40px rgba(0,0,0,.22); padding:22px;">
      <div class="row" style="justify-content:space-between; align-items:center; margin:0 0 14px;">
        <h3 style="margin:0; color:#0f172a;">切换归属</h3>
        <button class="secondary" onclick="closeBuildRecordReassignModal()">关闭</button>
      </div>
      <div class="muted" id="buildRecordReassignMeta" style="margin-bottom:12px;"></div>
      <div style="margin-bottom:12px;">
        <label style="font-size:12px; color:#64748b; display:block; margin-bottom:6px;">目标大版本（仅同一软件下）</label>
        <select id="buildRecordReassignSelect" style="width:100%; padding:8px 10px;"></select>
      </div>
      <div style="font-size:12px; color:#64748b; padding:10px 12px; border:1px solid #e2e8f0; border-radius:10px; background:#f8fafc;">
        禅道侧会通过 PUT 把当前 build 直接移到目标执行下（保留 stories/bugs/history）。
        禅道 IPD 4.3 不支持 API 删 build，所以这里不会有"留两份"的选项。
      </div>
      <div class="row" style="justify-content:flex-end; gap:8px; margin:18px 0 0;">
        <button class="secondary" onclick="closeBuildRecordReassignModal()">取消</button>
        <button onclick="submitBuildRecordReassign()">确认切换</button>
      </div>
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
    meta.innerHTML = `
      <div>构建：<b>${escapeHtml(jobNameToMajorLabel(row.job_name))}</b> / ${escapeHtml(row.version_name || '-')}</div>
      <div style="margin-top:4px; font-size:12px;">原大版本 = 当前归属，禅道侧会跟着搬到目标执行下。</div>
    `;
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
      pulseBuildCard(item.id, data.success ? 'green' : 'purple');
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
