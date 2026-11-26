import { api } from '../api.js';
import { escapeHtml as esc } from '../utils.js?v=20261126-hc-1';
import { openDrawer, closeDrawer } from '../components/side-drawer.js?v=20261126-zai-1';

// Bugly 问题中心（2026-11-26 并入主应用页签，原 /bugly 独立页面下线；样式 weave.css R23 .bgl-*）
// 访问控制只看数据中心勾选的「Bugly 问题中心」页面权限：有权限即可查看、编辑、导入，不再有应用 / 问题级授权、
// 外部只读账号与对外发布。
//   列表：状态筛选条（带计数）+ 筛选栏 + 问题表 / 逐条上报表 + 分页
//   详情：在页签内切换（返回列表保留筛选和页码）——概览、分析结论、上报列表 + 堆栈查看器
//   表单：右侧抽屉（新增问题 / 编辑问题 / 新增上报 / 导入文件 / 导入记录 / 应用管理）

const STATUS = {
  pending: { text: '待分析', tone: 'orange' },
  processing: { text: '处理中', tone: 'blue' },
  verifying: { text: '待验证', tone: 'amber' },
  closed: { text: '已关闭', tone: 'green' },
  ignored: { text: '忽略', tone: 'gray' },
};
const STACK_MODE = { parsed: '解析堆栈', manual: '手动录入', fallback: '兜底结果 · 待检查' };
const ACTION_TEXT = { created: '新增', updated: '更新', skipped: '跳过', failed: '无效' };
const PAGE_SIZES = [20, 50, 100];

const state = {
  inited: false,
  apps: [],
  view: 'issues',            // issues | reports
  renderedView: '',
  status: '',
  page: 1,
  size: 20,
  total: 0,
  statusCounts: {},
  loading: false,
  listSeq: 0,
  issue: null,               // 当前详情
  reports: [],
  reportPage: 1,
  reportTotal: 0,
  reportQuery: '',
  reportSeq: 0,
  reportListSeq: 0,
  detailSeq: 0,
  activeReportId: 0,
  stack: '',
};

const $ = (sel, root = document) => root.querySelector(sel);
const ic = (name, cls = '') => `<svg class="ui-ic${cls ? ` ${cls}` : ''}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const toast = (msg, type = 'success') => window.showMessage && window.showMessage(msg, type);
const root = () => document.getElementById('buglyRoot');
const json = async (url, options) => (await api(url, options)).json();
const bapi = (path, options) => json(`/api/bugly${path}`, options);

function fmt(value, fallback = '未采集') {
  if (!value) return fallback;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function rel(value) {
  if (!value) return '';
  const diff = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(diff)) return '';
  const m = Math.round(diff / 60000);
  if (m < 1) return '刚刚';
  if (m < 60) return `${m} 分钟前`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} 天前`;
  return fmt(value).slice(0, 10);
}

function statusChip(status) {
  const meta = STATUS[status] || { text: status || '—', tone: 'gray' };
  return `<span class="bgl-status bgl-tone--${meta.tone}">${esc(meta.text)}</span>`;
}

function userName(id) {
  if (!id) return '';
  const u = (window.users || []).find((x) => Number(x.id) === Number(id));
  return u ? (u.display_name || u.username) : `用户 #${id}`;
}

function sourceLabel(item) {
  return item.source_issue_id ? `Bugly #${esc(item.source_issue_id)}` : '手动问题';
}

function appOptions(selected = '', withAll = false) {
  const head = withAll ? '<option value="">全部应用</option>' : '';
  return head + state.apps.map((a) => `<option value="${a.id}" ${String(a.id) === String(selected) ? 'selected' : ''}>${esc(a.name)}${a.package_name ? ` · ${esc(a.package_name)}` : ''}</option>`).join('');
}

function defaultAppId() {
  return $('#bglAppFilter')?.value || state.apps[0]?.id || '';
}

// ── 骨架 ─────────────────────────────────────────────

function renderShell() {
  root().innerHTML = `
    <div id="bglList">
      <div class="bgl-statusbar" id="bglStatusBar" role="tablist" aria-label="按处理状态筛选"></div>
      <div class="fb bgl-fb">
        <div class="bgl-seg" role="tablist" aria-label="查看方式">
          <button type="button" data-bgl-view="issues" class="is-active">按问题聚合</button>
          <button type="button" data-bgl-view="reports">逐条上报</button>
        </div>
        <label class="fb-field"><span class="fb-k">应用</span><select id="bglAppFilter"><option value="">全部应用</option></select></label>
        <label class="fb-search fb-field--grow"><svg class="ui-ic" aria-hidden="true"><use href="#i-search"/></svg><input id="bglSearch" type="search" maxlength="200" placeholder="问题标题、Bugly 编号或堆栈关键词" autocomplete="off"></label>
        <button type="button" class="secondary bgl-more-btn" id="bglMoreBtn" aria-expanded="false">${ic('layers')}更多筛选<span class="bgl-more-count" id="bglMoreCount" hidden></span></button>
        <div class="fb-actions"><button type="button" class="secondary" id="bglResetBtn">重置</button></div>
      </div>
      <div class="bgl-more" id="bglMore" hidden>
        <label class="fb-field"><span class="fb-k">应用版本</span><input id="bglVersion" placeholder="例如 1.2.3" maxlength="100"></label>
        <label class="fb-field"><span class="fb-k">设备</span><input id="bglDevice" placeholder="设备机型" maxlength="100"></label>
        <div class="fb-field fb-range"><span class="fb-k">发生日期</span><input type="date" id="bglSince" aria-label="开始日期" data-range-role="start" data-range-pair="bglUntil"><span class="fb-range-sep">至</span><input type="date" id="bglUntil" aria-label="结束日期" data-range-role="end" data-range-pair="bglSince"></div>
        <label class="fb-check"><input type="checkbox" id="bglArchived">包含归档问题</label>
      </div>
      <div class="bgl-panel">
        <div class="bgl-table-wrap"><table class="bgl-table"><thead id="bglHead"></thead><tbody id="bglRows"></tbody></table></div>
        <footer class="bgl-pager">
          <span id="bglPageLabel"></span>
          <div class="bgl-pager-ctl">
            <select id="bglPageSize" aria-label="每页条数">${PAGE_SIZES.map((n) => `<option value="${n}">每页 ${n} 条</option>`).join('')}</select>
            <button type="button" class="secondary" id="bglPrev" aria-label="上一页">${ic('chevron-left')}</button>
            <button type="button" class="secondary" id="bglNext" aria-label="下一页">${ic('chevron-right')}</button>
          </div>
        </footer>
      </div>
    </div>
    <div id="bglDetail" hidden></div>`;
  bindShell();
}

