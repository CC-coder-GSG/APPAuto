import { api } from '../api.js';

/**
 * 软件功能树状图谱（Feature Tree）
 *
 * 两种模式：
 *  - design：从顶部"功能图谱"进入。纯编辑：建/改/删分支、富文本功能备注。无测试标记。
 *  - test  ：从需求工作台"全量测试树状图"进入（大版本已开最终测试或查看全部待测）。
 *            在 design 能力之上，额外可给分支打"我已测"标记 + 富文本说明；每个测试人
 *            用按 user_id 确定性配色的彩色圆点区分。
 *
 * 布局用 ECharts 径向树（中心向外发散、自动排版、整页、可缩放平移）。
 */

// 与后端 app/services/feature_tree_service.py:MARK_PALETTE 完全一致，保证配色稳定。
const MARK_PALETTE = [
  '#5b8cff', '#8b5cf6', '#d946a0', '#f59e0b', '#10b981', '#0ea5e9',
  '#ef4444', '#14b8a6', '#a855f7', '#f97316', '#22c55e', '#ec4899',
];

const state = {
  mode: 'design',      // 'design' | 'test'
  softwareId: 0,
  versionId: 0,
  data: null,          // 嵌套 root 节点
  flat: new Map(),     // id -> node
  chart: null,
  originTab: 'mine',
  editor: null,        // { nodeId, kind:'note'|'mark' }
  sseBound: false,
  imgPasteBound: false,
  copyMode: null,      // 复制节点选择态：{ sourceId, sourceName }
  collapsed: new Set(),// 折叠的节点 id：其子树不参与渲染（大图性能 + 聚焦）
  perf: false,         // 精简渲染模式：渲染节点多时自动开，关阴影/动画/hover 联动
  caseCtx: null,       // 关联用例弹窗上下文：{ nodeId }
};

// 渲染节点数超过该值进入精简模式：canvas 阴影、整树动画、hover 全图淡出
// 在 ~1000 节点时是主要卡顿来源，精简模式全部关闭。
const PERF_NODE_LIMIT = 300;

function $(id) { return document.getElementById(id); }
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
// rich 标签里 { } | \ 有特殊含义，节点名展示前先净化（完整名仍在 tooltip / 菜单里）
function sanitizeLabel(s) { return String(s || '').replace(/[{}|\\]/g, ' '); }

// 富文本是否为空：含图片/媒体算有内容；否则剥离标签与 &nbsp;/空白后判断。
// contenteditable 清空后常残留 <br>/<div><br></div>/&nbsp;，需据此判空。
function isBlankHtml(html) {
  if (!html) return true;
  if (/<(img|video|svg|iframe|audio)\b/i.test(html)) return false;
  const text = html.replace(/<[^>]+>/g, '').replace(/&nbsp;/g, '').replace(/ /g, '');
  return !text.trim();
}

function getVisibleTabName() {
  const ids = ['assign', 'mine', 'task-board', 'feedback', 'field-test', 'build-records',
    'testcase-center', 'report', 'activity', 'data', 'dispatch', 'zentao-ai', 'jenkins',
    'cad-test', 'terminal', 'learning'];
  return ids.find((n) => {
    const el = document.getElementById('tab-' + n);
    return el && !el.classList.contains('hidden');
  }) || 'mine';
}

// ── 数据加载 ──────────────────────────────────────────────
async function fetchTree() {
  const qs = new URLSearchParams({ software_id: String(state.softwareId) });
  if (state.mode === 'test' && state.versionId) qs.set('version_id', String(state.versionId));
  const resp = await api('/feature-tree?' + qs.toString());
  const data = await resp.json();
  state.data = data.tree;
  state.softwareName = data.software_name;
  rebuildFlat();
  return data;
}

function rebuildFlat() {
  state.flat.clear();
  const walk = (n) => {
    if (!n) return;
    state.flat.set(n.id, n);
    (n.children || []).forEach(walk);
  };
  walk(state.data);
}

// ── 入口 ──────────────────────────────────────────────────
async function open(mode, ctx = {}) {
  state.mode = mode === 'test' ? 'test' : 'design';
  state.softwareId = Number(ctx.softwareId || (window.getCurrentSoftwareId ? window.getCurrentSoftwareId() : 0) || window.currentSoftwareId || 0);
  state.versionId = Number(ctx.versionId || 0);
  if (!state.softwareId) { window.showMessage && window.showMessage('请先选择软件', 'error'); return; }

  state.originTab = getVisibleTabName();
  loadCollapsed();
  try {
    await fetchTree();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载功能图谱失败', 'error');
    return;
  }
  window.showTab && window.showTab('feature-tree');
  updateChrome();
  bindSSE();
  // 等待 section 显示获得尺寸后再初始化/重绘（恢复上次视角）
  requestAnimationFrame(() => { renderChart({ view: 'restore' }); });
}

// 从需求工作台进入：解析 test 模式所属的最终测试大版本
function openFromWorkbench() {
  const softwareId = Number(window.currentSoftwareId || (window.getCurrentSoftwareId && window.getCurrentSoftwareId()) || 0);
  const versionId = resolveFinalTestMajorId(softwareId);
  if (!versionId) {
    window.showMessage && window.showMessage('当前软件没有处于最终测试状态的大版本', 'error');
    return;
  }
  open('test', { softwareId, versionId });
}

