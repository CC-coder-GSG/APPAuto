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

// ── 导出本周工作内容 txt/md（先弹设置：勾选大版本/人员/格式/图表，确认后按勾选导出）──

// md 附图数据：打开设置弹窗时按本周区间拉 /reports/summary（user_id=0 全员视角，
// 非管理员会被后端降级为个人视角、无 team_comparison → 团队对比选项置灰）
let weeklyChartData = null;
let weeklyChartPromise = null;

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

function resetWeeklyExportOptions() {
  const txtRadio = document.querySelector('#weeklyExportModal input[name="weeklyExportFmt"][value="txt"]');
  if (txtRadio) txtRadio.checked = true;
  const area = document.getElementById('weeklyExportChartsArea');
  if (area) area.style.display = 'none';
  ['weeklyChartTrend', 'weeklyChartBugDist', 'weeklyChartTeam'].forEach((id) => {
    const cb = document.getElementById(id);
    if (cb) { cb.checked = true; cb.disabled = false; }
  });
  const teamArea = document.getElementById('weeklyExportTeamArea');
  if (teamArea) teamArea.style.display = '';
  const membersWrap = document.getElementById('weeklyExportTeamMembers');
  if (membersWrap) membersWrap.innerHTML = '<span class="muted" style="font-size:12px;">加载中…</span>';
  const chartsHint = document.getElementById('weeklyExportChartsHint');
  if (chartsHint) chartsHint.textContent = '';
}

async function loadWeeklyChartData(weekStart, weekEnd) {
  const chartsHint = document.getElementById('weeklyExportChartsHint');
  const membersWrap = document.getElementById('weeklyExportTeamMembers');
  const teamCb = document.getElementById('weeklyChartTeam');
  const teamArea = document.getElementById('weeklyExportTeamArea');
  const membersAll = document.getElementById('weeklyExportMembersAll');
  try {
    weeklyChartData = await (await api(`/reports/summary?start_date=${weekStart}&end_date=${weekEnd}&user_id=0`)).json();
  } catch (e) {
    weeklyChartData = null;
    if (chartsHint) chartsHint.textContent = '图表数据加载失败，本次导出将不包含图表';
    return;
  }
  const team = weeklyChartData.team_comparison || [];
  if (team.length) {
    if (membersWrap) renderWeeklyChecklist(membersWrap, team.map((i) => i.username), 'member');
    if (membersAll) membersAll.checked = true;
  } else {
    if (teamCb) { teamCb.checked = false; teamCb.disabled = true; }
    if (teamArea) teamArea.style.display = 'none';
    if (chartsHint) chartsHint.textContent = '团队对比需管理员全员视角，当前账号不可导出该图';
  }
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
    resetWeeklyExportOptions();
    weeklyChartData = null;
    weeklyChartPromise = loadWeeklyChartData(data.week_start, data.week_end);
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载本周任务数据失败', 'error');
  }
}

function weeklyFmt() {
  const checked = document.querySelector('#weeklyExportModal input[name="weeklyExportFmt"]:checked');
  return checked ? checked.value : 'txt';
}

// 格式切换：md 才显示图表勾选区
export function weeklyExportFmtChange() {
  const area = document.getElementById('weeklyExportChartsArea');
  if (area) area.style.display = weeklyFmt() === 'md' ? '' : 'none';
}

export function weeklyChartTeamToggle(checked) {
  const teamArea = document.getElementById('weeklyExportTeamArea');
  if (teamArea) teamArea.style.display = checked ? '' : 'none';
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
  const allIds = { version: 'weeklyExportVersionsAll', person: 'weeklyExportPersonsAll', member: 'weeklyExportMembersAll' };
  const all = document.getElementById(allIds[kind]);
  if (all) all.checked = boxes.length > 0 && boxes.every((cb) => cb.checked);
}

function weeklyPicked(kind) {
  const boxes = [...document.querySelectorAll(`#weeklyExportModal input[data-weekly-${kind}]`)];
  return { picked: boxes.filter((cb) => cb.checked).map((cb) => cb.value), total: boxes.length };
}

// 离屏渲染 ECharts → PNG dataURL（供 md 内嵌图片；Typora/VSCode 等可直接显示）
function renderWeeklyChartPng(option, width = 860, height = 420) {
  if (typeof echarts === 'undefined') return '';
  const el = document.createElement('div');
  el.style.cssText = `position:fixed; left:-10000px; top:0; width:${width}px; height:${height}px;`;
  document.body.appendChild(el);
  const chart = echarts.init(el, null, { renderer: 'canvas' });
  try {
    chart.setOption({ animation: false, backgroundColor: '#ffffff', ...option });
    return chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#ffffff' });
  } catch (e) {
    console.error('导出图表渲染失败:', e);
    return '';
  } finally {
    chart.dispose();
    el.remove();
  }
}

function weeklyTrendOption(trend) {
  const metricDefs = [
    ['执行需求', 'executed_requirements'],
    ['创建用例', 'created_cases'],
    ['创建Bug', 'created_bugs'],
    ['创建反馈', 'created_feedbacks'],
    ['处理反馈', 'processed_feedbacks'],
    ['复测需求', 'retested_reqs'],
    ['关闭Bug', 'closed_bugs'],
  ];
  return {
    title: { text: '趋势折线图' },
    grid: { top: 60, bottom: 40, left: 50, right: 30 },
    legend: { data: metricDefs.map(([n]) => n), top: 30 },
    xAxis: { type: 'category', data: trend.map((i) => i.date) },
    yAxis: { type: 'value' },
    series: metricDefs.map(([name, key]) => ({ name, type: 'line', smooth: true, data: trend.map((i) => i[key] || 0) })),
  };
}