function bindShell() {
  const r = root();
  let searchTimer = 0;
  r.querySelector('#bglSearch').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => reloadFirstPage(), 320);
  });
  r.querySelector('#bglSearch').addEventListener('keydown', (e) => { if (e.key === 'Enter') { clearTimeout(searchTimer); reloadFirstPage(); } });
  ['#bglAppFilter', '#bglVersion', '#bglDevice', '#bglSince', '#bglUntil', '#bglArchived'].forEach((sel) => {
    r.querySelector(sel).addEventListener('change', () => { updateMoreCount(); reloadFirstPage(); });
  });
  r.querySelector('#bglMoreBtn').addEventListener('click', (e) => {
    const more = r.querySelector('#bglMore');
    more.hidden = !more.hidden;
    e.currentTarget.setAttribute('aria-expanded', String(!more.hidden));
    e.currentTarget.classList.toggle('is-on', !more.hidden);
  });
  r.querySelector('#bglResetBtn').addEventListener('click', () => {
    ['#bglSearch', '#bglVersion', '#bglDevice', '#bglSince', '#bglUntil'].forEach((sel) => { r.querySelector(sel).value = ''; });
    r.querySelector('#bglAppFilter').value = '';
    r.querySelector('#bglArchived').checked = false;
    state.status = '';
    updateMoreCount();
    reloadFirstPage();
  });
  r.querySelector('#bglPageSize').addEventListener('change', (e) => { state.size = Number(e.target.value) || 20; reloadFirstPage(); });
  r.querySelector('#bglPrev').addEventListener('click', () => { if (state.page > 1) { state.page -= 1; loadList(); } });
  r.querySelector('#bglNext').addEventListener('click', () => { if (state.page * state.size < state.total) { state.page += 1; loadList(); } });
  r.addEventListener('click', onRootClick);
}

function updateMoreCount() {
  const r = root();
  const n = ['#bglVersion', '#bglDevice', '#bglSince', '#bglUntil'].filter((sel) => r.querySelector(sel).value.trim()).length
    + (r.querySelector('#bglArchived').checked ? 1 : 0);
  const badge = r.querySelector('#bglMoreCount');
  badge.textContent = String(n);
  badge.hidden = !n;
}

function reloadFirstPage() {
  state.page = 1;
  loadList();
}

// ── 列表 ─────────────────────────────────────────────

function filterParams() {
  const r = root();
  const p = new URLSearchParams();
  const put = (k, v) => { if (v) p.set(k, v); };
  put('application_id', r.querySelector('#bglAppFilter').value);
  put('q', r.querySelector('#bglSearch').value.trim());
  put('status', state.status);
  put('app_version', r.querySelector('#bglVersion').value.trim());
  put('device', r.querySelector('#bglDevice').value.trim());
  const since = r.querySelector('#bglSince').value;
  const until = r.querySelector('#bglUntil').value;
  if (since) p.set('since', new Date(`${since}T00:00:00`).toISOString());
  if (until) p.set('until', new Date(`${until}T23:59:59`).toISOString());
  if (r.querySelector('#bglArchived').checked) p.set('include_archived', 'true');
  p.set('page', String(state.page));
  p.set('page_size', String(state.size));
  return p;
}

function renderStatusBar() {
  const counts = state.statusCounts || {};
  const all = Object.values(counts).reduce((a, b) => a + b, 0);
  const items = [['', '全部', all, 'all'], ...Object.entries(STATUS).map(([k, m]) => [k, m.text, counts[k] || 0, m.tone])];
  $('#bglStatusBar').innerHTML = items.map(([key, text, n, tone]) => `
    <button type="button" role="tab" class="bgl-stat bgl-stat--${tone}${state.status === key ? ' is-active' : ''}" data-bgl-status="${key}" aria-selected="${state.status === key}">
      <span class="bgl-stat-label">${key ? '<i></i>' : ''}${esc(text)}</span><b>${n}</b>
    </button>`).join('');
}

function stateRow(cols, icon, title, sub, tone = '') {
  return `<tr class="bgl-state-row"><td colspan="${cols}"><div class="bgl-state${tone ? ` bgl-state--${tone}` : ''}">${ic(icon)}<b>${esc(title)}</b>${sub ? `<span>${esc(sub)}</span>` : ''}</div></td></tr>`;
}

function skeleton(cols) {
  return Array.from({ length: 6 }, () => `<tr class="bgl-skel">${Array.from({ length: cols }, () => '<td><i></i></td>').join('')}</tr>`).join('');
}

function issueHead() {
  return '<tr><th>问题</th><th class="bgl-col-status">处理状态</th><th class="bgl-col-num">上报数</th><th>涉及版本</th><th class="bgl-col-time">最近发生</th><th class="bgl-col-owner">负责人</th></tr>';
}

function reportHead() {
  return '<tr><th class="bgl-col-rid">上报编号</th><th>所属问题</th><th>版本</th><th>设备</th><th class="bgl-col-time">发生时间</th><th class="bgl-col-status">堆栈</th></tr>';
}

function issueRow(item) {
  const versions = (item.versions || []).filter(Boolean);
  const shown = versions.slice(0, 3).map((v) => `<span class="bgl-ver">${esc(v)}</span>`).join('');
  return `<tr class="bgl-row" data-open-issue="${item.id}" tabindex="0">
    <td><div class="bgl-title">${esc(item.title)}</div>
      <div class="bgl-sub"><span class="bgl-src">${sourceLabel(item)}</span><span>${esc(item.application_name || '')}</span>${item.archived ? '<span class="bgl-archived">已归档</span>' : ''}</div></td>
    <td class="bgl-col-status">${statusChip(item.status)}</td>
    <td class="bgl-col-num"><b>${Number(item.report_count) || 0}</b></td>
    <td>${shown || '<span class="bgl-muted">未采集</span>'}${versions.length > 3 ? `<span class="bgl-muted"> +${versions.length - 3}</span>` : ''}</td>
    <td class="bgl-col-time" title="${esc(fmt(item.last_occurred_at, ''))}">${item.last_occurred_at ? `<b>${esc(rel(item.last_occurred_at))}</b>` : '<span class="bgl-muted">未采集</span>'}</td>
    <td class="bgl-col-owner">${item.owner_id ? esc(userName(item.owner_id)) : '<span class="bgl-muted">未指定</span>'}</td>
  </tr>`;
}