function resolveFinalTestMajorId(softwareId) {
  const versions = window.versions || [];
  const selected = Number($('mineMajorSelect') ? $('mineMajorSelect').value : 0);
  const sel = versions.find((v) => Number(v.id) === selected);
  if (sel && sel.version_type === 'major' && sel.final_test_enabled) return sel.id;
  const cand = versions.find((v) => v.version_type === 'major' && v.final_test_enabled
    && (!softwareId || Number(v.software_id) === Number(softwareId)));
  return cand ? cand.id : 0;
}

// 工作台入口按钮可见性：选了最终测试大版本，或处于"查看所有待测试需求"
function refreshWorkbenchEntry() {
  const btn = $('featureTreeTestBtn');
  if (!btn) return;
  const softwareId = Number(window.currentSoftwareId || 0);
  const mode = $('mineDisplayMode') ? $('mineDisplayMode').value : 'version';
  const eligible = !!resolveFinalTestMajorId(softwareId) && (mode === 'all_pending' || (() => {
    const versions = window.versions || [];
    const selected = Number($('mineMajorSelect') ? $('mineMajorSelect').value : 0);
    const sel = versions.find((v) => Number(v.id) === selected);
    return !!(sel && sel.final_test_enabled);
  })());
  btn.classList.toggle('hidden', !eligible);
}

function close() {
  cancelCopy();
  window.showTab && window.showTab(state.originTab || 'mine');
}

function updateChrome() {
  const titleEl = $('ftreeTitleText');
  const badge = $('ftreeModeBadge');
  if (titleEl) titleEl.textContent = `${state.softwareName || '功能图谱'}`;
  if (badge) {
    if (state.mode === 'test') {
      const v = (window.versions || []).find((x) => Number(x.id) === Number(state.versionId));
      badge.textContent = `全量测试 · ${v ? v.version_no : '版本' + state.versionId}`;
      badge.className = 'ftree-mode-badge ftree-mode-badge--test';
    } else {
      badge.textContent = '图谱编辑';
      badge.className = 'ftree-mode-badge';
    }
  }
  renderLegend();
}

function renderLegend() {
  const el = $('ftreeLegend');
  if (!el) return;
  if (state.mode !== 'test') { el.innerHTML = ''; return; }
  const seen = new Map();
  state.flat.forEach((n) => (n.marks || []).forEach((m) => { if (!seen.has(m.user_id)) seen.set(m.user_id, m); }));
  if (!seen.size) { el.innerHTML = '<span class="ftree-legend-empty">暂无测试标记</span>'; return; }
  el.innerHTML = [...seen.values()].map((m) =>
    `<span class="ftree-legend-item"><span class="ftree-legend-dot" style="background:${m.color}"></span>${escapeHtml(m.display_name)}</span>`
  ).join('');
}

// ── 折叠（子树不渲染）────────────────────────────────────
function collapsedStorageKey() { return `ftreeCollapsed:${state.softwareId}`; }
function loadCollapsed() {
  try {
    const arr = JSON.parse(localStorage.getItem(collapsedStorageKey()) || '[]');
    state.collapsed = new Set(Array.isArray(arr) ? arr.map(Number) : []);
  } catch (e) { state.collapsed = new Set(); }
}
function persistCollapsed() {
  try { localStorage.setItem(collapsedStorageKey(), JSON.stringify([...state.collapsed])); } catch (e) { /* 忽略 */ }
}
function descendantCount(n) {
  let c = 0;
  (n.children || []).forEach((ch) => { c += 1 + descendantCount(ch); });
  return c;
}
function isCollapsed(n) {
  return state.collapsed.has(n.id) && (n.children || []).length > 0;
}
// 折叠裁剪后实际会渲染的节点数（决定是否进入精简模式）
function countRendered(n) {
  if (!n) return 0;
  if (isCollapsed(n)) return 1;
  return 1 + (n.children || []).reduce((s, c) => s + countRendered(c), 0);
}
function toggleCollapse(node) {
  if (state.collapsed.has(node.id)) state.collapsed.delete(node.id);
  else state.collapsed.add(node.id);
  persistCollapsed();
  renderChart({ view: 'keep' });
}
function collapseAll() {
  // 只保留 根 + 一级 + 二级：把深度 ≥2 且有子分支的节点全部折叠
  state.collapsed = new Set();
  const walk = (n, depth) => {
    (n.children || []).forEach((c) => walk(c, depth + 1));
    if (depth >= 2 && (n.children || []).length) state.collapsed.add(n.id);
  };
  if (state.data) walk(state.data, 0);
  persistCollapsed();
  renderChart({ view: 'keep' });
}
function expandAll() {
  state.collapsed = new Set();
  persistCollapsed();
  renderChart({ view: 'keep' });
}

// ── ECharts 渲染 ──────────────────────────────────────────
function toEchartNode(n) {
  const isRoot = !!n.is_root;
  const markCount = (n.marks || []).length;
  let color = isRoot ? '#6d6cf5' : '#b9b8fb';
  let borderColor = isRoot ? '#fff' : '#8b8af7';
  let borderWidth = isRoot ? 3 : 1.5;
  let shadowBlur = isRoot ? 24 : 0;
  let shadowColor = 'rgba(109,108,245,0.55)';
  // 已测试节点：在彩色描边的基础上，把节点本体也换成测试人颜色（白描边 + 同色辉光），
  // 让"已测"节点在一片浅紫未测节点中显著跳出来。
  if (markCount && n.marks[0]) {
    const mc = n.marks[0].color;
    color = mc;
    borderColor = '#ffffff';
    borderWidth = 3;
    shadowBlur = 14;
    shadowColor = mc;
  }
  // 精简模式：canvas 阴影按节点数放大重绘成本，是大图最贵的一项，全部去掉
  if (state.perf) shadowBlur = 0;
  const collapsed = isCollapsed(n);
  return {
    name: n.name,
    value: n.id,
    _meta: n,
    itemStyle: { color, borderColor, borderWidth, shadowBlur, shadowColor },
    children: collapsed ? [] : (n.children || []).map(toEchartNode),
  };
}

