import { api } from '../api.js';
import { state } from '../state.js';
import { renderGovernanceCharts, renderReportCharts, renderVersionBugChart } from '../components/charts.js?v=20260706-2';
import { renderBugLink, sourceTypeZh } from '../utils.js';

let governanceCache = null;
let reportSseBound = false;
let reportRefreshTimer = null;

function getCurrentUser() {
  return state.currentUser || window.currentUser || null;
}

function isAllUsersMode() {
  const currentUser = getCurrentUser();
  return currentUser && currentUser.role === 'admin' && window.reportUserSelect && window.reportUserSelect.value === '0';
}

function reqStatusZh(v) {
  const m = {
    pending: '待开始',
    assigned: '已分配',
    case_done: '用例完成',
    testing: '测试中',
    test_done: '测试完成',
    retest_pending: '待复测',
    retest_done: '复测完成',
  };
  return m[v] || v;
}

function renderSimpleTable(tbodyId, rows, renderer, emptyText = '暂无数据') {
  const el = document.getElementById(tbodyId);
  if (!el) return;
  if (!rows || rows.length === 0) {
    el.innerHTML = `<tr><td colspan="99" style="text-align:center; color:#94a3b8; padding:12px;">${emptyText}</td></tr>`;
    return;
  }
  el.innerHTML = rows.map(renderer).join('');
}

function renderGovernanceBoard(data) {
  if (!data) return;
  governanceCache = data;
  const k = data.kpis || {};
  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.innerText = val;
  };

  setVal('gReqOverdue', k.overdue_requirements || 0);
  setVal('gFbOverdue', k.overdue_feedbacks || 0);
  setVal('gBugUnassigned', k.unassigned_bugs || 0);
  setVal('gBugOverdue', k.overdue_bugs || 0);
  setVal('gBugStale', k.stale_bugs || 0);
  setVal('gBugAssignedNoProgress', k.assigned_no_progress_bugs || 0);
  setVal('gFeedbackToBugRatio', `${k.feedback_to_bug_ratio || 0}%`);
  const metaHintEl = document.getElementById('governanceMetaHint');
  if (metaHintEl) {
    metaHintEl.innerText = data.meta?.degraded_reason || '';
  }

  renderSimpleTable('gTopReqFeedbackTable', data.requirements?.top_feedback_reqs || [], (r) => `
    <tr>
      <td>${r.zentao_req_id} ${r.title || ''}</td>
      <td>${r.major_version_no || '-'}</td>
      <td>${reqStatusZh(r.status)}</td>
      <td><b>${r.feedback_count || 0}</b></td>
    </tr>
  `);

  renderSimpleTable('gTopReqBugTable', data.requirements?.top_bug_reqs || [], (r) => `
    <tr>
      <td>${r.zentao_req_id} ${r.title || ''}</td>
      <td>${r.major_version_no || '-'}</td>
      <td>${reqStatusZh(r.status)}</td>
      <td><b>${r.bug_count || 0}</b></td>
    </tr>
  `);

  renderSimpleTable('gFeedbackVersionTopTable', data.feedback?.version_top10 || [], (r) => `
    <tr>
      <td>${r.major_version_no || '-'} / ${r.minor_version_no || '-'}</td>
      <td>${r.total || 0}</td>
      <td>${r.done || 0}</td>
      <td>${r.undone || 0}</td>
    </tr>
  `);

  renderSimpleTable('gStaleBugTopTable', data.bugs?.top_stale || [], (r) => `
    <tr>
      <td>${renderBugLink(r)}</td>
      <td>${r.major_version_no || '-'}</td>
      <td>${r.dispatched_to_name || '未指派'}</td>
      <td><b>${r.stale_days || 0}</b></td>
    </tr>
  `);

  renderGovernanceCharts(data);
}