function reportRow(r) {
  return `<tr class="bgl-row" data-open-global-report="${r.id}" data-issue-id="${r.issue_id}" tabindex="0">
    <td class="bgl-col-rid"><span class="bgl-mono">${r.source_report_id ? `#${esc(r.source_report_id)}` : '手动上报'}</span></td>
    <td><div class="bgl-title">${esc(r.title)}</div><div class="bgl-sub"><span class="bgl-src">${sourceLabel(r)}</span><span>${esc(r.application_name || '')}</span></div></td>
    <td>${r.app_version ? `<span class="bgl-ver">${esc(r.app_version)}</span>` : '<span class="bgl-muted">未采集</span>'}</td>
    <td>${esc(r.device || '未采集')}</td>
    <td class="bgl-col-time" title="${esc(fmt(r.occurred_at, ''))}">${r.occurred_at ? `<b>${esc(rel(r.occurred_at))}</b>` : '<span class="bgl-muted">未采集</span>'}</td>
    <td class="bgl-col-status"><span class="bgl-mode bgl-mode--${esc(r.stack_mode)}">${esc(STACK_MODE[r.stack_mode] || r.stack_mode || '—')}</span></td>
  </tr>`;
}

export async function loadList() {
  const seq = ++state.listSeq;
  const reportView = state.view === 'reports';
  const cols = 6;
  // 已有行时保留旧行（面板变淡）直到新数据回来：筛选框失焦触发的刷新不会把鼠标下正在点的行换掉
  if (state.renderedView !== state.view || !document.querySelector('#bglRows .bgl-row')) {
    $('#bglHead').innerHTML = reportView ? reportHead() : issueHead();
    $('#bglRows').innerHTML = skeleton(cols);
    state.renderedView = state.view;
  }
  root().querySelector('.bgl-panel').classList.add('is-loading');
  try {
    const data = await bapi(`${reportView ? '/reports' : '/issues'}?${filterParams()}`);
    if (seq !== state.listSeq) return;
    state.total = data.total;
    if (data.status_counts) state.statusCounts = data.status_counts;
    renderStatusBar();
    const items = data.items || [];
    $('#bglRows').innerHTML = items.length
      ? items.map(reportView ? reportRow : issueRow).join('')
      : stateRow(cols, 'search', reportView ? '暂无符合条件的上报' : '暂无符合条件的问题',
        state.apps.length ? '调整筛选条件，或导入 Bugly 插件采集的 JSON / CSV 文件。' : '先在「应用管理」里建档一个 Bugly 应用，再导入采集文件。');
    const pages = Math.max(1, Math.ceil(data.total / state.size));
    $('#bglPageLabel').textContent = `共 ${data.total} ${reportView ? '条上报' : '个问题'} · 第 ${state.page} / ${pages} 页`;
    $('#bglPrev').disabled = state.page <= 1;
    $('#bglNext').disabled = state.page * state.size >= data.total;
  } catch (err) {
    if (seq !== state.listSeq) return;
    $('#bglRows').innerHTML = stateRow(cols, 'alert', '加载失败', err.message || '请稍后重试', 'error');
    $('#bglPageLabel').textContent = '';
  } finally {
    if (seq === state.listSeq) root().querySelector('.bgl-panel')?.classList.remove('is-loading');
  }
}

async function refreshApps() {
  state.apps = await bapi('/applications');
  const sel = $('#bglAppFilter');
  if (sel) {
    const cur = sel.value;
    sel.innerHTML = appOptions(cur, true);
    sel.value = state.apps.some((a) => String(a.id) === cur) ? cur : '';
  }
  const hasApp = state.apps.length > 0;
  document.getElementById('buglyNewIssueBtn')?.toggleAttribute('disabled', !hasApp);
  document.getElementById('buglyImportBtn')?.toggleAttribute('disabled', !hasApp);
}

// ── 详情 ─────────────────────────────────────────────

function showList() {
  $('#bglList').hidden = false;
  $('#bglDetail').hidden = true;
  document.getElementById('tab-bugly')?.classList.remove('is-bgl-detail');
}

async function backToList() {
  ++state.detailSeq; ++state.reportSeq; ++state.reportListSeq;
  state.issue = null;
  showList();
  await loadList();
}

export async function openIssue(id, { reportId = 0 } = {}) {
  const seq = ++state.detailSeq;
  const detail = $('#bglDetail');
  $('#bglList').hidden = true;
  detail.hidden = false;
  document.getElementById('tab-bugly')?.classList.add('is-bgl-detail');
  if (!state.issue || state.issue.id !== id) detail.innerHTML = `<div class="bgl-detail-loading">${ic('refresh')}正在加载问题详情…</div>`;
  let data;
  try {
    data = await bapi(`/issues/${id}`);
  } catch (err) {
    if (seq !== state.detailSeq) return;
    detail.innerHTML = `<div class="bgl-back-row"><button type="button" class="secondary" data-bgl-act="back">${ic('arrow-left')}返回问题列表</button></div>
      <div class="bgl-panel">${`<table class="bgl-table"><tbody>${stateRow(1, 'alert', '问题加载失败', err.message || '', 'error')}</tbody></table>`}</div>`;
    return;
  }
  if (seq !== state.detailSeq) return;
  state.issue = data;
  state.reportPage = 1;
  state.reportQuery = '';
  ++state.reportSeq;
  renderDetail();
  await loadReports(reportId);
}

function renderDetail() {
  const d = state.issue;
  const owner = d.owner_name || (d.owner_id ? userName(d.owner_id) : '');
  $('#bglDetail').innerHTML = `
    <div class="bgl-back-row">
      <button type="button" class="secondary" data-bgl-act="back">${ic('arrow-left')}返回问题列表</button>
    </div>
    <header class="bgl-dhead">
      <div class="bgl-dhead-main">
        <div class="bgl-dhead-meta"><span class="bgl-src">${sourceLabel(d)}</span><span>${esc(d.application_name || '')}</span>${d.archived ? '<span class="bgl-archived">已归档</span>' : ''}</div>
        <h3 class="bgl-dtitle">${esc(d.title)}</h3>
      </div>
      <div class="bgl-dhead-actions">
        <label class="bgl-quick-status"><span>处理状态</span><select id="bglQuickStatus" aria-label="处理状态">${Object.entries(STATUS).map(([k, m]) => `<option value="${k}" ${d.status === k ? 'selected' : ''}>${m.text}</option>`).join('')}</select></label>
        <button type="button" class="secondary" data-bgl-act="new-report">${ic('plus')}新增上报</button>
        <button type="button" data-bgl-act="edit">${ic('edit')}编辑问题</button>
      </div>
    </header>
    <dl class="bgl-facts">
      <div><dt>处理状态</dt><dd>${statusChip(d.status)}</dd></div>
      <div><dt>异常类型</dt><dd>${esc(d.exception_type || '待确认')}</dd></div>
      <div><dt>已导入上报</dt><dd><b class="bgl-num">${Number(d.report_count) || 0}</b> 条</dd></div>
      <div><dt>负责人</dt><dd>${owner ? esc(owner) : '<span class="bgl-muted">未指定</span>'}</dd></div>
      <div><dt>最近发生</dt><dd>${esc(fmt(d.last_occurred_at))}</dd></div>
      <div><dt>涉及版本</dt><dd class="bgl-vers">${(d.versions || []).filter(Boolean).map((v) => `<span class="bgl-ver">${esc(v)}</span>`).join('') || '<span class="bgl-muted">未采集</span>'}</dd></div>
    </dl>
    <section class="bgl-analysis">
      <div class="bgl-sec-head"><h4>${ic('book')}分析结论</h4><button type="button" class="bgl-link" data-bgl-act="edit">编辑</button></div>
      ${d.analysis ? `<p class="bgl-analysis-text">${esc(d.analysis)}</p>` : '<p class="bgl-muted bgl-analysis-empty">还没有分析结论。定位到原因后，在「编辑问题」里补充，方便团队复用。</p>'}
    </section>
    <div class="bgl-reports">
      <aside class="bgl-rlist">
        <div class="bgl-rlist-head"><h4>上报记录<span class="bgl-count" id="bglReportCount">0</span></h4></div>
        <form class="bgl-rsearch" id="bglReportSearch"><label class="bgl-search"><svg class="ui-ic" aria-hidden="true"><use href="#i-search"/></svg><input id="bglReportQuery" type="search" placeholder="版本 / 设备 / 上报编号" autocomplete="off"></label></form>
        <div class="bgl-rlist-items" id="bglReportList"></div>
        <div class="bgl-rlist-foot"><span id="bglReportPageLabel"></span><div>
          <button type="button" class="secondary" data-bgl-act="report-prev" aria-label="上一页">${ic('chevron-left')}</button>
          <button type="button" class="secondary" data-bgl-act="report-next" aria-label="下一页">${ic('chevron-right')}</button></div></div>
      </aside>
      <article class="bgl-rbody" id="bglReportBody"><div class="bgl-empty">${ic('file')}选择左侧的一条上报查看堆栈与现场</div></article>
    </div>`;
  const form = $('#bglReportSearch');
  form.addEventListener('submit', (e) => { e.preventDefault(); state.reportQuery = $('#bglReportQuery').value.trim(); state.reportPage = 1; loadReports(); });
  let t = 0;
  $('#bglReportQuery').addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { state.reportQuery = $('#bglReportQuery').value.trim(); state.reportPage = 1; loadReports(); }, 320); });
  $('#bglQuickStatus').addEventListener('change', (e) => quickStatus(e.target.value));
}

async function quickStatus(status) {
  const d = state.issue;
  if (!d || d.status === status) return;
  try {
    state.issue = await bapi(`/issues/${d.id}`, { method: 'PATCH', body: { expected_version: d.version, status } });
    toast(`处理状态已改为「${STATUS[status]?.text || status}」`);
    const keep = state.activeReportId;
    renderDetail();
    await loadReports(keep);
  } catch (err) {
    toast(err.message || '状态更新失败', 'error');
    $('#bglQuickStatus').value = d.status;
  }
}

async function loadReports(preferId = 0) {
  const seq = ++state.reportListSeq;
  const id = state.issue.id;
  const list = $('#bglReportList');
  list.innerHTML = '<div class="bgl-rskel"><i></i><i></i><i></i></div>';
  let data;
  try {
    data = await bapi(`/issues/${id}/reports?${new URLSearchParams({ page: state.reportPage, page_size: 20, q: state.reportQuery })}`);
  } catch (err) {
    if (seq === state.reportListSeq) list.innerHTML = `<div class="bgl-empty bgl-empty--error">${esc(err.message || '加载失败')}</div>`;
    return;
  }
  if (state.issue?.id !== id || seq !== state.reportListSeq) return;
  state.reports = data.items || [];
  state.reportTotal = data.total || 0;
  $('#bglReportCount').textContent = String(state.reportTotal);
  list.innerHTML = state.reports.length ? state.reports.map((r) => `
    <button type="button" class="bgl-ritem" data-open-report="${r.id}">
      <span class="bgl-ritem-top"><b class="bgl-mono">${r.source_report_id ? `#${esc(r.source_report_id)}` : '手动上报'}</b>${r.stack_mode === 'fallback' ? '<span class="bgl-mode bgl-mode--fallback">兜底</span>' : ''}</span>
      <span class="bgl-ritem-meta">${esc(r.app_version || '版本未采集')} · ${esc(r.device || '设备未采集')}</span>
      <span class="bgl-ritem-time">${esc(fmt(r.occurred_at))}</span>
    </button>`).join('') : `<div class="bgl-empty">${state.reportQuery ? '没有匹配的上报' : '暂无上报记录'}</div>`;
  const pages = Math.max(1, Math.ceil(state.reportTotal / 20));
  $('#bglReportPageLabel').textContent = `第 ${state.reportPage} / ${pages} 页`;
  $('[data-bgl-act="report-prev"]').disabled = state.reportPage <= 1;
  $('[data-bgl-act="report-next"]').disabled = state.reportPage * 20 >= state.reportTotal;
  const target = state.reports.find((r) => r.id === preferId) || state.reports[0];
  if (target) await openReport(target.id);
  else $('#bglReportBody').innerHTML = `<div class="bgl-empty">${ic('file')}${state.reportQuery ? '没有匹配的上报' : '这个问题还没有上报记录，可点「新增上报」手动补充'}</div>`;
}

function renderStack() {
  const q = $('#bglStackQuery')?.value || '';
  const lines = (state.stack || '未采集到堆栈').split('\n');
  let hits = 0;
  $('#bglStack').innerHTML = lines.map((line) => {
    if (!q) return `<span class="bgl-sl">${esc(line)}</span>`;
    const parts = line.split(q);
    hits += parts.length - 1;
    return `<span class="bgl-sl${parts.length > 1 ? ' is-hit' : ''}">${parts.map(esc).join(`<mark>${esc(q)}</mark>`)}</span>`;
  }).join('\n');
  const label = $('#bglStackHits');
  if (label) label.textContent = q ? (hits ? `${hits} 处匹配` : '无匹配') : `${lines.length} 行`;
  if (q && hits) $('#bglStack .is-hit')?.scrollIntoView({ block: 'nearest' });
}

async function openReport(id) {
  const seq = ++state.reportSeq;
  state.activeReportId = id;
  document.querySelectorAll('#bglReportList [data-open-report]').forEach((el) => el.classList.toggle('is-active', Number(el.dataset.openReport) === id));
  let row;
  try {
    row = await bapi(`/reports/${id}`);
  } catch (err) {
    if (seq === state.reportSeq) $('#bglReportBody').innerHTML = `<div class="bgl-empty bgl-empty--error">${esc(err.message || '上报加载失败')}</div>`;
    return;
  }
  if (seq !== state.reportSeq) return;
  const fields = [
    ['应用版本', row.app_version], ['设备机型', row.device], ['系统版本', row.os_version],
    ['发生时间', row.occurred_at ? fmt(row.occurred_at) : row.occurred_at_raw], ['采集时间', fmt(row.collected_at)], ['堆栈帧数', row.frame_count],
  ];
  const payload = row.payload || {};
  const scene = JSON.stringify({ 列表: payload.listFields || {}, 详情: payload.detailFields || {} }, null, 2);
  $('#bglReportBody').innerHTML = `
    <div class="bgl-rb-head">
      <h4>${row.source_report_id ? `上报 <span class="bgl-mono">#${esc(row.source_report_id)}</span>` : '手动上报'}</h4>
      <span class="bgl-mode bgl-mode--${esc(row.stack_mode)}">${esc(STACK_MODE[row.stack_mode] || row.stack_mode || '—')}</span>
    </div>
    <dl class="bgl-rfields">${fields.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v || v === 0 ? esc(v) : '<span class="bgl-muted">未采集</span>'}</dd></div>`).join('')}</dl>
    <div class="bgl-stack-bar">
      <label class="bgl-search bgl-stack-search"><svg class="ui-ic" aria-hidden="true"><use href="#i-search"/></svg><input id="bglStackQuery" type="search" placeholder="在堆栈中定位关键词" autocomplete="off"></label>
      <span class="bgl-stack-hits" id="bglStackHits"></span>
      <div class="bgl-stack-tools">
        <button type="button" class="bgl-tool" data-bgl-act="stack-wrap" aria-pressed="false">自动换行</button>
        <button type="button" class="bgl-tool" data-bgl-act="stack-full" aria-pressed="false">展开全部</button>
        <button type="button" class="bgl-tool" data-bgl-act="stack-copy">${ic('copy')}复制堆栈</button>
      </div>
    </div>
    <pre class="bgl-stack" id="bglStack"></pre>
    <details class="bgl-more-info"><summary>现场字段</summary><pre>${esc(scene)}</pre></details>
    <details class="bgl-more-info"><summary>原始文本与采集诊断</summary><pre>${esc(payload.rawDetailText || '未采集')}</pre><pre>${esc(JSON.stringify(payload.captureDiagnostics || {}, null, 2))}</pre></details>
    <details class="bgl-more-info" data-bgl-revisions="${row.id}"><summary>修订记录（最近 50 次）</summary><div class="bgl-revisions">加载中…</div></details>`;
  state.stack = row.stack_text || '未采集到堆栈';
  renderStack();
  $('#bglStackQuery').addEventListener('input', renderStack);
  const rev = $('[data-bgl-revisions]');
  rev.addEventListener('toggle', async () => {
    if (!rev.open || rev.dataset.loaded) return;
    try {
      const rows = await bapi(`/reports/${id}/revisions`);
      rev.querySelector('.bgl-revisions').innerHTML = rows.length
        ? rows.map((r) => `<details><summary>第 ${r.revision} 版 · ${esc(fmt(r.created_at))}</summary><pre>${esc(JSON.stringify(r.payload, null, 2))}</pre></details>`).join('')
        : '<span class="bgl-muted">暂无修订</span>';
      rev.dataset.loaded = '1';
    } catch (err) {
      rev.querySelector('.bgl-revisions').textContent = err.message || '加载失败';
    }
  });
}