function buildRich() {
  const rich = {
    nm: { fontSize: 13, color: '#1c1917', fontWeight: 600, padding: [3, 7], backgroundColor: 'rgba(255,255,255,0.92)', borderRadius: 8, borderColor: 'rgba(28,25,23,0.10)', borderWidth: 1 },
    note: { fontSize: 13, padding: [0, 2] },
    case: { fontSize: 11, color: '#0e7490', fontWeight: 700, padding: [1, 4], backgroundColor: 'rgba(207,250,254,0.95)', borderRadius: 6 },
    col: { fontSize: 11, color: '#7c7cf2', fontWeight: 700, padding: [1, 4], backgroundColor: 'rgba(232,232,253,0.95)', borderRadius: 6 },
    more: { fontSize: 11, color: '#a8a29e', padding: [0, 2] },
  };
  MARK_PALETTE.forEach((c, i) => { rich['c' + i] = { color: c, fontSize: 17, padding: [0, 1] }; });
  return rich;
}

function labelFormatter(params) {
  const m = params.data && params.data._meta;
  if (!m) return '';
  let s = `{nm|${sanitizeLabel(m.name)}}`;
  const badges = [];
  if (m.has_note) badges.push('{note|📝}');
  if ((m.cases || []).length) badges.push(`{case|🧪${m.cases.length}}`);
  if (isCollapsed(m)) badges.push(`{col|▸${descendantCount(m)}}`);
  const marks = m.marks || [];
  marks.slice(0, 8).forEach((mk) => {
    const idx = MARK_PALETTE.indexOf(mk.color);
    badges.push(`{c${idx >= 0 ? idx : 0}|●}`);
  });
  if (marks.length > 8) badges.push(`{more|+${marks.length - 8}}`);
  if (badges.length) s += '\n' + badges.join('');
  return s;
}

function tooltipFormatter(params) {
  const m = params.data && params.data._meta;
  if (!m) return '';
  let h = `<div class="ftree-tip-name">${escapeHtml(m.name)}</div>`;
  if (m.has_note && m.note_html) h += `<div class="ftree-tip-note">${m.note_html}</div>`;
  if (isCollapsed(m)) h += `<div class="ftree-tip-collapsed">▸ 已折叠 ${descendantCount(m)} 个子分支（点击节点菜单展开）</div>`;
  const cases = m.cases || [];
  if (cases.length) {
    h += `<div class="ftree-tip-cases"><div class="ftree-tip-cases-head">🧪 关联用例（${cases.length}）</div>`;
    cases.forEach((c) => {
      h += `<div class="ftree-tip-case${c.deleted ? ' ftree-tip-case--deleted' : ''}">`
        + `<span class="ftree-tip-case-id">${escapeHtml(c.case_id || '')}</span>`
        + `<span class="ftree-tip-case-title" title="${escapeHtml(c.title || '')}">${escapeHtml(c.title || '')}</span>`
        + `<span class="ftree-tip-case-acts">`
        + `<button onclick="window.OmniQAFeatureTreeTab.caseAction(${m.id},${c.case_numeric_id},'preview')" title="预览用例详情">预览</button>`
        + (c.url ? `<button onclick="window.OmniQAFeatureTreeTab.caseAction(${m.id},${c.case_numeric_id},'jump')" title="在禅道中打开">跳转</button>` : '')
        + `<button class="ftree-tip-case-del" onclick="window.OmniQAFeatureTreeTab.caseAction(${m.id},${c.case_numeric_id},'unlink')" title="解除关联">删除</button>`
        + `</span></div>`;
    });
    h += `</div>`;
  }
  (m.marks || []).forEach((mk) => {
    h += `<div class="ftree-tip-mark"><span class="ftree-tip-dot" style="background:${mk.color}"></span>`
      + `<b>${escapeHtml(mk.display_name)}</b> 已测`
      + (mk.is_auto ? `<span class="ftree-tip-auto">（自动：子分支均已标记）</span>` : '')
      + (mk.comment_html ? `<div class="ftree-tip-markbody">${mk.comment_html}</div>` : '')
      + `</div>`;
  });
  return h;
}

