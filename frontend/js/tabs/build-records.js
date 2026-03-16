import { api } from '../api.js';
import { closeModal, openModal } from '../components/modal.js';

const INITIAL_VISIBLE_COUNT = 12;
const LOAD_MORE_STEP = 12;

const state = {
  records: [],
  visibleCount: INITIAL_VISIBLE_COUNT,
};

const STATUS_META = {
  SUCCESS: { text: '成功', bg: '#dcfce7', color: '#166534' },
  FAILURE: { text: '失败', bg: '#fee2e2', color: '#b91c1c' },
  ABORTED: { text: '中止', bg: '#e2e8f0', color: '#334155' },
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
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
  });
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

function getSelectedJobName() {
  return document.getElementById('buildRecordsMajorFilter')?.value || '';
}

function getSelectedStatus() {
  return document.getElementById('buildRecordsStatusFilter')?.value || '';
}

function getDateRange() {
  return {
    from: document.getElementById('buildRecordsDateFrom')?.value || '',
    to: document.getElementById('buildRecordsDateTo')?.value || '',
  };
}

function setLoading(message = '正在加载构建记录...') {
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

function getFilteredRecords() {
  const selectedJob = getSelectedJobName();
  const selectedStatus = getSelectedStatus();
  const { from, to } = getDateRange();
  return state.records
    .filter((row) => !selectedJob || row.job_name === selectedJob)
    .filter((row) => !selectedStatus || String(row.build_status || '').toUpperCase() === selectedStatus)
    .filter((row) => recordInDateRange(row, from, to))
    .sort((a, b) => {
      const ta = new Date(a.created_at || 0).getTime();
      const tb = new Date(b.created_at || 0).getTime();
      return tb - ta;
    });
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
    empty.innerText = selectedJob ? '当前大版本暂无构建记录' : '暂无构建记录';
    moreWrap.classList.add('hidden');
    return;
  }

  empty.classList.add('hidden');
  wrap.innerHTML = visibleRows.map((row) => {
    const summary = (row.change_log || '').trim();
    const summaryText = summary ? `${summary.slice(0, 140)}${summary.length > 140 ? '...' : ''}` : '暂无日志内容';
    const linkHtml = row.build_url
      ? `<a href="${escapeHtml(row.build_url)}" target="_blank" rel="noopener noreferrer" style="word-break:break-all;">${escapeHtml(row.build_url)}</a>`
      : '<span class="muted">暂无 Jenkins 链接</span>';

    return `
      <div class="card" style="margin-bottom:0; border:1px solid #e2e8f0; box-shadow:none;">
        <div class="row" style="justify-content:space-between; align-items:flex-start; gap:14px; margin:0;">
          <div style="min-width:0; flex:1;">
            <div style="font-size:18px; font-weight:800; color:#0f172a;">${escapeHtml(jobNameToMajorLabel(row.job_name))}</div>
            <div class="muted" style="margin-top:4px; font-size:14px;">${escapeHtml(row.version_name || '暂无小版本信息')}</div>
          </div>
          <div>${buildStatusBadge(row.build_status)}</div>
        </div>

        <div class="row" style="margin-top:14px; flex-wrap:wrap; gap:16px;">
          <div style="min-width:160px;">
            <div class="muted" style="font-size:12px;">Jenkins 构建号</div>
            <div style="margin-top:4px; color:#0f172a; font-weight:700;">#${escapeHtml(row.build_number)}</div>
          </div>
          <div style="min-width:180px;">
            <div class="muted" style="font-size:12px;">构建状态</div>
            <div style="margin-top:4px; color:#334155;">${escapeHtml(getStatusText(row.build_status))}</div>
          </div>
          <div style="min-width:220px;">
            <div class="muted" style="font-size:12px;">构建时间</div>
            <div style="margin-top:4px; color:#334155;">${escapeHtml(formatDateTime(row.created_at))}</div>
          </div>
          <div style="min-width:220px;">
            <div class="muted" style="font-size:12px;">更新时间</div>
            <div style="margin-top:4px; color:#334155;">${escapeHtml(formatDateTime(row.updated_at))}</div>
          </div>
        </div>

        <div class="row" style="margin-top:12px; flex-wrap:wrap; gap:16px;">
          <div style="min-width:180px; flex:1;">
            <div class="muted" style="font-size:12px;">分支</div>
            <div style="margin-top:4px; color:#334155;">${escapeHtml(row.branch || '-')}</div>
          </div>
          <div style="min-width:280px; flex:2;">
            <div class="muted" style="font-size:12px;">Jenkins 链接</div>
            <div style="margin-top:4px;">${linkHtml}</div>
          </div>
        </div>

        <div style="margin-top:12px;">
          <div class="muted" style="font-size:12px;">小版本日志摘要</div>
          <div title="${escapeHtml(summary || '暂无日志内容')}" style="margin-top:6px; color:#334155; line-height:1.65; background:#f8fafc; border-radius:10px; padding:10px 12px;">${escapeHtml(summaryText)}</div>
        </div>

        <div class="row" style="justify-content:flex-end; margin-top:12px;">
          <button class="secondary" onclick="openBuildRecordLogModal(${row.id})">查看小版本日志</button>
        </div>
      </div>
    `;
  }).join('');

  if (rows.length > visibleRows.length) {
    moreWrap.classList.remove('hidden');
    moreMeta.innerText = `已显示 ${visibleRows.length} / ${rows.length} 条`;
    moreBtn.disabled = false;
  } else {
    moreWrap.classList.add('hidden');
    moreMeta.innerText = `已显示 ${visibleRows.length} / ${rows.length} 条`;
    moreBtn.disabled = true;
  }
}

