import { api } from '../api.js';
import { closeModal, openModal } from '../components/modal.js';

const state = {
  records: [],
  filteredRecords: [],
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

function buildStatusBadge(status) {
  const value = String(status || '').toUpperCase();
  const conf = {
    SUCCESS: { bg: '#dcfce7', color: '#166534', text: 'SUCCESS' },
    FAILURE: { bg: '#fee2e2', color: '#b91c1c', text: 'FAILURE' },
    UNSTABLE: { bg: '#fef3c7', color: '#92400e', text: 'UNSTABLE' },
  };
  const item = conf[value] || { bg: '#e2e8f0', color: '#334155', text: value || '-' };
  return `<span class="badge" style="border-radius:999px; background:${item.bg}; color:${item.color}; border:none; padding:4px 10px;">${item.text}</span>`;
}

function setLoading(message = '正在加载构建记录...') {
  const wrap = document.getElementById('buildRecordsTableBody');
  if (wrap) wrap.innerHTML = `<div style="text-align:center; padding:24px; color:#64748b;">${escapeHtml(message)}</div>`;
}

function fillMajorFilterOptions() {
  const select = document.getElementById('buildRecordsMajorFilter');
  if (!select) return;
  const seen = new Map();
  (state.records || []).forEach((row) => {
    if (!row.job_name || seen.has(row.job_name)) return;
    seen.set(row.job_name, jobNameToMajorLabel(row.job_name));
  });
  const options = [`<option value="">全部大版本</option>`]
    .concat(Array.from(seen.entries()).map(([jobName, label]) => `<option value="${escapeHtml(jobName)}">${escapeHtml(label)}</option>`));
  select.innerHTML = options.join('');
}

function getSelectedJobName() {
  return document.getElementById('buildRecordsMajorFilter')?.value || '';
}

function getLatestRecordForSelectedMajor() {
  const selected = getSelectedJobName();
  if (!selected) return null;
  return (state.records || []).find((row) => row.job_name === selected) || null;
}

function renderCards() {
  const wrap = document.getElementById('buildRecordsTableBody');
  const empty = document.getElementById('buildRecordsEmpty');
  const hint = document.getElementById('buildRecordsMajorLogHint');
  const majorBtn = document.getElementById('buildRecordsMajorLogBtn');
  if (!wrap || !empty || !hint || !majorBtn) return;

  const selected = getSelectedJobName();
  const rows = selected ? state.records.filter((row) => row.job_name === selected) : state.records.slice();
  state.filteredRecords = rows;

  majorBtn.disabled = !selected;
  hint.innerText = selected ? `当前筛选：${jobNameToMajorLabel(selected)}` : '当前筛选：全部大版本';

  if (!rows.length) {
    wrap.innerHTML = '';
    empty.classList.remove('hidden');
    empty.innerText = selected ? '当前大版本暂无构建记录' : '暂无构建记录';
    return;
  }

  empty.classList.add('hidden');
  wrap.innerHTML = rows.map((row) => {
    const summary = (row.change_log || '').trim();
    const summaryShort = summary ? `${summary.slice(0, 80)}${summary.length > 80 ? '...' : ''}` : '暂无日志内容';
    return `
      <div style="border:1px solid #e2e8f0; border-radius:12px; padding:16px 18px; background:#fff; box-shadow:0 4px 6px -1px rgba(0,0,0,0.04);">
        <div class="row" style="justify-content:space-between; align-items:flex-start; gap:12px; margin:0;">
          <div style="min-width:0; flex:1;">
            <div style="font-weight:800; color:#0f172a; font-size:18px;">${escapeHtml(jobNameToMajorLabel(row.job_name))}</div>
            <div class="muted" style="margin-top:4px; font-size:14px;">${escapeHtml(row.version_name || '暂无小版本信息')}</div>
          </div>
          <div>${buildStatusBadge(row.build_status)}</div>
        </div>

        <div class="row" style="margin-top:14px; align-items:stretch; flex-wrap:wrap; gap:16px;">
          <div style="flex:1; min-width:180px;">
            <div class="muted" style="font-size:12px;">Jenkins 构建号</div>
            <div style="margin-top:4px; color:#0f172a; font-weight:700;">${escapeHtml(row.build_number)}</div>
          </div>
          <div style="flex:1; min-width:220px;">
            <div class="muted" style="font-size:12px;">构建时间</div>
            <div style="margin-top:4px; color:#334155;">${escapeHtml(formatDateTime(row.created_at))}</div>
          </div>
          <div style="flex:1; min-width:220px;">
            <div class="muted" style="font-size:12px;">更新时间</div>
            <div style="margin-top:4px; color:#334155;">${escapeHtml(formatDateTime(row.updated_at))}</div>
          </div>
        </div>

        <div class="row" style="margin-top:12px; align-items:stretch; flex-wrap:wrap; gap:16px;">
          <div style="flex:1; min-width:220px;">
            <div class="muted" style="font-size:12px;">分支</div>
            <div style="margin-top:4px; color:#334155;">${escapeHtml(row.branch || '-')}</div>
          </div>
          <div style="flex:2; min-width:280px;">
            <div class="muted" style="font-size:12px;">Jenkins 链接</div>
            <div style="margin-top:4px;">${row.build_url ? `<a href="${escapeHtml(row.build_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.build_url)}</a>` : '<span class="muted">暂无 Jenkins 链接</span>'}</div>
          </div>
        </div>

        <div style="margin-top:12px;">
          <div class="muted" style="font-size:12px;">小版本日志摘要</div>
          <div title="${escapeHtml(summary || '暂无日志内容')}" style="margin-top:6px; color:#334155; line-height:1.65; background:#f8fafc; border-radius:10px; padding:10px 12px;">${escapeHtml(summaryShort)}</div>
        </div>

        <div class="row" style="justify-content:flex-end; margin-top:12px;">
          <button class="secondary" onclick="openBuildRecordLogModal(${row.id})">查看小版本日志</button>
        </div>
      </div>
    `;
  }).join('');
}

function renderLogModal(title, content) {
  const titleEl = document.getElementById('buildRecordLogTitle');
  const bodyEl = document.getElementById('buildRecordLogBody');
  if (!titleEl || !bodyEl) return;
  titleEl.innerText = title;
  bodyEl.innerText = content || '暂无日志内容';
  openModal('buildRecordLogModal');
}

export async function loadBuildRecordsBoard() {
  setLoading();
  try {
    const data = await (await api('/api/admin/build-records?limit=100&offset=0')).json();
    state.records = (data.items || []).slice().sort((a, b) => {
      const ta = new Date(a.created_at || 0).getTime();
      const tb = new Date(b.created_at || 0).getTime();
      return tb - ta;
    });
    fillMajorFilterOptions();
    renderCards();
  } catch (err) {
    setLoading(err.message || '加载构建记录失败');
    throw err;
  }
}

export function onBuildRecordsMajorFilterChange() {
  renderCards();
}

export function openBuildRecordLogModal(recordId) {
  const row = (state.records || []).find((item) => Number(item.id) === Number(recordId));
  if (!row) {
    window.showMessage && window.showMessage('未找到对应构建记录', 'error');
    return;
  }
  renderLogModal(`${jobNameToMajorLabel(row.job_name)} / ${row.version_name || '小版本日志'}`, row.change_log || '暂无日志内容');
}

export function openMajorBuildLogModal() {
  const row = getLatestRecordForSelectedMajor();
  if (!row) {
    window.showMessage && window.showMessage('暂无大版本日志内容', 'error');
    return;
  }
  // 第一版占位逻辑：直接显示当前筛选大版本下最新一条构建记录的 change_log。
  renderLogModal(`${jobNameToMajorLabel(row.job_name)} 大版本修改日志`, row.change_log || '暂无大版本日志内容');
}

export function closeBuildRecordLogModal() {
  closeModal('buildRecordLogModal');
}

window.OmniQABuildRecordsTab = {
  loadBuildRecordsBoard,
  onBuildRecordsMajorFilterChange,
  openBuildRecordLogModal,
  openMajorBuildLogModal,
  closeBuildRecordLogModal,
  jobNameToMajorLabel,
};