function buildOption(seriesData) {
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', enterable: true, appendToBody: true, confine: true,
      extraCssText: 'max-width:340px;white-space:normal;', borderColor: 'rgba(28,25,23,0.12)',
      formatter: tooltipFormatter },
    series: [{
      type: 'tree',
      data: seriesData,
      layout: 'radial',
      roam: true,
      initialTreeDepth: -1,
      expandAndCollapse: false,
      symbol: 'circle',
      symbolSize: (v, p) => (p.data._meta && p.data._meta.is_root ? 22 : 12),
      edgeShape: 'curve',
      lineStyle: { color: 'rgba(139,138,247,0.45)', width: 1.4, curveness: 0.5 },
      label: { formatter: labelFormatter, rich: buildRich() },
      // 精简模式：focus:'descendant' 会在每次 hover 时给全图其余节点加淡出效果，
      // 上千节点等于每次 hover 全量重绘，是 hover 卡顿的主因，故关闭。
      emphasis: state.perf
        ? { focus: 'none', itemStyle: { borderWidth: 3 } }
        : { focus: 'descendant', itemStyle: { shadowBlur: 18, shadowColor: 'rgba(109,108,245,0.6)' } },
      animation: !state.perf,
      animationDuration: state.perf ? 0 : 500,
      animationDurationUpdate: state.perf ? 0 : 450,
      animationEasing: 'cubicOut',
    }],
  };
}

// view: 'keep'（数据更新，merge 保留缩放/平移）| 'restore'（首次/刷新，恢复上次视角）
//       | 'reset'（居中按钮，回全图）
function renderChart({ view = 'restore' } = {}) {
  const dom = $('ftreeCanvas');
  if (!dom || !window.echarts) return;
  let firstInit = false;
  if (!state.chart || (state.chart.isDisposed && state.chart.isDisposed())) {
    // useDirtyRect：hover/tooltip 等局部变化只重绘脏矩形，大图收益显著
    state.chart = window.echarts.getInstanceByDom(dom) || window.echarts.init(dom, null, { useDirtyRect: true });
    state.chart.on('click', onNodeClick);
    window.addEventListener('resize', resize);
    bindRoamPersist();
    firstInit = true;
  }
  const prevPerf = state.perf;
  state.perf = countRendered(state.data) > PERF_NODE_LIMIT;
  // 精简模式切换会改 series 级配置（emphasis/animation），merge 更新不生效，需整体重建
  if (!firstInit && prevPerf !== state.perf && view === 'keep') {
    const keep = captureView();
    state.chart.setOption(buildOption([toEchartNode(state.data)]), { notMerge: true });
    requestAnimationFrame(() => applyView(keep));
    state.rendered = true;
    resize();
    return;
  }
  const seriesData = [toEchartNode(state.data)];
  if (view === 'keep' && state.rendered && !firstInit) {
    // 合并更新数据，不动坐标系 → 当前缩放/平移保留
    state.chart.setOption({ series: [{ data: seriesData }] });
  } else {
    state.chart.setOption(buildOption(seriesData), { notMerge: true });
    if (view === 'restore') restorePersistedView();
    else if (view === 'reset') clearPersistedView();
  }
  state.rendered = true;
  resize();
}

function resize() { if (state.chart && !(state.chart.isDisposed && state.chart.isDisposed())) state.chart.resize(); }
function fit() { renderChart({ view: 'reset' }); }

// ── 视角（缩放/平移）持久化：让操作/刷新后停留在原位 ────────
function viewStorageKey() {
  return `ftreeView:${state.softwareId}:${state.mode}:${state.versionId || 0}`;
}
// 取径向树的可平移/缩放渲染组（ECharts tree 内部 _mainGroup）。
// 用内部结构，全部 try/catch 包裹：取不到就降级（不报错、仅不持久化）。
function getRoamGroup() {
  try {
    const views = state.chart && state.chart._chartsViews;
    if (!views || !views.length) return null;
    const v = views.find((x) => x && x.__model && x.__model.subType === 'tree');
    return (v && (v._mainGroup || v.group)) || null;
  } catch (e) { return null; }
}
function captureView() {
  const g = getRoamGroup();
  if (!g) return null;
  return { x: g.x, y: g.y, sx: g.scaleX, sy: g.scaleY };
}
function applyView(t) {
  if (!t) return;
  const g = getRoamGroup();
  if (!g) return;
  try {
    g.x = t.x; g.y = t.y; g.scaleX = t.sx; g.scaleY = t.sy;
    g.dirty && g.dirty();
    state.chart.getZr().refresh();
  } catch (e) { /* 忽略 */ }
}
function persistView() {
  const t = captureView();
  if (!t) return;
  try { localStorage.setItem(viewStorageKey(), JSON.stringify(t)); } catch (e) { /* 忽略 */ }
}
function clearPersistedView() {
  try { localStorage.removeItem(viewStorageKey()); } catch (e) { /* 忽略 */ }
}
function restorePersistedView() {
  let t = null;
  try { t = JSON.parse(localStorage.getItem(viewStorageKey()) || 'null'); } catch (e) { t = null; }
  if (!t) return;
  // 等 ECharts 完成本次布局后再套用变换
  requestAnimationFrame(() => applyView(t));
}
let _roamPersistBound = false;
function bindRoamPersist() {
  if (_roamPersistBound || !state.chart) return;
  _roamPersistBound = true;
  let timer = null;
  const save = () => { clearTimeout(timer); timer = setTimeout(persistView, 300); };
  try {
    const zr = state.chart.getZr();
    zr.on('mouseup', save);
    zr.on('mousewheel', save);
  } catch (e) { /* 忽略 */ }
}

// 内容指纹：节点 id/名称/备注更新时间/各标记的人与更新时间。用于跳过"无变化"的重绘，
// 避免自己刚改完被自己的广播再重绘一次、以及无关事件造成视图（缩放/平移）被重置。
function treeSignature(node) {
  const parts = [];
  const walk = (n) => {
    if (!n) return;
    parts.push(`${n.id}:${n.name}:${n.note_updated_at || ''}:${(n.marks || []).map((m) => m.user_id + '@' + (m.updated_at || '')).join(',')}:${(n.cases || []).map((c) => c.case_numeric_id).join(',')}`);
    (n.children || []).forEach(walk);
  };
  walk(node);
  return parts.join('|');
}