function openLogModal(title, content) {
  const titleEl = document.getElementById('buildRecordLogTitle');
  const bodyEl = document.getElementById('buildRecordLogBody');
  if (!titleEl || !bodyEl) return;
  titleEl.innerText = title;
  bodyEl.innerText = content || '暂无日志内容';
  openModal('buildRecordLogModal');
}

export async function loadBuildRecordsBoard() {
  state.visibleCount = INITIAL_VISIBLE_COUNT;
  setLoading();
  try {
    const data = await (await api('/api/admin/build-records?limit=100&offset=0')).json();
    state.records = data.items || [];
    fillMajorFilterOptions();
    renderCards();
  } catch (err) {
    setLoading(err.message || '加载构建记录失败');
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
    window.showMessage && window.showMessage('未找到对应构建记录', 'error');
    return;
  }
  openLogModal(`${jobNameToMajorLabel(row.job_name)} / ${row.version_name || '小版本日志'}`, row.change_log || '暂无日志内容');
}

export async function openMajorBuildLogModal() {
  const jobName = getSelectedJobName();
  if (!jobName) {
    window.showMessage && window.showMessage('请先选择一个大版本', 'error');
    return;
  }
  const title = `${jobNameToMajorLabel(jobName)} 修改日志`;
  openLogModal(title, '正在加载大版本修改日志...');
  try {
    const params = new URLSearchParams({ job_name: jobName });
    const result = await (await api(`/api/admin/build-records/major-log?${params.toString()}`)).json();
    const content = result?.data?.major_log || '暂无大版本日志内容';
    openLogModal(title, content);
  } catch (err) {
    closeModal('buildRecordLogModal');
    window.showMessage && window.showMessage(err.message || '获取大版本日志失败', 'error');
  }
}

export function closeBuildRecordLogModal() {
  closeModal('buildRecordLogModal');
}

window.OmniQABuildRecordsTab = {
  loadBuildRecordsBoard,
  onBuildRecordsMajorFilterChange,
  onBuildRecordsFilterChange,
  loadMoreBuildRecords,
  openBuildRecordLogModal,
  openMajorBuildLogModal,
  closeBuildRecordLogModal,
  jobNameToMajorLabel,
};