function renderGovernanceDetail(headers, rows, emptyText = '暂无数据') {
  const headEl = document.getElementById('governanceDetailHead');
  const bodyEl = document.getElementById('governanceDetailBody');
  if (!headEl || !bodyEl) return;
  headEl.innerHTML = `<tr>${headers.map((h) => `<th>${h}</th>`).join('')}</tr>`;
  if (!rows || rows.length === 0) {
    bodyEl.innerHTML = `<tr><td colspan="${headers.length}" style="text-align:center; color:#94a3b8; padding:12px;">${emptyText}</td></tr>`;
    return;
  }
  bodyEl.innerHTML = rows.join('');
}

export function closeGovernanceDetail() {
  const card = document.getElementById('governanceDetailCard');
  if (card) card.classList.add('hidden');
}

export function openGovernanceDetail(type) {
  const card = document.getElementById('governanceDetailCard');
  const titleEl = document.getElementById('governanceDetailTitle');
  const hintEl = document.getElementById('governanceDetailHint');
  if (!card || !titleEl || !hintEl || !governanceCache) return;

  let headers = [];
  let rows = [];
  let title = '治理明细';
  let hint = '';

  if (type === 'overdue_requirements') {
    title = '超时未关闭需求明细';
    hint = `超时阈值：${governanceCache.meta?.req_overdue_days || 14} 天`;
    headers = ['需求', '版本', '状态', '负责人', '创建时间', '持续天数'];
    rows = (governanceCache.requirements?.overdue_list || []).map((r) => `<tr>
      <td>${r.zentao_req_id} ${r.title || ''}</td><td>${r.major_version_no || '-'}</td><td>${reqStatusZh(r.status)}</td>
      <td>${r.owner_name || '未分配'}</td><td>${r.created_at ? new Date(r.created_at).toLocaleString() : '-'}</td><td><b>${r.age_days || 0}</b></td></tr>`);
  } else if (type === 'overdue_feedbacks') {
    title = '超时未处理反馈明细';
    hint = `超时阈值：${governanceCache.meta?.feedback_overdue_days || 7} 天`;
    headers = ['反馈编号', '概览', '来源版本', '状态', '处理人', '创建时间', '持续天数'];
    rows = (governanceCache.feedback?.overdue_list || []).map((r) => `<tr>
      <td>${r.feedback_no || '-'}</td><td>${(r.summary || '').slice(0, 40)}</td><td>${r.major_version_no || '-'} / ${r.minor_version_no || '-'}</td>
      <td>${r.status || '-'}</td><td>${r.assignee_name || '未指派'}</td><td>${r.created_at ? new Date(r.created_at).toLocaleString() : '-'}</td><td><b>${r.age_days || 0}</b></td></tr>`);
  } else if (type === 'unassigned_bugs') {
    title = '无人处理 Bug 明细';
    hint = '口径：未关闭且未指派';
    headers = ['Bug编号', '版本', '状态', '创建时间', '持续天数'];
    rows = (governanceCache.bugs?.unassigned_list || []).map((r) => `<tr>
      <td>${renderBugLink(r)}</td><td>${r.major_version_no || '-'}</td><td>${r.status || '-'}</td><td>${r.created_at ? new Date(r.created_at).toLocaleString() : '-'}</td><td><b>${r.age_days || 0}</b></td></tr>`);
  } else if (type === 'overdue_bugs') {
    title = '超时未关闭 Bug 明细';
    hint = `超时阈值：${governanceCache.meta?.bug_overdue_days || 7} 天`;
    headers = ['Bug编号', '版本', '指派给', '创建时间', '持续天数'];
    rows = (governanceCache.bugs?.overdue_list || []).map((r) => `<tr>
      <td>${renderBugLink(r)}</td><td>${r.major_version_no || '-'}</td><td>${r.dispatched_to_name || '未指派'}</td><td>${r.created_at ? new Date(r.created_at).toLocaleString() : '-'}</td><td><b>${r.age_days || 0}</b></td></tr>`);
  } else if (type === 'stale_bugs') {
    title = '长期未更新 Bug 明细';
    hint = `未更新阈值：${governanceCache.meta?.stale_bug_days || 14} 天`;
    headers = ['Bug编号', '版本', '指派给', '最后更新时间', '未更新天数'];
    rows = (governanceCache.bugs?.stale_list || []).map((r) => `<tr>
      <td>${renderBugLink(r)}</td><td>${r.major_version_no || '-'}</td><td>${r.dispatched_to_name || '未指派'}</td><td>${r.updated_at ? new Date(r.updated_at).toLocaleString() : '-'}</td><td><b>${r.stale_days || 0}</b></td></tr>`);
  } else if (type === 'assigned_no_progress_bugs') {
    title = '已指派但未处理 Bug 明细';
    hint = `口径：已指派 + 未关闭 + 连续${governanceCache.meta?.bug_overdue_days || 7}天无更新`;
    headers = ['Bug编号', '版本', '指派给', '最后更新时间', '未更新天数'];
    rows = (governanceCache.bugs?.assigned_no_progress_list || []).map((r) => `<tr>
      <td>${renderBugLink(r)}</td><td>${r.major_version_no || '-'}</td><td>${r.dispatched_to_name || '未指派'}</td><td>${r.updated_at ? new Date(r.updated_at).toLocaleString() : '-'}</td><td><b>${r.stale_days || 0}</b></td></tr>`);
  }

  titleEl.innerText = title;
  hintEl.innerText = hint;
  renderGovernanceDetail(headers, rows);
  card.classList.remove('hidden');
}