async function reload({ force = false } = {}) {
  try {
    await fetchTree();
    const sig = treeSignature(state.data);
    if (!force && sig === state.lastSig) return; // 内容无变化，不重绘
    state.lastSig = sig;
    updateChrome();
    renderChart({ view: 'keep' }); // 数据更新保留当前缩放/平移
  } catch (err) { window.showMessage && window.showMessage(err.message || '刷新失败', 'error'); }
}

// ── 节点点击 → 操作浮层 ───────────────────────────────────
function onNodeClick(params) {
  const m = params.data && params.data._meta;
  if (!m) return;
  // 复制选择态：点击节点 = 选定目标，而非打开操作菜单
  if (state.copyMode) { chooseCopyTarget(m); return; }
  const ev = params.event && params.event.event;
  const x = ev ? ev.clientX : window.innerWidth / 2;
  const y = ev ? ev.clientY : window.innerHeight / 2;
  openMenu(m, x, y);
}

// ── 复制节点（子树深拷贝到另一节点下，仅节点+备注，不含测试状态）─────
function startCopy(node) {
  state.copyMode = { sourceId: node.id, sourceName: node.name };
  const sec = $('tab-feature-tree');
  if (sec) sec.classList.add('ftree-copying');
  const banner = $('ftreeCopyBanner');
  if (banner) {
    const txt = banner.querySelector('.ftree-copy-banner-text');
    if (txt) txt.innerHTML = `复制「<b>${escapeHtml(node.name)}</b>」中：拖动/缩放画面，点选要接入的目标节点`;
    banner.classList.remove('hidden');
  }
}

function cancelCopy() {
  state.copyMode = null;
  const sec = $('tab-feature-tree');
  if (sec) sec.classList.remove('ftree-copying');
  const banner = $('ftreeCopyBanner');
  if (banner) banner.classList.add('hidden');
}

// target 是否为 ancestorId 的后代（用嵌套 state.flat 节点走子树判断）
function isDescendant(ancestorId, nodeId) {
  const anc = state.flat.get(ancestorId);
  if (!anc) return false;
  let found = false;
  const walk = (n) => (n.children || []).forEach((c) => { if (c.id === nodeId) found = true; walk(c); });
  walk(anc);
  return found;
}

function chooseCopyTarget(target) {
  const { sourceId, sourceName } = state.copyMode;
  if (target.id === sourceId) {
    window.showMessage && window.showMessage('不能复制到自身，请另选目标节点', 'error');
    return;
  }
  if (isDescendant(sourceId, target.id)) {
    window.showMessage && window.showMessage('不能复制到它自己的子分支下，请另选目标节点', 'error');
    return;
  }
  if (!window.confirm(`将「${sourceName}」及其所有子分支复制到「${target.name}」下？\n（仅复制节点与功能备注，不保留测试状态/测试说明）`)) return;
  doCopy(sourceId, target.id);
}

async function doCopy(sourceId, targetId) {
  try {
    await api(`/feature-tree/nodes/${sourceId}/copy`, { method: 'POST', body: { target_id: targetId } });
    cancelCopy();
    await reload();
    window.showMessage && window.showMessage('复制成功');
  } catch (err) { window.showMessage && window.showMessage(err.message || '复制失败', 'error'); }
}

function openMenu(node, x, y) {
  const menu = $('ftreeMenu');
  if (!menu) return;
  const items = [];
  items.push({ icon: '➕', label: '添加子分支', act: () => addBranch(node) });
  if (!node.is_root) items.push({ icon: '✏️', label: '重命名', act: () => renameNode(node) });
  if (!node.is_root) items.push({ icon: '📋', label: '复制节点', act: () => startCopy(node) });
  items.push({ icon: '📝', label: node.has_note ? '编辑备注' : '添加备注', act: () => openEditor(node, 'note') });
  const caseCount = (node.cases || []).length;
  items.push({ icon: '🧪', label: caseCount ? `关联用例（已关联 ${caseCount}）` : '关联用例', act: () => openCaseModal(node) });
  if ((node.children || []).length) {
    const collapsed = state.collapsed.has(node.id);
    items.push({ icon: collapsed ? '▸' : '▾', label: collapsed ? `展开子分支（${descendantCount(node)}）` : '折叠子分支', act: () => toggleCollapse(node) });
  }
  if (state.mode === 'test') {
    const isLeaf = !node.children || node.children.length === 0;
    const mine = (node.marks || []).find((mk) => Number(mk.user_id) === Number(currentUserId()));
    if (isLeaf) {
      // 叶子节点：手动打标记 / 编辑说明 / 取消
      items.push({ icon: '✅', label: mine ? '编辑我的测试标记' : '标记我已测', act: () => openEditor(node, 'mark') });
      if (mine) items.push({ icon: '❌', label: '取消我的标记', danger: true, act: () => removeMark(node) });
    } else {
      // 含子分支：标记由子分支自动汇总，不能手动打
      items.push({ icon: '🧩', label: '子分支全部标记后自动汇总', disabled: true, act: () => {} });
    }
  }
  if (!node.is_root) items.push({ icon: '🗑️', label: '删除分支', danger: true, act: () => deleteNode(node) });

  menu.innerHTML = `<div class="ftree-menu-title">${escapeHtml(node.name)}</div>`
    + items.map((it, i) => `<button class="ftree-menu-item${it.danger ? ' danger' : ''}${it.disabled ? ' ftree-menu-item--hint' : ''}" data-i="${i}"${it.disabled ? ' disabled' : ''}><span class="ftree-menu-icon">${it.icon}</span><span class="ftree-menu-label">${it.label}</span></button>`).join('');
  menu.querySelectorAll('.ftree-menu-item:not([disabled])').forEach((btn) => {
    btn.onclick = () => { hideMenu(); items[Number(btn.dataset.i)].act(); };
  });
  // 定位（避免溢出视口）
  menu.classList.remove('hidden');
  const r = menu.getBoundingClientRect();
  const px = Math.min(x, window.innerWidth - r.width - 12);
  const py = Math.min(y, window.innerHeight - r.height - 12);
  menu.style.left = Math.max(8, px) + 'px';
  menu.style.top = Math.max(8, py) + 'px';
  // 标记"本次点击刚打开菜单"，让随后冒泡到 document 的同一次 click 不要立刻关掉它。
  menuJustOpened = true;
}
let menuJustOpened = false;
function hideMenu() { const m = $('ftreeMenu'); if (m) m.classList.add('hidden'); }