async function copyStack() {
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(state.stack);
    else {
      const ta = document.createElement('textarea');
      ta.value = state.stack; document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove();
    }
    toast('堆栈已复制');
  } catch (_) {
    toast('复制失败，请手动选择堆栈文本', 'error');
  }
}

// ── 事件 ─────────────────────────────────────────────

async function onRootClick(event) {
  const t = event.target;
  const view = t.closest('[data-bgl-view]');
  if (view) {
    state.view = view.dataset.bglView;
    root().querySelectorAll('[data-bgl-view]').forEach((b) => b.classList.toggle('is-active', b === view));
    reloadFirstPage();
    return;
  }
  const st = t.closest('[data-bgl-status]');
  if (st) {
    state.status = st.dataset.bglStatus;
    reloadFirstPage();
    return;
  }
  const gr = t.closest('[data-open-global-report]');
  if (gr) { await openIssue(Number(gr.dataset.issueId), { reportId: Number(gr.dataset.openGlobalReport) }); return; }
  const iss = t.closest('[data-open-issue]');
  if (iss) { await openIssue(Number(iss.dataset.openIssue)); return; }
  const rep = t.closest('[data-open-report]');
  if (rep) { await openReport(Number(rep.dataset.openReport)); return; }
  const act = t.closest('[data-bgl-act]')?.dataset.bglAct;
  if (!act) return;
  if (act === 'back') await backToList();
  else if (act === 'edit') await openEditIssue();
  else if (act === 'new-report') openNewReport();
  else if (act === 'report-prev' && state.reportPage > 1) { state.reportPage -= 1; await loadReports(); }
  else if (act === 'report-next' && state.reportPage * 20 < state.reportTotal) { state.reportPage += 1; await loadReports(); }
  else if (act === 'stack-wrap' || act === 'stack-full') {
    const btn = t.closest('[data-bgl-act]');
    const on = $('#bglStack').classList.toggle(act === 'stack-wrap' ? 'is-wrap' : 'is-full');
    btn.setAttribute('aria-pressed', String(on));
    if (act === 'stack-full') btn.textContent = on ? '收起' : '展开全部';
  } else if (act === 'stack-copy') await copyStack();
}