export async function queryReport() {
  const currentUser = getCurrentUser();
  if (!currentUser) {
    throw new Error('当前用户信息未加载，请刷新页面后重试');
  }

  const startDate = window.reportStartDate.value;
  const endDate = window.reportEndDate.value;
  const reportMajorSelect = document.getElementById('reportMajorSelect');
  const selectedMajorId = Number(reportMajorSelect?.value || 0);
  const currentSoftwareId = Number(window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || 0);

  let summaryUrl = `/reports/summary?start_date=${startDate}&end_date=${endDate}`;
  if (currentUser.role === 'admin' && window.reportUserSelect?.value) {
    summaryUrl += `&user_id=${window.reportUserSelect.value}`;
  }
  if (selectedMajorId) {
    summaryUrl += `&major_version_id=${selectedMajorId}`;
  }
  if (currentSoftwareId) {
    summaryUrl += `&software_id=${currentSoftwareId}`;
  }

  const data = await (await api(summaryUrl)).json();
  state.reportLoaded = true;
  window.reportLoaded = true;

  window.mReq.innerText = data.overview.executed_requirements || 0;
  window.mCase.innerText = data.overview.created_cases || 0;
  window.mBug.innerText = data.overview.created_bugs || 0;
  window.mRetest.innerText = data.overview.retested_reqs || 0;
  window.mClosed.innerText = data.overview.closed_bugs || 0;

  const vbUrl = selectedMajorId ? `/reports/version-bugs?major_version_id=${selectedMajorId}` : '/reports/version-bugs';
  const versionBugs = await (await api(vbUrl)).json();

  const advUrl = selectedMajorId
    ? `/reports/advanced?start_date=${startDate}&end_date=${endDate}&major_version_id=${selectedMajorId}${currentSoftwareId ? `&software_id=${currentSoftwareId}` : ''}`
    : `/reports/advanced?start_date=${startDate}&end_date=${endDate}${currentSoftwareId ? `&software_id=${currentSoftwareId}` : ''}`;
  const advancedData = await (await api(advUrl)).json();

  const govUrl = selectedMajorId
    ? `/reports/governance?start_date=${startDate}&end_date=${endDate}&major_version_id=${selectedMajorId}${currentSoftwareId ? `&software_id=${currentSoftwareId}` : ''}`
    : `/reports/governance?start_date=${startDate}&end_date=${endDate}${currentSoftwareId ? `&software_id=${currentSoftwareId}` : ''}`;
  const governanceData = await (await api(govUrl)).json();
  const fieldTestUrl = selectedMajorId
    ? `/reports/field-test?start_date=${startDate}&end_date=${endDate}&major_version_id=${selectedMajorId}${currentSoftwareId ? `&software_id=${currentSoftwareId}` : ''}${currentUser.role === 'admin' && window.reportUserSelect?.value ? `&user_id=${window.reportUserSelect.value}` : ''}`
    : `/reports/field-test?start_date=${startDate}&end_date=${endDate}${currentSoftwareId ? `&software_id=${currentSoftwareId}` : ''}${currentUser.role === 'admin' && window.reportUserSelect?.value ? `&user_id=${window.reportUserSelect.value}` : ''}`;
  let fieldTestData = null;
  try {
    fieldTestData = await (await api(fieldTestUrl)).json();
  } catch (e) {
    fieldTestData = { overview: {}, by_day: [], by_purpose: [], by_user: [], mode: 'personal' };
  }

  renderReportCharts(data, advancedData, {
    sourceTypeZh,
    isAllUsersMode: isAllUsersMode(),
    versionBugs,
    fieldTestData,
  });
  renderGovernanceBoard(governanceData);

  // 「各发包 Bug 检出分布」独立大版本筛选：默认跟随报表选的大版本，
  // 之后可单独切换、即时重渲染该图（不重查整张报表）。
  const vbMajorSelect = document.getElementById('vbMajorSelect');
  if (vbMajorSelect) {
    vbMajorSelect.value = String(selectedMajorId || 0);
    if (!vbMajorSelect.dataset.bound) {
      vbMajorSelect.dataset.bound = '1';
      vbMajorSelect.addEventListener('change', async () => {
        const mid = Number(vbMajorSelect.value || 0);
        const url = mid ? `/reports/version-bugs?major_version_id=${mid}` : '/reports/version-bugs';
        try {
          const rows = await (await api(url)).json();
          renderVersionBugChart(rows);
        } catch (e) {
          window.showMessage && window.showMessage('加载发包 Bug 分布失败', 'error');
        }
      });
    }
  }

  if (typeof window.adjustReportChartVisibility === 'function') {
    window.adjustReportChartVisibility();
  }
  if (typeof window.showMessage === 'function') {
    window.showMessage('报表查询成功', 'success');
  }

  // Load Zentao sync stats alongside governance data
  loadZentaoSyncStats().catch(() => {});
}