function currentUserId() { return (window.currentUser && window.currentUser.id) || 0; }

// ── 写操作 ────────────────────────────────────────────────
async function addBranch(node) {
  const name = window.prompt(`在「${node.name}」下新建分支，输入名称：`, '');
  if (name == null) return;
  if (!name.trim()) { window.showMessage && window.showMessage('名称不能为空', 'error'); return; }
  try {
    await api('/feature-tree/nodes', { method: 'POST', body: { software_id: state.softwareId, parent_id: node.id, name: name.trim() } });
    await reload();
  } catch (err) { window.showMessage && window.showMessage(err.message || '创建失败', 'error'); }
}

async function renameNode(node) {
  const name = window.prompt('重命名分支：', node.name);
  if (name == null) return;
  if (!name.trim()) { window.showMessage && window.showMessage('名称不能为空', 'error'); return; }
  try {
    await api(`/feature-tree/nodes/${node.id}`, { method: 'PUT', body: { name: name.trim() } });
    await reload();
  } catch (err) { window.showMessage && window.showMessage(err.message || '重命名失败', 'error'); }
}

async function deleteNode(node) {
  if (!window.confirm(`确定删除分支「${node.name}」及其所有子分支、标记？此操作不可恢复。`)) return;
  try {
    await api(`/feature-tree/nodes/${node.id}`, { method: 'DELETE' });
    await reload();
  } catch (err) { window.showMessage && window.showMessage(err.message || '删除失败', 'error'); }
}

async function removeMark(node) {
  try {
    await api(`/feature-tree/nodes/${node.id}/mark?version_id=${state.versionId}`, { method: 'DELETE' });
    await reload();
  } catch (err) { window.showMessage && window.showMessage(err.message || '取消标记失败', 'error'); }
}

// ── 关联用例 ─────────────────────────────────────────────
let caseSearchTimer = null;
let caseSearchSeq = 0;

function openCaseModal(node) {
  state.caseCtx = { nodeId: node.id };
  const modal = $('ftreeCaseModal');
  const input = $('ftreeCaseSearch');
  if (!modal || !input) return;
  const titleEl = $('ftreeCaseTitle');
  if (titleEl) titleEl.textContent = `关联用例 · ${node.name}`;
  input.value = '';
  bindCaseModal();
  renderLinkedCases();
  modal.classList.remove('hidden');
  input.focus();
  runCaseSearch(''); // 空关键字 = 展示最近更新的用例，便于直接挑选
}

function closeCaseModal() {
  const modal = $('ftreeCaseModal');
  if (modal) modal.classList.add('hidden');
  state.caseCtx = null;
  clearTimeout(caseSearchTimer);
}

function bindCaseModal() {
  const modal = $('ftreeCaseModal');
  if (!modal || modal._bound) return;
  modal._bound = true;
  modal.addEventListener('click', (e) => { if (e.target === modal) closeCaseModal(); });
  const input = $('ftreeCaseSearch');
  input.addEventListener('input', () => {
    clearTimeout(caseSearchTimer);
    caseSearchTimer = setTimeout(() => runCaseSearch(input.value.trim()), 300);
  });
}