function onRowKey(event) {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const row = event.target.closest?.('#tab-bugly tr.bgl-row');
  if (!row || event.target !== row) return;
  event.preventDefault();
  row.click();
}

// ── 抽屉表单 ─────────────────────────────────────────

function drawerHead(eyebrow, title, sub = '') {
  return `<div class="sd-eyebrow">${esc(eyebrow)}</div><h3 class="sd-title">${esc(title)}</h3>${sub ? `<div class="sd-sub">${esc(sub)}</div>` : ''}`;
}

function busy(btn, on, text) {
  if (!btn) return;
  if (on) { btn.dataset.label = btn.innerHTML; btn.disabled = true; btn.innerHTML = `${ic('refresh', 'bgl-spin')}${esc(text)}`; }
  else { btn.disabled = false; if (btn.dataset.label) btn.innerHTML = btn.dataset.label; }
}

function readForm(scope) {
  const out = {};
  scope.querySelectorAll('[name]').forEach((el) => {
    out[el.name] = el.type === 'checkbox' ? el.checked : el.value.trim();
  });
  return out;
}

function requireFields(scope, names) {
  for (const n of names) {
    const el = scope.querySelector(`[name="${n}"]`);
    if (el && !el.value.trim()) {
      el.focus();
      el.classList.add('is-invalid');
      el.addEventListener('input', () => el.classList.remove('is-invalid'), { once: true });
      return false;
    }
  }
  return true;
}