export function exportReportPdf() {
  const element = document.getElementById('reportContent');
  if (!element) return;
  if (typeof html2pdf === 'undefined') {
    window.showMessage && window.showMessage('PDF 导出组件未加载，请检查 /static/vendor/html2pdf.bundle.min.js', 'error');
    return;
  }

  // 物理隔离 0x0 canvas，避免 html2canvas 在克隆阶段崩溃
  const badCanvases = Array.from(element.querySelectorAll('canvas')).filter((c) => c.width === 0 || c.height === 0);
  const restoredList = badCanvases.map((canvas) => {
    const placeholder = document.createComment('hidden-canvas-placeholder');
    canvas.parentNode.insertBefore(placeholder, canvas);
    canvas.parentNode.removeChild(canvas);
    return { canvas, placeholder };
  });

  if (typeof window.showMessage === 'function') {
    window.showMessage('正在生成高清晰度报表 PDF，耗时约几秒钟，请稍候...', 'success');
  }

  const opt = {
    margin: 0.3,
    filename: '测试效能度量大盘.pdf',
    image: { type: 'jpeg', quality: 0.98 },
    html2canvas: { scale: 2, useCORS: true, backgroundColor: '#f8fafc' },
    jsPDF: { unit: 'in', format: 'a4', orientation: 'portrait' },
  };

  html2pdf().set(opt).from(element).save().then(() => {
    window.showMessage && window.showMessage('🎉 报表 PDF 导出成功！', 'success');
  }).catch((err) => {
    console.error('PDF导出失败:', err);
    window.showMessage && window.showMessage('导出失败，请打开 F12 查看报错', 'error');
  }).finally(() => {
    restoredList.forEach((item) => {
      if (item.placeholder && item.placeholder.parentNode) {
        item.placeholder.parentNode.insertBefore(item.canvas, item.placeholder);
        item.placeholder.parentNode.removeChild(item.placeholder);
      }
    });
  });
}