function renderLinkedCases() {
  const box = $('ftreeCaseLinked');
  if (!box || !state.caseCtx) return;
  const node = state.flat.get(state.caseCtx.nodeId);
  const cases = (node && node.cases) || [];
  if (!cases.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="ftree-case-linked-head">已关联（${cases.length}）</div>`
    + cases.map((c) =>
      `<span class="ftree-case-chip${c.deleted ? ' ftree-case-chip--deleted' : ''}" title="${escapeHtml(c.title || '')}">`
      + `${escapeHtml(c.case_id || '')} ${escapeHtml((c.title || '').slice(0, 18))}`
      + `<button class="ftree-case-chip-x" onclick="window.OmniQAFeatureTreeTab.caseAction(${node.id},${c.case_numeric_id},'unlink')" title="解除关联">✕</button></span>`
    ).join('');
}

async function runCaseSearch(keyword) {
  const box = $('ftreeCaseResults');
  if (!box || !state.caseCtx) return;
  const seq = ++caseSearchSeq;
  box.innerHTML = '<div class="ftree-case-empty">搜索中…</div>';
  let items = [];
  try {
    const qs = new URLSearchParams({ software_id: String(state.softwareId), keyword });
    const data = await (await api('/feature-tree/case-search?' + qs.toString())).json();
    items = data.items || [];
  } catch (err) {
    if (seq === caseSearchSeq) box.innerHTML = `<div class="ftree-case-empty">${escapeHtml(err.message || '搜索失败')}</div>`;
    return;
  }
  if (seq !== caseSearchSeq || !state.caseCtx) return; // 已有更新的搜索/弹窗已关
  if (!items.length) { box.innerHTML = '<div class="ftree-case-empty">没有匹配的用例</div>'; return; }
  const node = state.flat.get(state.caseCtx.nodeId);
  const linked = new Set(((node && node.cases) || []).map((c) => Number(c.case_numeric_id)));
  box.innerHTML = items.map((c) => {
    const done = linked.has(Number(c.case_numeric_id));
    return `<div class="ftree-case-row" data-cnid="${c.case_numeric_id}">`
      + `<span class="ftree-case-row-id">${escapeHtml(c.case_id || '')}</span>`
      + `<span class="ftree-case-row-title" title="${escapeHtml(c.title || '')}">${escapeHtml(c.title || '')}</span>`
      + (c.status ? `<span class="ftree-case-row-status">${escapeHtml(c.status)}</span>` : '')
      + `<button class="ftree-case-row-link" ${done ? 'disabled' : ''}>${done ? '✓ 已关联' : '关联'}</button>`
      + `</div>`;
  }).join('');
  box.querySelectorAll('.ftree-case-row-link:not([disabled])').forEach((btn) => {
    btn.onclick = () => linkCase(Number(btn.closest('.ftree-case-row').dataset.cnid), btn);
  });
}

async function linkCase(caseNumericId, btn) {
  if (!state.caseCtx) return;
  const nodeId = state.caseCtx.nodeId;
  if (btn) { btn.disabled = true; btn.textContent = '关联中…'; }
  try {
    await api(`/feature-tree/nodes/${nodeId}/cases`, { method: 'POST', body: { case_numeric_id: caseNumericId } });
    await reload({ force: true });
    if (btn) btn.textContent = '✓ 已关联';
    renderLinkedCases();
    window.showMessage && window.showMessage('已关联用例');
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = '关联'; }
    window.showMessage && window.showMessage(err.message || '关联失败', 'error');
  }
}

// tooltip / 弹窗里的用例操作：预览（复用用例中心详情弹窗）、跳转禅道、解除关联
async function caseAction(nodeId, caseNumericId, action) {
  const node = state.flat.get(Number(nodeId));
  const c = node && (node.cases || []).find((x) => Number(x.case_numeric_id) === Number(caseNumericId));
  if (action === 'jump') {
    if (c && c.url) window.open(c.url, '_blank', 'noopener');
    else window.showMessage && window.showMessage('该用例没有禅道链接', 'error');
    return;
  }
  if (action === 'preview') {
    try {
      const detail = await (await api(`/feature-tree/cases/${caseNumericId}?software_id=${state.softwareId}`)).json();
      const tc = window.OmniQATestcaseCenterTab;
      if (tc && tc.showTestcaseDetailModal) tc.showTestcaseDetailModal(detail);
    } catch (err) { window.showMessage && window.showMessage(err.message || '加载用例详情失败', 'error'); }
    return;
  }
  if (action === 'unlink') {
    const label = c ? `用例 ${c.case_id}` : '该用例';
    if (!window.confirm(`解除「${(node && node.name) || '节点'}」与 ${label} 的关联？（不会删除禅道用例本身）`)) return;
    try {
      await api(`/feature-tree/nodes/${nodeId}/cases/${caseNumericId}`, { method: 'DELETE' });
      await reload({ force: true });
      renderLinkedCases();
      window.showMessage && window.showMessage('已解除关联');
    } catch (err) { window.showMessage && window.showMessage(err.message || '解除关联失败', 'error'); }
  }
}

// ── 富文本编辑弹窗 ───────────────────────────────────────
function openEditor(node, kind) {
  state.editor = { nodeId: node.id, kind };
  const modal = $('ftreeEditorModal');
  const titleEl = $('ftreeEditorTitle');
  const ed = $('ftreeEditor');
  if (!modal || !ed) return;
  if (kind === 'note') {
    titleEl.textContent = `功能备注 · ${node.name}`;
    ed.innerHTML = node.note_html || '';
  } else {
    const mine = (node.marks || []).find((mk) => Number(mk.user_id) === Number(currentUserId()));
    titleEl.textContent = `测试标记说明 · ${node.name}（可留空）`;
    ed.innerHTML = (mine && mine.comment_html) || '';
  }
  bindEditorTools();
  modal.classList.remove('hidden');
  ed.focus();
}
function closeEditor() {
  const m = $('ftreeEditorModal');
  if (m) m.classList.add('hidden');
  state.editor = null;
  // 编辑期间若有别人推来的更新被挂起，关闭后补刷一次
  if (state.pendingRemote) { state.pendingRemote = false; scheduleRemoteReload(); }
}

async function saveEditor() {
  if (!state.editor) return;
  const ed = $('ftreeEditor');
  let html = ed ? ed.innerHTML.trim() : '';
  if (isBlankHtml(html)) html = ''; // 空内容（仅残留 <br>/&nbsp;）统一存空串
  const { nodeId, kind } = state.editor;
  try {
    if (kind === 'note') {
      await api(`/feature-tree/nodes/${nodeId}`, { method: 'PUT', body: { note_html: html } });
    } else {
      await api(`/feature-tree/nodes/${nodeId}/mark`, { method: 'PUT', body: { version_id: state.versionId, comment_html: html } });
    }
    closeEditor();
    await reload();
  } catch (err) { window.showMessage && window.showMessage(err.message || '保存失败', 'error'); }
}

function bindEditorTools() {
  const modal = $('ftreeEditorModal');
  if (!modal || modal._toolsBound) return;
  modal._toolsBound = true;
  // 点遮罩（弹窗内容之外）关闭编辑弹窗
  modal.addEventListener('click', (e) => { if (e.target === modal) closeEditor(); });
  modal.querySelectorAll('.ftree-editor-toolbar [data-cmd]').forEach((btn) => {
    btn.addEventListener('mousedown', (e) => { e.preventDefault(); document.execCommand(btn.dataset.cmd, false, null); });
  });
  const imgBtn = $('ftreeEditorImageBtn');
  const imgInput = $('ftreeEditorImageInput');
  if (imgBtn && imgInput) {
    imgBtn.addEventListener('click', () => imgInput.click());
    imgInput.addEventListener('change', async () => {
      const f = imgInput.files && imgInput.files[0];
      if (f) await uploadAndInsert(f);
      imgInput.value = '';
    });
  }
  const ed = $('ftreeEditor');
  if (ed && !state.imgPasteBound) {
    state.imgPasteBound = true;
    ed.addEventListener('paste', async (event) => {
      const cd = event.clipboardData;
      if (!cd) return;
      const imgs = Array.from(cd.items || []).filter((it) => it.kind === 'file' && (it.type || '').startsWith('image/'));
      if (!imgs.length) return;
      event.preventDefault();
      for (const it of imgs) { const f = it.getAsFile(); if (f) await uploadAndInsert(f); }
    });
  }
}

async function uploadAndInsert(file) {
  try {
    const form = new FormData();
    form.append('file', file, file.name || `paste-${Date.now()}.png`);
    const resp = await api('/feature-tree/upload-image', { method: 'POST', body: form });
    const data = await resp.json();
    if (data && data.url) {
      document.execCommand('insertHTML', false, `<img src="${data.url}" style="max-width:100%;">`);
    }
  } catch (err) { window.showMessage && window.showMessage(err.message || '图片上传失败', 'error'); }
}

// ── SSE 实时刷新 ─────────────────────────────────────────
let remoteReloadTimer = null;
// 远端更新（别人加分支/备注/打标记）：防抖合并连发；正在编辑富文本时先挂起，
// 等关闭弹窗再刷新，避免在用户编辑途中把树重绘、视图被重置。
function scheduleRemoteReload() {
  const sec = $('tab-feature-tree');
  if (!sec || sec.classList.contains('hidden')) return;
  if (state.editor) { state.pendingRemote = true; return; }
  clearTimeout(remoteReloadTimer);
  remoteReloadTimer = setTimeout(() => reload(), 400);
}
function bindSSE() {
  if (state.sseBound || !window.OmniQASSE || typeof window.OmniQASSE.subscribe !== 'function') return;
  state.sseBound = true;
  window.OmniQASSE.subscribe('feature_tree_updated', ({ payload }) => {
    if (Number(payload && payload.software_id) !== Number(state.softwareId)) return;
    scheduleRemoteReload();
  });
}

// 关闭操作菜单：点菜单外任意处（含画布空白）都关；点菜单内部不关。
// 打开菜单的那一次 click 由 menuJustOpened 吸收，避免开了又被自己关掉。
document.addEventListener('click', (e) => {
  const menu = $('ftreeMenu');
  if (!menu || menu.classList.contains('hidden')) return;
  if (menuJustOpened) { menuJustOpened = false; return; }
  if (!menu.contains(e.target)) hideMenu();
});

// ESC：优先关编辑弹窗，否则关操作菜单
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const modal = $('ftreeEditorModal');
  if (modal && !modal.classList.contains('hidden')) { closeEditor(); return; }
  const caseModal = $('ftreeCaseModal');
  if (caseModal && !caseModal.classList.contains('hidden')) { closeCaseModal(); return; }
  if (state.copyMode) { cancelCopy(); return; }
  const menu = $('ftreeMenu');
  if (menu && !menu.classList.contains('hidden')) hideMenu();
});

// 需求工作台里改"显示模式 / 大版本"时，实时刷新"全量测试树状图"入口按钮的显隐。
// 用事件委托，不依赖元素渲染时机，也不覆盖下拉框已有的内联 onchange。
document.addEventListener('change', (e) => {
  const id = e.target && e.target.id;
  if (id === 'mineDisplayMode' || id === 'mineMajorSelect') refreshWorkbenchEntry();
});

window.OmniQAFeatureTreeTab = {
  open, openFromWorkbench, refreshWorkbenchEntry, close, fit,
  reload: () => reload({ force: true }), // 手动"刷新"按钮：强制重绘
  closeEditor, saveEditor, cancelCopy,
  closeCaseModal, caseAction,
  collapseAll, expandAll,
};