export function openNewIssue() {
  if (!state.apps.length) { toast('请先在「应用管理」里建档一个 Bugly 应用', 'error'); return; }
  const d = openDrawer({
    key: 'bugly-issue-new',
    size: 'md',
    head: drawerHead('Bugly 问题中心', '新增问题', '可以先记录问题和分析线索，再补充上报；没有 Bugly 编号时留空。'),
    body: `<div class="sd-form" id="bglNewIssueForm">
      <label class="sd-field"><span class="sd-label">所属应用<em>*</em></span><select name="application_id">${appOptions(defaultAppId())}</select></label>
      <label class="sd-field"><span class="sd-label">问题标题<em>*</em></span><input name="title" maxlength="500" placeholder="例如：切换页面时空指针崩溃"></label>
      <div class="sd-grid2">
        <label class="sd-field"><span class="sd-label">Bugly 问题编号<small>选填</small></span><input name="source_issue_id" maxlength="100" placeholder="例如 12345"></label>
        <label class="sd-field"><span class="sd-label">异常类型</span><input name="exception_type" maxlength="100" value="待确认"></label>
      </div>
    </div>`,
    foot: `<button type="button" class="secondary" data-sd-close>取消</button><button type="button" id="bglNewIssueSubmit">${ic('check')}创建问题</button>`,
  });
  const form = d.body.querySelector('#bglNewIssueForm');
  setTimeout(() => form.querySelector('[name=title]').focus({ preventScroll: true }), 60);
  d.foot.querySelector('#bglNewIssueSubmit').onclick = async (e) => {
    if (!requireFields(form, ['application_id', 'title'])) return;
    const v = readForm(form);
    busy(e.currentTarget, true, '创建中…');
    try {
      const row = await bapi('/issues', { method: 'POST', body: { application_id: Number(v.application_id), title: v.title, source_issue_id: v.source_issue_id || null, exception_type: v.exception_type || '待确认' } });
      d.close();
      toast('问题已创建');
      await openIssue(row.id);
    } catch (err) {
      toast(err.message || '创建失败', 'error');
      busy(e.currentTarget, false);
    }
  };
}

async function openEditIssue() {
  const data = state.issue;
  if (!data) return;
  const people = (window.users || []).filter((u) => u.account_type !== 'external');
  const d = openDrawer({
    key: 'bugly-issue-edit',
    size: 'md',
    head: drawerHead(data.source_issue_id ? `Bugly #${data.source_issue_id}` : '手动问题', '编辑问题'),
    body: `<div class="sd-form" id="bglEditForm">
      <label class="sd-field"><span class="sd-label">问题标题<em>*</em></span><input name="title" maxlength="500" value="${esc(data.title)}"></label>
      <div class="sd-grid2">
        <label class="sd-field"><span class="sd-label">处理状态</span><select name="status">${Object.entries(STATUS).map(([k, m]) => `<option value="${k}" ${data.status === k ? 'selected' : ''}>${m.text}</option>`).join('')}</select></label>
        <label class="sd-field"><span class="sd-label">负责人</span><select name="owner_id"><option value="">暂不指定</option>${people.map((u) => `<option value="${u.id}" ${Number(data.owner_id) === Number(u.id) ? 'selected' : ''}>${esc(u.display_name || u.username)}</option>`).join('')}</select></label>
        <label class="sd-field"><span class="sd-label">Bugly 问题编号${data.source_issue_id ? '<small>已关联，不能更换</small>' : '<small>尚无编号时可后续补充</small>'}</span><input name="source_issue_id" maxlength="100" value="${esc(data.source_issue_id || '')}" ${data.source_issue_id ? 'readonly' : ''}></label>
        <label class="sd-field"><span class="sd-label">异常类型</span><input name="exception_type" maxlength="100" value="${esc(data.exception_type || '')}"></label>
      </div>
      <label class="sd-field"><span class="sd-label">分析结论<small>原因、影响范围、处理方案</small></span><textarea name="analysis" rows="8" maxlength="50000">${esc(data.analysis || '')}</textarea></label>
      <label class="fb-check bgl-check"><input type="checkbox" name="archived" ${data.archived ? 'checked' : ''}>归档问题（默认不在列表里显示）</label>
    </div>`,
    foot: `<button type="button" class="secondary" data-sd-close>取消</button><button type="button" id="bglEditSubmit">${ic('check')}保存修改</button>`,
  });
  const form = d.body.querySelector('#bglEditForm');
  d.foot.querySelector('#bglEditSubmit').onclick = async (e) => {
    if (!requireFields(form, ['title'])) return;
    const v = readForm(form);
    busy(e.currentTarget, true, '保存中…');
    try {
      state.issue = await bapi(`/issues/${data.id}`, {
        method: 'PATCH',
        body: {
          expected_version: data.version, title: v.title, status: v.status, exception_type: v.exception_type,
          owner_id: v.owner_id ? Number(v.owner_id) : null, archived: !!v.archived, analysis: v.analysis,
          source_issue_id: v.source_issue_id || null,
        },
      });
      d.close();
      toast('修改已保存');
      const keep = state.activeReportId;
      renderDetail();
      await loadReports(keep);
    } catch (err) {
      toast(err.message || '保存失败', 'error');
      busy(e.currentTarget, false);
    }
  };
}

function openNewReport() {
  const issue = state.issue;
  if (!issue) return;
  const d = openDrawer({
    key: 'bugly-report-new',
    size: 'lg',
    head: drawerHead(issue.title, '新增上报记录', '手动补充一次崩溃现场；导入的上报请用「导入文件」。'),
    body: `<div class="sd-form" id="bglReportForm">
      <div class="sd-grid3 bgl-grid3-flat">
        <label class="sd-field"><span class="sd-label">上报编号<small>选填</small></span><input name="reportId" maxlength="100"></label>
        <label class="sd-field"><span class="sd-label">应用版本</span><input name="appVersion" maxlength="255" placeholder="例如 1.2.3"></label>
        <label class="sd-field"><span class="sd-label">发生时间</span><input name="occurredAt" type="datetime-local"></label>
        <label class="sd-field"><span class="sd-label">设备机型</span><input name="device" maxlength="255"></label>
        <label class="sd-field"><span class="sd-label">系统版本</span><input name="osVersion" maxlength="255"></label>
      </div>
      <label class="sd-field"><span class="sd-label">出错堆栈</span><textarea name="stack" rows="12" maxlength="600000" spellcheck="false" class="bgl-code"></textarea></label>
      <label class="sd-field"><span class="sd-label">现场补充文本<small>选填</small></span><textarea name="rawDetailText" rows="4" maxlength="600000"></textarea></label>
    </div>`,
    foot: `<button type="button" class="secondary" data-sd-close>取消</button><button type="button" id="bglReportSubmit">${ic('check')}保存上报</button>`,
  });
  const form = d.body.querySelector('#bglReportForm');
  d.foot.querySelector('#bglReportSubmit').onclick = async (e) => {
    const v = readForm(form);
    busy(e.currentTarget, true, '保存中…');
    try {
      await bapi(`/issues/${issue.id}/reports`, {
        method: 'POST',
        body: {
          reportId: v.reportId || null,
          collectedAt: new Date().toISOString(),
          detailFields: { 应用版本: v.appVersion, 发生时间: v.occurredAt ? new Date(v.occurredAt).toISOString() : '', 设备机型: v.device, 系统版本: v.osVersion },
          stack: { text: v.stack, mode: 'manual', frameCount: 0 },
          rawDetailText: v.rawDetailText,
        },
      });
      d.close();
      toast('上报已保存');
      await openIssue(issue.id);
    } catch (err) {
      toast(err.message || '保存失败', 'error');
      busy(e.currentTarget, false);
    }
  };
}