// ── 导出本周工作内容 txt（先弹设置：勾选大版本/人员，确认后按勾选导出）──

function weeklyModalEls() {
  return {
    modal: document.getElementById('weeklyExportModal'),
    hint: document.getElementById('weeklyExportHint'),
    versionsWrap: document.getElementById('weeklyExportVersions'),
    personsWrap: document.getElementById('weeklyExportPersons'),
    versionsAll: document.getElementById('weeklyExportVersionsAll'),
    personsAll: document.getElementById('weeklyExportPersonsAll'),
  };
}

function renderWeeklyChecklist(wrap, items, kind) {
  wrap.innerHTML = items.length
    ? items.map((name) => `
        <label style="display:flex; align-items:center; gap:5px; font-size:13px; cursor:pointer;">
          <input type="checkbox" data-weekly-${kind} value="${name.replace(/"/g, '&quot;')}" checked
            onchange="weeklyExportSyncAll('${kind}')">${name}
        </label>`).join('')
    : '<span class="muted" style="font-size:12px;">本周暂无数据</span>';
}

// 点「导出本周工作」→ 拉取本周全集（版本/人员清单），弹设置窗
export async function exportWeeklyTasks() {
  const { modal, hint, versionsWrap, personsWrap, versionsAll, personsAll } = weeklyModalEls();
  if (!modal) return;
  try {
    const data = await (await api('/reports/weekly-task-export')).json();
    hint.textContent = `本周：${data.week_start} ~ ${data.week_end}，勾选要导出的大版本与人员（默认全选）`;
    renderWeeklyChecklist(versionsWrap, data.available_versions || [], 'version');
    renderWeeklyChecklist(personsWrap, data.available_persons || [], 'person');
    if (versionsAll) versionsAll.checked = true;
    if (personsAll) personsAll.checked = true;
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载本周任务数据失败', 'error');
  }
}

export function closeWeeklyExportModal() {
  const { modal } = weeklyModalEls();
  if (!modal) return;
  modal.classList.add('hidden');
  modal.style.display = 'none';
}

export function weeklyExportToggleAll(kind, checked) {
  document.querySelectorAll(`#weeklyExportModal input[data-weekly-${kind}]`).forEach((cb) => { cb.checked = checked; });
}

// 单项勾选变化时同步「全选」框状态
export function weeklyExportSyncAll(kind) {
  const boxes = [...document.querySelectorAll(`#weeklyExportModal input[data-weekly-${kind}]`)];
  const all = document.getElementById(kind === 'version' ? 'weeklyExportVersionsAll' : 'weeklyExportPersonsAll');
  if (all) all.checked = boxes.length > 0 && boxes.every((cb) => cb.checked);
}

function weeklyPicked(kind) {
  const boxes = [...document.querySelectorAll(`#weeklyExportModal input[data-weekly-${kind}]`)];
  return { picked: boxes.filter((cb) => cb.checked).map((cb) => cb.value), total: boxes.length };
}

