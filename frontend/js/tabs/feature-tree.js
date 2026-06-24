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
};

function $(id) { return document.getElementById(id); }
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
// rich 标签里 { } | \ 有特殊含义，节点名展示前先净化（完整名仍在 tooltip / 菜单里）
function sanitizeLabel(s) { return String(s || '').replace(/[{}|\\]/g, ' '); }

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
  try {
    await fetchTree();
  } catch (err) {
    window.showMessage && window.showMessage(err.message || '加载功能图谱失败', 'error');
    return;
  }
  window.showTab && window.showTab('feature-tree');
  updateChrome();
  bindSSE();
  // 等待 section 显示获得尺寸后再初始化/重绘
  requestAnimationFrame(() => { renderChart(); });
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

// ── ECharts 渲染 ──────────────────────────────────────────
function toEchartNode(n) {
  const isRoot = !!n.is_root;
  const markCount = (n.marks || []).length;
  let color = isRoot ? '#6d6cf5' : '#b9b8fb';
  let borderColor = isRoot ? '#fff' : '#8b8af7';
  let borderWidth = isRoot ? 3 : 1.5;
  let shadowBlur = isRoot ? 24 : 0;
  if (markCount && n.marks[0]) { borderColor = n.marks[0].color; borderWidth = 3; }
  return {
    name: n.name,
    value: n.id,
    _meta: n,
    itemStyle: { color, borderColor, borderWidth, shadowBlur, shadowColor: 'rgba(109,108,245,0.55)' },
    children: (n.children || []).map(toEchartNode),
  };
}

function buildRich() {
  const rich = {
    nm: { fontSize: 13, color: '#1c1917', fontWeight: 600, padding: [3, 7], backgroundColor: 'rgba(255,255,255,0.92)', borderRadius: 8, borderColor: 'rgba(28,25,23,0.10)', borderWidth: 1 },
    note: { fontSize: 13, padding: [0, 2] },
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
  (m.marks || []).forEach((mk) => {
    h += `<div class="ftree-tip-mark"><span class="ftree-tip-dot" style="background:${mk.color}"></span>`
      + `<b>${escapeHtml(mk.display_name)}</b> 已测`
      + (mk.comment_html ? `<div class="ftree-tip-markbody">${mk.comment_html}</div>` : '')
      + `</div>`;
  });
  return h;
}

function renderChart() {
  const dom = $('ftreeCanvas');
  if (!dom || !window.echarts) return;
  if (!state.chart || state.chart.isDisposed && state.chart.isDisposed()) {
    state.chart = window.echarts.getInstanceByDom(dom) || window.echarts.init(dom);
    state.chart.on('click', onNodeClick);
    window.addEventListener('resize', resize);
  }
  const option = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', enterable: true, appendToBody: true, confine: true,
      extraCssText: 'max-width:340px;white-space:normal;', borderColor: 'rgba(28,25,23,0.12)',
      formatter: tooltipFormatter },
    series: [{
      type: 'tree',
      data: [toEchartNode(state.data)],
      layout: 'radial',
      roam: true,
      initialTreeDepth: -1,
      expandAndCollapse: false,
      symbol: 'circle',
      symbolSize: (v, p) => (p.data._meta && p.data._meta.is_root ? 22 : 12),
      edgeShape: 'curve',
      lineStyle: { color: 'rgba(139,138,247,0.45)', width: 1.4, curveness: 0.5 },
      label: { formatter: labelFormatter, rich: buildRich() },
      emphasis: { focus: 'descendant', itemStyle: { shadowBlur: 18, shadowColor: 'rgba(109,108,245,0.6)' } },
      animationDuration: 500,
      animationDurationUpdate: 450,
      animationEasing: 'cubicOut',
    }],
  };
  state.chart.setOption(option, { notMerge: true });
  resize();
}

function resize() { if (state.chart && !(state.chart.isDisposed && state.chart.isDisposed())) state.chart.resize(); }
function fit() { renderChart(); }

// 内容指纹：节点 id/名称/备注更新时间/各标记的人与更新时间。用于跳过"无变化"的重绘，
// 避免自己刚改完被自己的广播再重绘一次、以及无关事件造成视图（缩放/平移）被重置。
function treeSignature(node) {
  const parts = [];
  const walk = (n) => {
    if (!n) return;
    parts.push(`${n.id}:${n.name}:${n.note_updated_at || ''}:${(n.marks || []).map((m) => m.user_id + '@' + (m.updated_at || '')).join(',')}`);
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
    renderChart();
  } catch (err) { window.showMessage && window.showMessage(err.message || '刷新失败', 'error'); }
}

// ── 节点点击 → 操作浮层 ───────────────────────────────────
function onNodeClick(params) {
  const m = params.data && params.data._meta;
  if (!m) return;
  const ev = params.event && params.event.event;
  const x = ev ? ev.clientX : window.innerWidth / 2;
  const y = ev ? ev.clientY : window.innerHeight / 2;
  openMenu(m, x, y);
}

function openMenu(node, x, y) {
  const menu = $('ftreeMenu');
  if (!menu) return;
  const items = [];
  items.push({ icon: '➕', label: '添加子分支', act: () => addBranch(node) });
  if (!node.is_root) items.push({ icon: '✏️', label: '重命名', act: () => renameNode(node) });
  items.push({ icon: '📝', label: node.has_note ? '编辑备注' : '添加备注', act: () => openEditor(node, 'note') });
  if (state.mode === 'test') {
    const mine = (node.marks || []).find((mk) => Number(mk.user_id) === Number(currentUserId()));
    items.push({ icon: '✅', label: mine ? '编辑我的测试标记' : '标记我已测', act: () => openEditor(node, 'mark') });
    if (mine) items.push({ icon: '❌', label: '取消我的标记', danger: true, act: () => removeMark(node) });
  }
  if (!node.is_root) items.push({ icon: '🗑', label: '删除分支', danger: true, act: () => deleteNode(node) });

  menu.innerHTML = `<div class="ftree-menu-title">${escapeHtml(node.name)}</div>`
    + items.map((it, i) => `<button class="ftree-menu-item${it.danger ? ' danger' : ''}" data-i="${i}"><span>${it.icon}</span>${it.label}</button>`).join('');
  menu.querySelectorAll('.ftree-menu-item').forEach((btn) => {
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
  const html = ed ? ed.innerHTML.trim() : '';
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
  closeEditor, saveEditor,
};