// 导入：选应用 / 归属问题 / 文件 → 逐个文件校验预览 → 逐批确认
export function openImport() {
  if (!state.apps.length) { toast('请先在「应用管理」里建档一个 Bugly 应用', 'error'); return; }
  const d = openDrawer({
    key: 'bugly-import',
    size: 'lg',
    head: drawerHead('Bugly 问题中心', '导入采集文件', '推荐使用插件导出的 JSON，CSV 也可导入；每个文件不超过 20 MB、2,000 条上报，单次最多 10 个文件。'),
    body: `<div class="sd-form" id="bglImportForm">
      <div class="sd-grid2">
        <label class="sd-field"><span class="sd-label">所属应用<em>*</em></span><select name="application_id">${appOptions(defaultAppId())}</select></label>
        <label class="sd-field"><span class="sd-label">缺少问题编号时归到</span><select name="issue_id"><option value="">按文件里的问题编号识别</option></select></label>
      </div>
      <div class="sd-field"><span class="sd-label">采集文件<em>*</em></span>
        <label class="sd-drop" id="bglImportDrop"><input type="file" name="files" accept=".json,.csv" multiple hidden>${ic('upload', 'sd-drop-ic')}<span><b>点击选择文件</b>，或拖到这里</span></label>
        <div class="bgl-files" id="bglImportFiles"></div>
      </div>
      <div id="bglImportPreview" class="bgl-import-preview"></div>
    </div>`,
    foot: `<button type="button" class="secondary" data-sd-close>关闭</button><button type="button" id="bglImportCheck">${ic('search')}校验并预览</button>`,
  });
  const form = d.body.querySelector('#bglImportForm');
  const fileInput = form.querySelector('[name=files]');
  let files = [];
  const showFiles = () => {
    form.querySelector('#bglImportFiles').innerHTML = files.map((f, i) => `<span class="bgl-file">${ic('file')}<b>${esc(f.name)}</b><small>${(f.size / 1024 / 1024).toFixed(2)} MB</small><button type="button" data-rm-file="${i}" aria-label="移除">×</button></span>`).join('');
  };
  const addFiles = (list) => { files = [...files, ...Array.from(list || [])].slice(0, 10); showFiles(); };
  fileInput.addEventListener('change', () => { addFiles(fileInput.files); fileInput.value = ''; });
  const drop = form.querySelector('#bglImportDrop');
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('is-over'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('is-over'); }));
  drop.addEventListener('drop', (e) => addFiles(e.dataTransfer?.files));
  form.querySelector('#bglImportFiles').addEventListener('click', (e) => {
    const rm = e.target.closest('[data-rm-file]');
    if (rm) { files.splice(Number(rm.dataset.rmFile), 1); showFiles(); }
  });
  const appSel = form.querySelector('[name=application_id]');
  const issueSel = form.querySelector('[name=issue_id]');
  const reloadIssues = async () => {
    if (!appSel.value) return;
    try {
      const rows = await bapi(`/issues?application_id=${appSel.value}&page_size=100`);
      issueSel.innerHTML = '<option value="">按文件里的问题编号识别</option>' + rows.items.map((r) => `<option value="${r.id}">${r.source_issue_id ? `#${esc(r.source_issue_id)}` : '手动'} · ${esc(r.title)}</option>`).join('');
    } catch (_) { /* 归属问题下拉加载失败不影响按编号导入 */ }
  };
  appSel.addEventListener('change', reloadIssues);
  reloadIssues();

  const preview = form.querySelector('#bglImportPreview');
  d.foot.querySelector('#bglImportCheck').onclick = async (e) => {
    if (!files.length) { toast('请先选择要导入的文件', 'error'); return; }
    if (files.some((f) => f.size > 20 * 1024 * 1024)) { toast('单个文件不能超过 20 MB', 'error'); return; }
    busy(e.currentTarget, true, '校验中…');
    const batches = [];
    const errors = [];
    for (const file of files) {
      const fd = new FormData();
      fd.set('application_id', appSel.value);
      if (issueSel.value) fd.set('issue_id', issueSel.value);
      fd.set('file', file);
      try { batches.push(await bapi('/imports/preview', { method: 'POST', body: fd })); } catch (err) { errors.push(`${file.name}：${err.message}`); }
    }
    busy(e.currentTarget, false);
    preview.innerHTML = errors.map((m) => `<div class="bgl-import-err">${ic('alert')}${esc(m)}</div>`).join('') + batches.map(batchCard).join('');
    preview.scrollIntoView({ block: 'start', behavior: 'smooth' });
  };
  preview.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-confirm-import]');
    if (!btn) return;
    busy(btn, true, '导入中…');
    try {
      const batch = await bapi(`/imports/${btn.dataset.confirmImport}/confirm`, { method: 'POST' });
      const r = batch.result;
      btn.closest('[data-batch]').querySelector('[data-result]').innerHTML = `${ic('check')}已导入：新增 ${r.created} · 更新 ${r.updated} · 跳过 ${r.skipped} · 无效 ${r.failed}`;
      btn.remove();
      toast('导入完成');
      await refreshApps();
      await loadList();
    } catch (err) {
      toast(err.message || '导入失败', 'error');
      busy(btn, false);
    }
  });
}

function batchCard(batch) {
  const counts = { created: 0, updated: 0, skipped: 0, failed: 0 };
  batch.items.forEach((i) => { counts[i.error ? 'failed' : i.action] += 1; });
  const warn = (batch.diagnostics?.warnings || []).join('；');
  const failures = batch.diagnostics?.failures?.length || 0;
  const allBad = counts.failed === batch.items.length;
  return `<section class="bgl-batch" data-batch="${esc(batch.id)}">
    <header><b>${ic('file')}${esc(batch.filename)}</b><span class="bgl-batch-counts">${Object.entries(counts).map(([k, v]) => `<span class="bgl-bc bgl-bc--${k}">${ACTION_TEXT[k]} <b>${v}</b></span>`).join('')}</span></header>
    ${warn || failures ? `<p class="bgl-batch-note">${esc(warn)}${failures ? `${warn ? ' · ' : ''}采集时失败 ${failures} 条` : ''}</p>` : ''}
    <details><summary>逐条校验结果（${batch.items.length} 条）</summary>
      <div class="bgl-mini-wrap"><table class="bgl-mini"><thead><tr><th>行</th><th>问题</th><th>结果</th><th>提示</th></tr></thead><tbody>
      ${batch.items.map((i) => `<tr><td>${i.row}</td><td>${esc(i.title || '未识别')}</td><td><span class="bgl-bc bgl-bc--${i.error ? 'failed' : i.action}">${esc(i.error ? '无效' : ACTION_TEXT[i.action])}</span></td><td>${esc(i.error || (i.warnings || []).join('；'))}</td></tr>`).join('')}
      </tbody></table></div></details>
    <footer><span class="bgl-batch-result" data-result>${allBad ? '没有可导入的有效记录' : '确认后写入有效记录，无效行会被跳过'}</span>
      ${allBad ? '' : `<button type="button" data-confirm-import="${esc(batch.id)}">${ic('check')}确认导入有效记录</button>`}</footer>
  </section>`;
}