export async function confirmWeeklyExport() {
  const versions = weeklyPicked('version');
  const persons = weeklyPicked('person');
  if (versions.total && !versions.picked.length) {
    window.showMessage && window.showMessage('请至少勾选一个大版本', 'info');
    return;
  }
  if (persons.total && !persons.picked.length) {
    window.showMessage && window.showMessage('请至少勾选一个人员', 'info');
    return;
  }
  const params = new URLSearchParams();
  // 全选时不传参（导出全部），部分勾选才传清单
  if (versions.picked.length < versions.total) params.set('versions', versions.picked.join(','));
  if (persons.picked.length < persons.total) params.set('persons', persons.picked.join(','));
  const qs = params.toString();
  try {
    const data = await (await api('/reports/weekly-task-export' + (qs ? `?${qs}` : ''))).json();
    const blob = new Blob([data.text || ''], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `本周工作内容_${data.week_start}_${data.week_end}.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
    closeWeeklyExportModal();
    window.showMessage && window.showMessage('本周工作内容已导出', 'success');
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '导出本周工作失败', 'error');
  }
}

function bindReportSSE() {
  if (reportSseBound) return;
  if (!window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;
  window.OmniQASSE.subscribe('report_data_changed', () => {
    const tab = document.getElementById('tab-report');
    if (!tab || tab.classList.contains('hidden')) return;
    if (reportRefreshTimer) clearTimeout(reportRefreshTimer);
    reportRefreshTimer = setTimeout(() => {
      queryReport().catch(() => {});
    }, 900);
  });
  reportSseBound = true;
}

bindReportSSE();

// ── Zentao Sync Stats ─────────────────────────────────────────────────────────

let syncStatsCache = null;

export async function loadZentaoSyncStats() {
  const majorId = window.reportMajorFilter?.value || '';
  const softwareId = window.currentSoftwareId || localStorage.getItem('currentSoftwareId') || '';
  let url = '/reports/zentao-sync-stats?stale_minutes=60';
  if (majorId && majorId !== '0') url += `&major_version_id=${majorId}`;
  else if (softwareId) url += `&software_id=${softwareId}`;

  try {
    const resp = await api(url);
    const data = await resp.json();
    syncStatsCache = data;
    renderZentaoSyncStats(data);
  } catch (err) {
    const el = document.getElementById('zentaoSyncStatsArea');
    if (el) el.innerHTML = `<div class="muted" style="padding:12px;">加载禅道同步统计失败: ${err.message || '未知错误'}</div>`;
  }
}

function renderZentaoSyncStats(data) {
  if (!data) return;
  const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
  setVal('zsStaleCount', data.stale_sync_count || 0);
  setVal('zsZtClosedNoLocalCount', data.zentao_closed_no_local_count || 0);
  setVal('zsLocalClosedNoZtCount', data.local_closed_no_zentao_count || 0);
  setVal('zsDeletedCount', data.zentao_deleted_count || 0);

  // Detail tables
  renderZentaoSyncTable('zsStaleTable',
    data.stale_sync || [],
    ['bug_id', 'title', 'status', 'last_synced_at'],
    ['Bug编号', '标题', '禅道状态', '上次同步']
  );
  renderZentaoSyncTable('zsZtClosedNoLocalTable',
    data.zentao_closed_no_local || [],
    ['bug_id', 'title', 'closed_by', 'close_date'],
    ['Bug编号', '标题', '关闭人', '关闭时间']
  );
  renderZentaoSyncTable('zsLocalClosedNoZtTable',
    data.local_closed_no_zentao || [],
    ['bug_id', 'title', 'zentao_status', 'last_synced_at'],
    ['Bug编号', '标题', '禅道状态', '上次同步']
  );
}

function renderZentaoSyncTable(tbodyId, rows, fields, headers) {
  const el = document.getElementById(tbodyId);
  if (!el) return;
  const thead = document.getElementById(tbodyId + 'Head');
  if (thead) {
    thead.innerHTML = `<tr>${headers.map((h) => `<th>${h}</th>`).join('')}</tr>`;
  }
  if (!rows || rows.length === 0) {
    el.innerHTML = `<tr><td colspan="${fields.length}" style="text-align:center; color:#94a3b8; padding:8px;">暂无数据 ✓</td></tr>`;
    return;
  }
  el.innerHTML = rows.map((row) =>
    `<tr>${fields.map((f) => `<td style="font-size:13px; color:#475569;">${row[f] || '-'}</td>`).join('')}</tr>`
  ).join('');
}

window.OmniQAReportTab = {
  queryReport,
  exportReportPdf,
  exportWeeklyTasks,
  closeWeeklyExportModal,
  confirmWeeklyExport,
  weeklyExportToggleAll,
  weeklyExportSyncAll,
  loadZentaoSyncStats,
};
window.openGovernanceDetail = openGovernanceDetail;
window.closeGovernanceDetail = closeGovernanceDetail;
window.loadZentaoSyncStats = loadZentaoSyncStats;