function weeklyBugDistOption(rows) {
  const pieData = rows.map((i) => ({ name: i.major_version_no || '未关联大版本', value: i.count }));
  return {
    title: { text: '大版本Bug分布', left: 'center' },
    legend: { top: 'bottom', type: 'scroll' },
    series: [{ type: 'pie', radius: '50%', center: ['50%', '52%'], showEmptyCircle: false, minShowLabelAngle: 10, label: { formatter: '{b}: {c}个' }, data: pieData }],
    graphic: pieData.length ? [] : [{
      type: 'text', left: 'center', top: 'middle',
      style: { text: '本周暂无 Bug 数据', fill: '#94a3b8', fontSize: 13 },
    }],
  };
}

function weeklyTeamOption(team) {
  const names = team.map((i) => i.username);
  const metricDefs = [
    ['执行需求', 'executed_requirements'],
    ['创建用例', 'created_cases'],
    ['创建Bug', 'created_bugs'],
    ['处理反馈', 'processed_feedbacks'],
    ['复测需求', 'retested_reqs'],
    ['关闭Bug', 'closed_bugs'],
  ];
  const series = metricDefs.map(([name, key]) => ({ name, type: 'bar', data: team.map((i) => i[key] || 0) }));
  const common = {
    title: { text: '团队对比' },
    legend: { type: 'scroll', data: metricDefs.map(([n]) => n), top: 28 },
    series,
  };
  // 与报表页同口径：人数多转横向条形，导出图不能滚动 → 靠加高画布放下全部人
  if (team.length > 6) {
    return {
      ...common,
      grid: { top: 64, bottom: 40, left: 90, right: 56 },
      xAxis: { type: 'value' },
      yAxis: { type: 'category', data: names, inverse: true, axisLabel: { interval: 0, fontSize: 12 } },
    };
  }
  const rotate = team.length > 4 ? 22 : 0;
  return {
    ...common,
    grid: { top: 64, bottom: rotate ? 70 : 44, left: 50, right: 30 },
    xAxis: { type: 'category', data: names, axisLabel: { interval: 0, rotate, fontSize: 12 } },
    yAxis: { type: 'value' },
  };
}

function weeklyChartMdSection(title, option, height = 420) {
  const url = renderWeeklyChartPng(option, 860, height);
  return url ? `### ${title}\n\n![${title}](${url})\n` : '';
}

async function buildWeeklyChartsMd() {
  if (weeklyChartPromise) { try { await weeklyChartPromise; } catch (e) { /* 图表数据加载失败已提示 */ } }
  if (!weeklyChartData) return '';
  const want = (id) => { const cb = document.getElementById(id); return !!cb && cb.checked && !cb.disabled; };
  const parts = [];
  if (want('weeklyChartTrend')) parts.push(weeklyChartMdSection('趋势折线图', weeklyTrendOption(weeklyChartData.trend || [])));
  if (want('weeklyChartBugDist')) parts.push(weeklyChartMdSection('大版本Bug分布', weeklyBugDistOption(weeklyChartData.bug_major_dist || [])));
  if (want('weeklyChartTeam')) {
    const picked = weeklyPicked('member').picked;
    const rows = (weeklyChartData.team_comparison || []).filter((i) => picked.includes(i.username));
    if (rows.length) {
      const height = rows.length > 6 ? Math.max(420, rows.length * 78 + 120) : 420;
      parts.push(weeklyChartMdSection('团队对比', weeklyTeamOption(rows), height));
    }
  }
  return parts.filter(Boolean).join('\n');
}

function downloadTextFile(content, filename, mime) {
  const blob = new Blob([content], { type: mime });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
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
  const fmt = weeklyFmt();
  const teamCb = document.getElementById('weeklyChartTeam');
  if (fmt === 'md' && teamCb && teamCb.checked && !teamCb.disabled) {
    const members = weeklyPicked('member');
    if (members.total && !members.picked.length) {
      window.showMessage && window.showMessage('团队对比已勾选，请至少勾选一个团队成员（或取消团队对比）', 'info');
      return;
    }
  }
  const params = new URLSearchParams();
  // 全选时不传参（导出全部），部分勾选才传清单
  if (versions.picked.length < versions.total) params.set('versions', versions.picked.join(','));
  if (persons.picked.length < persons.total) params.set('persons', persons.picked.join(','));
  const qs = params.toString();
  try {
    const data = await (await api('/reports/weekly-task-export' + (qs ? `?${qs}` : ''))).json();
    if (fmt === 'md') {
      let md = (data.markdown || '').replace(/\s+$/, '') + '\n';
      const charts = await buildWeeklyChartsMd();
      if (charts) md += `\n---\n\n## 图表\n\n${charts}`;
      downloadTextFile(md, `本周工作内容_${data.week_start}_${data.week_end}.md`, 'text/markdown;charset=utf-8');
    } else {
      downloadTextFile(data.text || '', `本周工作内容_${data.week_start}_${data.week_end}.txt`, 'text/plain;charset=utf-8');
    }
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
  weeklyExportFmtChange,
  weeklyChartTeamToggle,
  loadZentaoSyncStats,
};
window.openGovernanceDetail = openGovernanceDetail;
window.closeGovernanceDetail = closeGovernanceDetail;
window.loadZentaoSyncStats = loadZentaoSyncStats;