export async function openHistory() {
  const d = openDrawer({
    key: 'bugly-history',
    size: 'lg',
    head: drawerHead('Bugly 问题中心', '最近导入记录', '最近 50 次校验与导入，预览 30 分钟内未确认会过期。'),
    body: '<div class="bgl-empty">正在加载…</div>',
  });
  try {
    const rows = await bapi('/imports');
    const statusText = (r) => (r.status === 'confirmed' ? ['已导入', 'green'] : new Date(r.expires_at) < new Date() ? ['预览已过期', 'gray'] : ['待确认', 'amber']);
    d.body.innerHTML = rows.length ? `<div class="bgl-history">${rows.map((r) => {
      const [text, tone] = statusText(r);
      const res = r.status === 'confirmed' ? `新增 ${r.result.created} · 更新 ${r.result.updated} · 跳过 ${r.result.skipped} · 无效 ${r.result.failed}` : '尚未写入上报';
      return `<article class="bgl-hrow">
        <div class="bgl-hrow-main"><b>${ic('file')}${esc(r.filename)}</b><span>${esc(r.actor_name || '—')} · ${esc(fmt(r.created_at))}</span></div>
        <span class="bgl-status bgl-tone--${tone}">${text}</span>
        <div class="bgl-hrow-res">${esc(res)}</div>
        <details><summary>校验与采集结果</summary><pre>${esc(JSON.stringify({ 采集信息: r.diagnostics, 逐条结果: r.result?.items || r.items }, null, 2))}</pre></details>
      </article>`;
    }).join('')}</div>` : `<div class="bgl-empty">${ic('file')}暂无导入记录</div>`;
  } catch (err) {
    d.body.innerHTML = `<div class="bgl-empty bgl-empty--error">${esc(err.message || '加载失败')}</div>`;
  }
}

export async function openApplications() {
  const render = () => {
    const d = openDrawer({
      key: 'bugly-apps',
      size: 'md',
      keepScroll: true,
      head: drawerHead('Bugly 问题中心', '应用管理', '每个 Bugly 应用是一个独立数据空间：来源站点和包名用于导入校验，编号相同的不同应用不会合并。'),
      body: `
        <section class="sd-sec"><h4 class="sd-sec-title">${ic('layers')}已建档应用<span class="sd-count">${state.apps.length}</span></h4>
          ${state.apps.length ? `<div class="bgl-apps">${state.apps.map((a) => `<div class="bgl-app">
            <span class="bgl-app-badge">${esc((a.platform || 'O').slice(0, 1))}</span>
            <div class="bgl-app-main"><b>${esc(a.name)}</b><span>${esc(a.platform)} · ${esc(a.package_name || '包名未配置')} · ${esc(a.source_host)}</span></div>
            <div class="bgl-app-side"><b>${Number(a.issue_count) || 0}</b><span>个问题</span></div></div>`).join('')}</div>` : '<div class="sd-empty">尚未建档应用</div>'}
        </section>
        <section class="sd-sec"><h4 class="sd-sec-title">${ic('plus')}新增应用</h4>
          <div class="sd-form" id="bglAppForm">
            <label class="sd-field"><span class="sd-label">应用名称<em>*</em></span><input name="name" maxlength="120" placeholder="例如 Android 正式版"></label>
            <div class="sd-grid2">
              <label class="sd-field"><span class="sd-label">系统平台</span><select name="platform">${['Android', 'iOS', 'Other'].map((p) => `<option value="${p}">${p}</option>`).join('')}</select></label>
              <label class="sd-field"><span class="sd-label">Bugly App ID<small>选填</small></span><input name="bugly_app_id" maxlength="100"></label>
              <label class="sd-field"><span class="sd-label">应用包名<small>选填</small></span><input name="package_name" maxlength="255"></label>
              <label class="sd-field"><span class="sd-label">来源站点<em>*</em></span><input name="source_host" maxlength="255" value="bugly.qq.com"></label>
              <label class="sd-field"><span class="sd-label">来源时区<em>*</em></span><input name="source_timezone" value="Asia/Shanghai"></label>
            </div>
          </div>
        </section>`,
      foot: `<button type="button" class="secondary" data-sd-close>关闭</button><button type="button" id="bglAppSubmit">${ic('plus')}新增应用</button>`,
    });
    const form = d.body.querySelector('#bglAppForm');
    d.foot.querySelector('#bglAppSubmit').onclick = async (e) => {
      if (!requireFields(form, ['name', 'source_host', 'source_timezone'])) return;
      const v = readForm(form);
      busy(e.currentTarget, true, '保存中…');
      try {
        await bapi('/applications', { method: 'POST', body: { ...v, bugly_app_id: v.bugly_app_id || null, package_name: v.package_name || null } });
        toast('应用已建档');
        await refreshApps();
        render();
        if (!$('#bglDetail') || $('#bglDetail').hidden) loadList();
      } catch (err) {
        toast(err.message || '保存失败', 'error');
        busy(e.currentTarget, false);
      }
    };
  };
  try { await refreshApps(); } catch (_) { /* 用已有列表渲染 */ }
  render();
}

// ── 入口 ─────────────────────────────────────────────

export async function activate() {
  if (!state.inited) {
    state.inited = true;
    renderShell();
    document.addEventListener('keydown', onRowKey);
    try {
      await refreshApps();
    } catch (err) {
      toast(err.message || '加载 Bugly 应用失败', 'error');
    }
    const params = new URLSearchParams(location.search);
    const issueId = Number(params.get('bugly_issue'));
    if (issueId) {
      params.delete('bugly_issue');
      history.replaceState(history.state, '', `${location.pathname}${params.toString() ? `?${params}` : ''}${location.hash}`);
      await openIssue(issueId);
      return;
    }
    await loadList();
    return;
  }
  if (state.issue) return;   // 停留在详情时切回页签不打断
  await loadList();
}

export async function reload() {
  await refreshApps();
  if (state.issue) await openIssue(state.issue.id, { reportId: state.activeReportId });
  else await loadList();
}

window.OmniQABuglyTab = { activate, reload, loadList, openIssue, openNewIssue, openImport, openHistory, openApplications, closeDrawer };
