// CAD 测试统计 —— 版本分组卡片视图。
// 数据：统计表(board) → 版本(version) + 自定义列(column) + 条目(item) → 记录(item×version) → 附件(CAD/截图)。
// 依赖全局：api()、token、window.OmniQAUtils、window.OmniQAPreview、window.showMessage。

import { confirmDialog, promptDialog } from '../components/dialog.js?v=20261126-zai-1';

const U = () => window.OmniQAUtils || {};
const esc = (s) => (U().escapeHtml ? U().escapeHtml(String(s ?? '')) : String(s ?? ''));
const toast = (m, t = 'success') => window.showMessage && window.showMessage(m, t);
const authHeaders = () => ({ Authorization: 'Bearer ' + (localStorage.getItem('token') || window.token || '') });
// 视频/流式访问：JWT 走 query token，<video src> 无法设置请求头。
const streamSrc = (a) => `${a.stream_url}?token=${encodeURIComponent(localStorage.getItem('token') || window.token || '')}`;
const splitAtts = (rec) => {
  const all = rec ? rec.attachments : [];
  return {
    cad: all.filter((a) => a.kind === 'cad'),
    video: all.filter((a) => a.is_video || a.kind === 'video'),
    shot: all.filter((a) => a.kind === 'screenshot' && !a.is_video),
  };
};

const state = {
  boards: [],
  boardId: null,
  board: null, // detail
  versionId: null,
  filter: 'all', // all | abnormal | normal | untested（按当前版本）
  view: 'cards', // cards | matrix
  keyword: '',
};

async function japi(url, opt) {
  const r = await api(url, opt);
  if (!r.ok) {
    let msg = '请求失败';
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  try { return await r.json(); } catch { return {}; }
}

export async function activate() {
  const root = document.getElementById('cadTestRoot');
  if (!root) return;
  bindGlobalPaste();
  try {
    state.boards = await japi('/api/cad/boards');
  } catch (e) {
    root.innerHTML = `<div class="cad-empty">${ic('i-alert', 'cad-empty-ic')}<b>加载失败</b><span>${esc(e.message)}</span></div>`;
    return;
  }
  if (!state.boardId && state.boards.length) state.boardId = state.boards[0].id;
  await loadBoard();
}

async function loadBoard() {
  if (state.boardId) {
    try {
      state.board = await japi(`/api/cad/boards/${state.boardId}`);
      if (!state.versionId || !state.board.versions.some((v) => v.id === state.versionId)) {
        state.versionId = state.board.versions[0]?.id || null;
      }
    } catch (e) {
      state.board = null;
      toast(e.message, 'error');
    }
  } else {
    state.board = null;
  }
  render();
}

// 记录状态：abnormal / normal / untested（与卡片、矩阵、筛选共用）
function recStatus(rec) {
  if (rec && rec.abnormal_count > 0) return 'abnormal';
  if (rec && rec.normal_count > 0) return 'normal';
  return 'untested';
}
const STATUS_TEXT = { abnormal: '异常', normal: '正常', untested: '未测' };
const ic = (name, cls = 'ui-ic') => `<svg class="${cls}" aria-hidden="true"><use href="#${name}"/></svg>`;
const short = (text, n) => (text.length > n ? `${text.slice(0, n)}…` : text);

function itemMatches(item, versionId) {
  const kw = (state.keyword || '').trim().toLowerCase();
  if (kw) {
    const hay = `#${item.seq ?? ''} ${item.title || ''} ${item.zentao_bug_id || ''}`.toLowerCase();
    if (!hay.includes(kw)) return false;
  }
  if (state.filter === 'all') return true;
  return recStatus(state.board.records[`${item.id}:${versionId}`]) === state.filter;
}

function render() {
  const root = document.getElementById('cadTestRoot');
  if (!root) return;
  root.innerHTML = `
    <div class="ph">
      <span class="ph-icon" aria-hidden="true"><svg><use href="#i-cad"/></svg></span><svg class="ph-mark" aria-hidden="true"><use href="#i-cad"/></svg>
      <div class="ph-text"><h2>CAD测试统计</h2><p class="page-desc">按版本记录每张图纸的测试结果，追踪异常与附件</p></div>
      <div class="ph-actions">
        <button class="secondary" onclick="window.OmniQACadTab._newBoard()">${ic('i-plus')}新建统计表</button>
      </div>
    </div>
    <div class="fb">
      <label class="fb-field fb-field--grow"><span class="fb-k">统计表</span><select id="cadBoardSelect">
          ${state.boards.map((b) => `<option value="${b.id}" ${b.id === state.boardId ? 'selected' : ''}>${esc(b.name)}</option>`).join('')}
        </select></label>
      ${state.board ? `<div class="fb-actions">
        <button class="secondary" onclick="window.OmniQACadTab._manageColumns()">${ic('i-layers')}自定义列</button>
        <button class="secondary" onclick="window.OmniQACadTab._renameBoard()">${ic('i-edit')}重命名</button>
        <button class="secondary fb-danger" onclick="window.OmniQACadTab._delBoard()">${ic('i-trash')}删除表</button>
      </div>` : ''}
    </div>
    ${state.board ? renderBoardBody() : `<div class="cad-empty">${ic('i-cad', 'cad-empty-ic')}<b>还没有统计表</b><span>新建一张统计表，再添加版本和图纸条目。</span>
      <button onclick="window.OmniQACadTab._newBoard()">${ic('i-plus')}新建统计表</button></div>`}
  `;
  const sel = document.getElementById('cadBoardSelect');
  if (sel) sel.onchange = async () => { state.boardId = Number(sel.value); state.versionId = null; await loadBoard(); };
  const kw = document.getElementById('cadKeyword');
  if (kw) {
    kw.oninput = () => { state.keyword = kw.value; renderItemsOnly(); };
  }
  hydrateScreenshots(root);
}

// 只重绘条目区（搜索时保留输入框焦点）
function renderItemsOnly() {
  const host = document.getElementById('cadItemsHost');
  if (!host || !state.board) return;
  const ver = currentVersion();
  host.innerHTML = state.view === 'matrix' ? renderMatrix() : renderCards(ver.id);
  hydrateScreenshots(host);
}

function currentVersion() {
  const b = state.board;
  return b.versions.find((v) => v.id === state.versionId) || b.versions[0];
}

function renderBoardBody() {
  const b = state.board;
  if (!b.versions.length) {
    return `<div class="cad-empty">${ic('i-build', 'cad-empty-ic')}<b>该统计表还没有版本</b><span>每个版本单独记录一轮测试结果，图纸条目在各版本间共用。</span>
      <button onclick="window.OmniQACadTab._newVersion()">${ic('i-plus')}新增版本</button></div>`;
  }
  const ver = currentVersion();
  const counts = { all: b.items.length, abnormal: 0, normal: 0, untested: 0 };
  b.items.forEach((it) => { counts[recStatus(b.records[`${it.id}:${ver.id}`])] += 1; });
  const tested = counts.abnormal + counts.normal;
  const pct = counts.all ? Math.round((tested / counts.all) * 100) : 0;
  const seg = (key, label) => `<button type="button" class="${state.filter === key ? 'is-active' : ''}" onclick="window.OmniQACadTab._setFilter('${key}')">${label}<span>${counts[key]}</span></button>`;
  return `
    <div class="cad-vbar">
      <div class="cad-vtabs" role="tablist" aria-label="测试版本">
        ${b.versions.map((v) => `<button type="button" role="tab" aria-selected="${v.id === ver.id}" class="cad-vtab${v.id === ver.id ? ' is-active' : ''}" onclick="window.OmniQACadTab._pickVersion(${v.id})" title="${esc(v.name)}">${esc(v.name)}</button>`).join('')}
      </div>
      <div class="cad-vacts">
        <button class="secondary cad-icon-btn" onclick="window.OmniQACadTab._newVersion()" title="新增版本" aria-label="新增版本">${ic('i-plus')}</button>
        <button class="secondary cad-icon-btn" onclick="window.OmniQACadTab._renameVersion(${ver.id})" title="重命名当前版本" aria-label="重命名当前版本">${ic('i-edit')}</button>
        <button class="secondary cad-icon-btn cad-icon-btn--danger" onclick="window.OmniQACadTab._delVersion(${ver.id})" title="删除当前版本" aria-label="删除当前版本">${ic('i-trash')}</button>
      </div>
    </div>
    <div class="cad-stats">
      <div class="cad-progress">
        <div class="cad-progress-top"><b>${pct}%</b><span>已测 ${tested} / ${counts.all} 个条目</span></div>
        <div class="cad-progress-bar" aria-hidden="true">
          <i class="is-normal" style="width:${counts.all ? (counts.normal / counts.all) * 100 : 0}%"></i><i class="is-abnormal" style="width:${counts.all ? (counts.abnormal / counts.all) * 100 : 0}%"></i>
        </div>
      </div>
      <div class="cad-filter" role="group" aria-label="按状态筛选">
        ${seg('all', '全部')}${seg('abnormal', '异常')}${seg('normal', '正常')}${seg('untested', '未测')}
      </div>
      <label class="fb-search cad-search">${ic('i-search')}<input id="cadKeyword" placeholder="搜索序号、图纸名、Bug" value="${esc(state.keyword || '')}"></label>
      <div class="cad-view" role="group" aria-label="视图">
        <button type="button" class="${state.view !== 'matrix' ? 'is-active' : ''}" onclick="window.OmniQACadTab._setView('cards')" title="按当前版本看卡片">${ic('i-board')}卡片</button>
        <button type="button" class="${state.view === 'matrix' ? 'is-active' : ''}" onclick="window.OmniQACadTab._setView('matrix')" title="横向对比所有版本">${ic('i-layers')}版本对比</button>
      </div>
      <button onclick="window.OmniQACadTab._newItem()">${ic('i-plus')}新增条目</button>
    </div>
    <div id="cadItemsHost">${state.view === 'matrix' ? renderMatrix() : renderCards(ver.id)}</div>`;
}

function renderCards(versionId) {
  const items = state.board.items;
  if (!items.length) return `<div class="cad-empty cad-empty--sm"><b>还没有条目</b><span>点击「新增条目」添加第一张图纸。</span></div>`;
  const shown = items.filter((it) => itemMatches(it, versionId));
  if (!shown.length) return `<div class="cad-empty cad-empty--sm"><b>没有符合条件的条目</b><span>换个状态或清空搜索试试。</span></div>`;
  return `<div class="cad-grid">${shown.map((it) => renderCard(it, versionId)).join('')}</div>`;
}

// 版本对比矩阵：行 = 条目，列 = 版本，单元格点开即填写该版本的记录
function renderMatrix() {
  const b = state.board;
  const ver = currentVersion();
  const rows = b.items.filter((it) => itemMatches(it, ver.id));
  if (!b.items.length) return `<div class="cad-empty cad-empty--sm"><b>还没有条目</b><span>点击「新增条目」添加第一张图纸。</span></div>`;
  if (!rows.length) return `<div class="cad-empty cad-empty--sm"><b>没有符合条件的条目</b><span>筛选以当前选中的版本为准。</span></div>`;
  const colSum = b.versions.map((v) => {
    let ab = 0; let ok = 0;
    b.items.forEach((it) => { const st = recStatus(b.records[`${it.id}:${v.id}`]); if (st === 'abnormal') ab += 1; if (st === 'normal') ok += 1; });
    return { ab, ok };
  });
  return `
    <div class="cad-matrix-wrap"><table class="cad-matrix">
      <thead><tr><th class="cad-m-item">条目</th>${b.versions.map((v, i) => `<th class="${v.id === ver.id ? 'is-cur' : ''}" title="${esc(v.name)}">
        <span class="cad-m-vname">${esc(short(v.name, 22))}</span>
        <span class="cad-m-vsum"><em class="is-normal">${colSum[i].ok}</em><em class="is-abnormal">${colSum[i].ab}</em></span></th>`).join('')}</tr></thead>
      <tbody>${rows.map((it) => `<tr>
        <th class="cad-m-item"><span class="cad-seq">#${it.seq ?? '-'}</span>${esc(it.title || '未命名条目')}${it.zentao_bug_id ? `<span class="cad-bug">Bug ${it.zentao_bug_id}</span>` : ''}</th>
        ${b.versions.map((v) => {
          const rec = b.records[`${it.id}:${v.id}`];
          const st = recStatus(rec);
          const tip = rec && rec.description ? rec.description : STATUS_TEXT[st];
          return `<td class="${v.id === ver.id ? 'is-cur' : ''}"><button type="button" class="cad-cell cad-cell--${st}" title="${esc(tip)}" onclick="window.OmniQACadTab._editRecord(${it.id}, ${v.id})">${STATUS_TEXT[st]}${rec && (rec.attachments || []).length ? ic('i-file') : ''}</button></td>`;
        }).join('')}
      </tr>`).join('')}</tbody>
    </table></div>
    <p class="cad-hint">点单元格可直接填写该版本的记录；表头数字为该版本的正常 / 异常条目数。</p>`;
}

function renderCard(item, versionId) {
  const rec = state.board.records[`${item.id}:${versionId}`];
  const bug = item.bug;
  const st = recStatus(rec);
  const { video: videos, shot: shots } = splitAtts(rec);
  const customs = (state.board.columns || [])
    .map((c) => {
      const val = rec && rec.custom_values ? rec.custom_values[String(c.id)] : '';
      return val ? `<div><dt>${esc(c.name)}</dt><dd>${esc(val)}</dd></div>` : '';
    })
    .join('');
  const shared = [
    ...(item.cad_folders || []).map((fd) => `<button type="button" class="cad-file" onclick="window.OmniQACadTab._viewFolder(${fd.id},${item.id},${versionId})" title="查看文件夹：${esc(fd.name)}（${fd.file_count} 个文件）">📁 ${esc(short(fd.name, 14))}<small>${fd.file_count}</small></button>`),
    ...(item.cad_files || []).map((f) => `<button type="button" class="cad-file" onclick="window.OmniQACadTab._dlcad(${f.id})" title="下载：${esc(f.original_name)}">${ic('i-download')}${esc(short(f.original_name, 18))}</button>`),
  ].join('');
  const bugChip = item.zentao_bug_id
    ? `<span class="cad-bug-row"><span class="cad-bug">Bug ${item.zentao_bug_id}</span>
         ${U().renderPreviewBtn ? U().renderPreviewBtn('bug', item.zentao_bug_id) : ''}
         ${bug && bug.zentao_bug_url ? `<a class="qa-ext-link" href="${esc(bug.zentao_bug_url)}" target="_blank" rel="noopener" title="${esc(bug.zentao_bug_title || '')}">跳转</a>` : ''}</span>`
    : '';

  return `
    <article class="cad-card cad-card--${st}">
      <header class="cad-card-h">
        <span class="cad-seq">#${item.seq ?? '-'}</span>
        <b class="cad-title" title="${esc(item.title || '')}">${esc(item.title || '未命名条目')}</b>
        <span class="cad-st cad-st--${st}">${STATUS_TEXT[st]}</span>
      </header>
      ${bugChip}
      ${shared ? `<div class="cad-shared"><span class="cad-shared-k" title="CAD 文件挂在条目上，各测试版本通用">共享 CAD</span>${shared}</div>` : ''}
      ${rec && rec.description ? `<p class="cad-desc">${esc(rec.description)}</p>` : ''}
      ${(shots.length || videos.length) ? `<div class="cad-media">
        ${shots.map((a) => `<img data-cad-src="${a.download_url}" onclick="window.OmniQACadTab._viewShot(${a.id})" alt="${esc(a.original_name)}" title="查看图片：${esc(a.original_name)}">`).join('')}
        ${videos.map((a) => `<button type="button" class="cad-file" onclick="window.OmniQACadTab._viewVideo(${a.id})" title="${esc(a.original_name)}">${ic('i-play')}视频</button>`).join('')}
      </div>` : ''}
      ${customs ? `<dl class="cad-custom">${customs}</dl>` : ''}
      <footer class="cad-card-f">
        <button type="button" class="cad-ghost" onclick="window.OmniQACadTab._editItem(${item.id})">${ic('i-wrench')}编辑条目</button>
        <button type="button" class="cad-fill" onclick="window.OmniQACadTab._editRecord(${item.id}, ${versionId})">${ic('i-edit')}${rec ? '编辑记录' : '填写记录'}</button>
      </footer>
    </article>`;
}

function _pickVersion(id) { state.versionId = Number(id); render(); }
function _setFilter(key) { state.filter = key; render(); }
function _setView(view) { state.view = view; render(); }

// 截图需要带 JWT 拉取，转成 objectURL 注入 <img>。
function hydrateScreenshots(container) {
  container.querySelectorAll('img[data-cad-src]').forEach(async (img) => {
    if (img.dataset.loaded) return;
    img.dataset.loaded = '1';
    try {
      const r = await fetch(img.getAttribute('data-cad-src'), { headers: authHeaders() });
      if (!r.ok) return;
      img.src = URL.createObjectURL(await r.blob());
    } catch {}
  });
}

// ---------------------------------------------------------------- board ops
async function _newBoard() {
  const name = await promptDialog({ icon: 'i-cad', title: '新建统计表', label: '统计表名称', placeholder: '如：40311版本测试统计表', okText: '创建' });
  if (!name) return;
  try {
    const res = await japi('/api/cad/boards', { method: 'POST', body: { name: name.trim() } });
    state.boardId = res.id; state.versionId = null;
    state.boards = await japi('/api/cad/boards');
    await loadBoard();
    toast('已创建统计表');
  } catch (e) { toast(e.message, 'error'); }
}
async function _renameBoard() {
  const name = await promptDialog({ icon: 'i-cad', title: '重命名统计表', label: '统计表名称', value: state.board?.name || '', okText: '保存' });
  if (!name) return;
  try { await japi(`/api/cad/boards/${state.boardId}`, { method: 'PUT', body: { name: name.trim() } });
    state.boards = await japi('/api/cad/boards'); await loadBoard(); toast('已重命名'); } catch (e) { toast(e.message, 'error'); }
}
async function _delBoard() {
  if (!(await confirmDialog({ danger: true, title: '删除统计表', sub: state.board?.name || '', message: '该表下所有版本、条目、记录和附件会一并删除。', okText: '删除统计表' }))) return;
  try { await japi(`/api/cad/boards/${state.boardId}`, { method: 'DELETE' });
    state.boardId = null; state.board = null; state.versionId = null;
    state.boards = await japi('/api/cad/boards');
    if (state.boards.length) state.boardId = state.boards[0].id;
    await loadBoard(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// -------------------------------------------------------------- version ops
async function _newVersion() {
  const name = await promptDialog({ icon: 'i-build', title: '新增版本', label: '版本名称', placeholder: '如：4.0.3.11.260603(40311022)版本测试', okText: '新增' });
  if (!name) return;
  try { const res = await japi(`/api/cad/boards/${state.boardId}/versions`, { method: 'POST', body: { name: name.trim() } });
    state.versionId = res.id; await loadBoard(); toast('已新增版本'); } catch (e) { toast(e.message, 'error'); }
}
async function _renameVersion(id) {
  const cur = state.board.versions.find((v) => v.id === id);
  const name = await promptDialog({ icon: 'i-build', title: '重命名版本', label: '版本名称', value: cur?.name || '', okText: '保存' });
  if (!name) return;
  try { await japi(`/api/cad/versions/${id}`, { method: 'PUT', body: { name: name.trim() } }); await loadBoard(); toast('已重命名'); } catch (e) { toast(e.message, 'error'); }
}
async function _delVersion(id) {
  if (!(await confirmDialog({ danger: true, title: '删除版本', message: '该版本下的记录与附件会一并删除。', okText: '删除版本' }))) return;
  try { await japi(`/api/cad/versions/${id}`, { method: 'DELETE' }); state.versionId = null; await loadBoard(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// --------------------------------------------------------------- column ops
function _manageColumns() {
  const cols = state.board.columns || [];
  const body = `
    <div style="display:flex; flex-direction:column; gap:8px;">
      ${cols.length ? cols.map((c) => `
        <div class="row" data-col="${c.id}" style="gap:8px; align-items:center;">
          <input value="${esc(c.name)}" data-colname="${c.id}" style="flex:1;">
          <button class="secondary" onclick="window.OmniQACadTab._renameColumn(${c.id})">改名</button>
          <button class="secondary" style="color:#dc2626;" onclick="window.OmniQACadTab._delColumn(${c.id})">删除</button>
        </div>`).join('') : '<div class="muted">还没有自定义列。</div>'}
    </div>
    <div class="row" style="margin-top:12px; gap:8px;">
      <input id="cadNewColName" placeholder="新列名称（如：华测-测地通）" style="flex:1;">
      <button onclick="window.OmniQACadTab._addColumn()">+ 添加列</button>
    </div>`;
  openModal('自定义备注列', body);
}
async function _addColumn() {
  const inp = document.getElementById('cadNewColName');
  const name = inp && inp.value.trim();
  if (!name) return;
  try { await japi(`/api/cad/boards/${state.boardId}/columns`, { method: 'POST', body: { name } }); await loadBoard(); _manageColumns(); toast('已添加列'); } catch (e) { toast(e.message, 'error'); }
}
async function _renameColumn(id) {
  const inp = document.querySelector(`input[data-colname="${id}"]`);
  const name = inp && inp.value.trim();
  if (!name) return;
  try { await japi(`/api/cad/columns/${id}`, { method: 'PUT', body: { name } }); await loadBoard(); toast('已改名'); } catch (e) { toast(e.message, 'error'); }
}
async function _delColumn(id) {
  if (!(await confirmDialog({ danger: true, title: '删除自定义列', message: '已填写的对应值将不再显示。', okText: '删除列' }))) return;
  try { await japi(`/api/cad/columns/${id}`, { method: 'DELETE' }); await loadBoard(); _manageColumns(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// ----------------------------------------------------------------- item ops
function _newItem() { openItemModal(null); }
function _editItem(id) { openItemModal(state.board.items.find((i) => i.id === id)); }
function openItemModal(item) {
  const body = `
    <div class="cad-form">
      <div class="cad-form-row">
        <label class="cad-field cad-field--seq"><span>序号</span><input id="cadItemSeq" type="number" value="${item?.seq ?? ''}" placeholder="自动顺延"></label>
        <label class="cad-field"><span>图纸名 / 标题</span><input id="cadItemTitle" value="${esc(item?.title || '')}" placeholder="如：总平面图_A1.dwg"></label>
      </div>
      <label class="cad-field"><span>关联禅道 Bug ID <em>可选</em></span><input id="cadItemBug" type="number" value="${item?.zentao_bug_id ?? ''}" placeholder="如 29614"></label>
    </div>
    <div class="cad-modal-f">
      ${item ? `<button class="secondary cad-danger" onclick="window.OmniQACadTab._delItem(${item.id})">${ic('i-trash')}删除条目</button>` : '<span></span>'}
      <span class="cad-modal-f-r"><button class="secondary" onclick="window.OmniQACadTab._close()">取消</button><button onclick="window.OmniQACadTab._saveItem(${item ? item.id : 'null'})">保存</button></span>
    </div>`;
  openModal(item ? '编辑条目' : '新增条目', body);
}
async function _saveItem(id) {
  const seq = document.getElementById('cadItemSeq').value;
  const title = document.getElementById('cadItemTitle').value;
  const bug = document.getElementById('cadItemBug').value;
  const payload = { seq: seq === '' ? null : Number(seq), title: title || null, zentao_bug_id: bug === '' ? null : Number(bug) };
  try {
    if (id && id !== 'null') await japi(`/api/cad/items/${id}`, { method: 'PUT', body: payload });
    else await japi(`/api/cad/boards/${state.boardId}/items`, { method: 'POST', body: payload });
    closeModal(); await loadBoard(); toast('已保存');
  } catch (e) { toast(e.message, 'error'); }
}
async function _delItem(id) {
  if (!(await confirmDialog({ danger: true, title: '删除条目', message: '该条目在所有版本下的记录与附件会一并删除。', okText: '删除条目' }))) return;
  try { await japi(`/api/cad/items/${id}`, { method: 'DELETE' }); closeModal(); await loadBoard(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// --------------------------------------------------------------- record edit
function _editRecord(itemId, versionId) {
  const item = state.board.items.find((i) => i.id === itemId);
  const rec = state.board.records[`${itemId}:${versionId}`];
  const cols = state.board.columns || [];
  const { cad: cadFiles, video: videos, shot: shots } = splitAtts(rec);
  const body = `
    <div class="cad-form">
      <div class="cad-rec-meta"><span class="cad-seq">#${item?.seq ?? '-'}</span><b>${esc(item?.title || '未命名条目')}</b>${item?.zentao_bug_id ? `<span class="cad-bug">Bug ${item.zentao_bug_id}</span>` : ''}
        <span class="cad-rec-ver">${esc(state.board.versions.find((v) => v.id === versionId)?.name || '')}</span></div>
      <div class="cad-field"><span>测试结果</span>
        <div class="cad-pick" role="group" aria-label="测试结果">
          <label class="cad-pick-opt cad-pick-opt--normal"><input type="checkbox" id="cadRecNormal" ${rec && rec.normal_count > 0 ? 'checked' : ''} onchange="if(this.checked)document.getElementById('cadRecAbnormal').checked=false">${ic('i-check')}正常</label>
          <label class="cad-pick-opt cad-pick-opt--abnormal"><input type="checkbox" id="cadRecAbnormal" ${rec && rec.abnormal_count > 0 ? 'checked' : ''} onchange="if(this.checked)document.getElementById('cadRecNormal').checked=false">${ic('i-alert')}异常</label>
          <small>都不选 = 未测</small>
        </div>
      </div>
      <label class="cad-field"><span>问题说明 / 异常说明</span>
        <textarea id="cadRecDesc" rows="3" placeholder="描述现象、复现方式或对比结论">${esc(rec ? rec.description || '' : '')}</textarea>
      </label>
      ${cols.length ? `<div class="cad-form-cols">
        ${cols.map((c) => `<label class="cad-field"><span>${esc(c.name)}</span><input data-cv="${c.id}" value="${esc(rec && rec.custom_values ? (rec.custom_values[String(c.id)] || '') : '')}"></label>`).join('')}
      </div>` : ''}
      <section class="cad-att cad-att--cad">
        <div class="cad-att-h">
          <b>共享 CAD 文件 / 文件夹</b>
          <span class="cad-att-acts">
            <label class="cad-up">${ic('i-upload')}上传 CAD
              <input type="file" accept=".dwg,.dxf,.dwf,.dgn,.dwt" hidden onchange="window.OmniQACadTab._upload(${itemId},${versionId},'cad',this)">
            </label>
            <label class="cad-up" title="上传一整个文件夹（含外部参照 / 整组图纸）">📁 上传文件夹
              <input type="file" webkitdirectory directory multiple hidden onchange="window.OmniQACadTab._uploadFolder(${itemId},${versionId},this)">
            </label>
          </span>
        </div>
        <p class="cad-att-tip">挂在条目上，所有测试版本通用，不用每个版本重复上传；带外部参照或整组图纸时可直接上传整个文件夹。</p>
        <div class="cad-chips">
          ${(item?.cad_folders || []).map((fd) => `<span class="cad-chip">
            <a href="javascript:void(0)" onclick="window.OmniQACadTab._viewFolder(${fd.id},${itemId},${versionId})" title="查看文件夹内容">📁 ${esc(fd.name)} <small>${fd.file_count}</small></a>
            <button type="button" onclick="window.OmniQACadTab._dlFolder(${fd.id})" title="打包下载整个文件夹" aria-label="下载文件夹">${ic('i-download')}</button>
            <button type="button" class="is-del" onclick="window.OmniQACadTab._delFolder(${fd.id},${itemId},${versionId})" title="删除整个文件夹" aria-label="删除文件夹">×</button>
          </span>`).join('')}
          ${(item?.cad_files || []).map((f) => `<span class="cad-chip">
            <a href="javascript:void(0)" onclick="window.OmniQACadTab._dlcad(${f.id})" title="下载">📐 ${esc(f.original_name)}</a>
            <button type="button" class="is-del" onclick="window.OmniQACadTab._delCad(${f.id},${itemId},${versionId})" title="删除" aria-label="删除文件">×</button>
          </span>`).join('')}
          ${(item?.cad_folders || []).length || (item?.cad_files || []).length ? '' : '<span class="cad-none">还没有上传</span>'}
        </div>
      </section>
      <section class="cad-att">
        <div class="cad-att-h">
          <b>截图 <em>本版本</em></b>
          <span class="cad-att-acts">
            <button type="button" class="cad-up" onclick="window.OmniQACadTab._pasteShot(${itemId},${versionId})">📋 粘贴截图</button>
            <label class="cad-up">${ic('i-upload')}上传截图
              <input type="file" accept="image/*" hidden onchange="window.OmniQACadTab._upload(${itemId},${versionId},'screenshot',this)">
            </label>
          </span>
        </div>
        <p class="cad-att-tip">截图工具复制图片后，在弹窗里直接按 <kbd>Ctrl</kbd>+<kbd>V</kbd> 即可上传。</p>
        <div class="cad-thumbs">
          ${shots.map((a) => `<span class="cad-thumb">
            <img data-cad-src="${a.download_url}" alt="${esc(a.original_name)}" onclick="window.OmniQACadTab._viewShot(${a.id})">
            <button type="button" onclick="window.OmniQACadTab._delAtt(${a.id},${itemId},${versionId})" title="删除" aria-label="删除截图">×</button>
          </span>`).join('') || '<span class="cad-none">暂无截图</span>'}
        </div>
      </section>
      <section class="cad-att">
        <div class="cad-att-h">
          <b>视频 <em>本版本 · 支持在线播放</em></b>
          <span class="cad-att-acts">
            <label class="cad-up">${ic('i-upload')}上传视频
              <input type="file" accept="video/*" hidden onchange="window.OmniQACadTab._upload(${itemId},${versionId},'video',this)">
            </label>
          </span>
        </div>
        <div class="cad-thumbs">
          ${videos.map((a) => `<span class="cad-thumb cad-thumb--video">
            <video src="${streamSrc(a)}" controls preload="metadata" playsinline></video>
            <button type="button" onclick="window.OmniQACadTab._delAtt(${a.id},${itemId},${versionId})" title="删除" aria-label="删除视频">×</button>
          </span>`).join('') || '<span class="cad-none">暂无视频</span>'}
        </div>
      </section>
    </div>
    <div class="cad-modal-f">
      <span></span>
      <span class="cad-modal-f-r"><button class="secondary" onclick="window.OmniQACadTab._close()">取消</button><button onclick="window.OmniQACadTab._saveRecord(${itemId},${versionId})">保存记录</button></span>
    </div>`;
  openModal(rec ? '编辑记录' : '填写记录', body, { wide: true });
  // 记录当前粘贴上下文：弹窗内 Ctrl+V 将图片作为该条目/版本的截图上传。
  const modal = document.getElementById('cadModal');
  if (modal) modal._cadPasteCtx = { itemId, versionId };
  hydrateScreenshots(document.getElementById('cadModalBody'));
}
function attChip(a) {
  return `<span class="row" style="gap:4px; align-items:center; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:3px 8px;">
    <a class="qa-ext-link" href="javascript:void(0)" onclick="window.OmniQACadTab._download(${a.id})">📐 ${esc(a.original_name)}</a>
    <button onclick="window.OmniQACadTab._delAtt(${a.id})" title="删除" style="border:none;background:none;color:#dc2626;cursor:pointer;">×</button>
  </span>`;
}
async function _saveRecord(itemId, versionId) {
  const normal = document.getElementById('cadRecNormal').checked ? 1 : 0;
  const abnormal = document.getElementById('cadRecAbnormal').checked ? 1 : 0;
  const desc = document.getElementById('cadRecDesc').value;
  const custom = {};
  document.querySelectorAll('#cadModalBody input[data-cv]').forEach((inp) => {
    const v = inp.value.trim();
    if (v) custom[inp.getAttribute('data-cv')] = v;
  });
  try {
    await japi('/api/cad/records', { method: 'POST', body: { item_id: itemId, version_id: versionId, normal_count: normal, abnormal_count: abnormal, description: desc || null, custom_values: custom } });
    closeModal(); await loadBoard(); toast('已保存');
  } catch (e) { toast(e.message, 'error'); }
}
async function _upload(itemId, versionId, kind, inputEl) {
  const file = inputEl.files && inputEl.files[0];
  if (!file) return;
  inputEl.value = ''; // 允许再次选择同一文件
  await uploadCadFile(itemId, versionId, kind, file);
}
// 上传一整个文件夹（含外部参照/整组图纸）：先建文件夹，再逐个文件带相对路径上传。
async function _uploadFolder(itemId, versionId, inputEl) {
  const files = Array.from(inputEl.files || []);
  inputEl.value = '';
  if (!files.length) return;
  // 文件夹名取自首个文件相对路径的顶层目录名。
  const firstRel = files[0].webkitRelativePath || files[0].name;
  const folderName = (firstRel.split('/')[0] || '上传文件夹').trim();
  if (!(await confirmDialog({ icon: 'i-upload', title: '上传文件夹', sub: folderName, message: `将把文件夹中的 ${files.length} 个文件作为一组归档上传。`, okText: '开始上传' }))) return;

  const prog = createProgressBar(`📁 ${folderName} 0/${files.length}`);
  try {
    const folder = await japi(`/api/cad/items/${itemId}/cad-folder`, { method: 'POST', body: { name: folderName } });
    for (let i = 0; i < files.length; i += 1) {
      const f = files[i];
      // 相对路径去掉顶层目录名，仅保留文件夹内层级（含文件名）。
      const rel = (f.webkitRelativePath || f.name).split('/').slice(1).join('/') || f.name;
      prog.setLabel(`📁 ${folderName} ${i + 1}/${files.length} · ${f.name}`);
      const fd = new FormData();
      fd.append('file', f, f.name || 'upload');
      fd.append('rel_path', rel);
      // 总进度 = 已完成文件 + 当前文件分数，再除以总数。
      await xhrUpload(`/api/cad/cad-folders/${folder.id}/file`, fd, (r) => prog.update((i + r) / files.length));
    }
    prog.done();
    await loadBoard();
    _editRecord(itemId, versionId);
    toast(`文件夹「${folderName}」上传成功（${files.length} 个文件）`);
  } catch (e) {
    prog.fail();
    toast(e.message || '文件夹上传失败', 'error');
    await loadBoard();
    _editRecord(itemId, versionId);
  }
}
// 带上传进度的 POST（fetch 无法读取上传进度，故用 XHR）。
function xhrUpload(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    const h = authHeaders();
    Object.keys(h).forEach((k) => xhr.setRequestHeader(k, h[k]));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        let data = {}; try { data = JSON.parse(xhr.responseText); } catch {}
        resolve(data);
      } else {
        let m = '上传失败';
        try { m = JSON.parse(xhr.responseText).detail || m; } catch {}
        if (xhr.status === 413) m = '文件过大，被服务器拒绝（413）。请联系管理员调大上传限制。';
        reject(new Error(m));
      }
    };
    xhr.onerror = () => reject(new Error('网络错误，上传中断'));
    xhr.onabort = () => reject(new Error('上传已取消'));
    xhr.send(formData);
  });
}
// 右下角浮动进度卡片：支持多文件并行各占一行，完成/失败后自动消失。
function createProgressBar(label) {
  let host = document.getElementById('cadUploadProgressHost');
  if (!host) {
    host = document.createElement('div');
    host.id = 'cadUploadProgressHost';
    host.style.cssText = 'position:fixed; right:18px; bottom:18px; z-index:12000; display:flex; flex-direction:column; gap:8px; width:280px;';
    document.body.appendChild(host);
  }
  const item = document.createElement('div');
  item.style.cssText = 'background:#fff; border:1px solid #e2e8f0; border-radius:10px; box-shadow:0 6px 20px rgba(0,0,0,.12); padding:10px 12px; font-size:12px;';
  item.innerHTML = `
    <div style="display:flex; justify-content:space-between; gap:8px; color:#334155;">
      <span data-label style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">⬆ ${esc(label)}</span>
      <span data-pct style="color:#2563eb; font-weight:600; flex-shrink:0;">0%</span>
    </div>
    <div style="height:6px; background:#eef2f7; border-radius:999px; margin-top:6px; overflow:hidden;">
      <div data-bar style="height:100%; width:0%; background:linear-gradient(90deg,#3b82f6,#6366f1); transition:width .15s ease;"></div>
    </div>`;
  host.appendChild(item);
  const bar = item.querySelector('[data-bar]');
  const pct = item.querySelector('[data-pct]');
  const labelEl = item.querySelector('[data-label]');
  const remove = (delay) => setTimeout(() => { item.remove(); if (host && !host.children.length) host.remove(); }, delay);
  return {
    update(r) { const p = Math.round(r * 100); bar.style.width = p + '%'; pct.textContent = p === 100 ? '处理中…' : p + '%'; },
    setLabel(text) { if (labelEl) labelEl.textContent = '⬆ ' + text; },
    done() { pct.textContent = '完成'; pct.style.color = '#16a34a'; bar.style.background = '#16a34a'; bar.style.width = '100%'; remove(1200); },
    fail() { pct.textContent = '失败'; pct.style.color = '#dc2626'; bar.style.background = '#dc2626'; remove(2500); },
  };
}
// 文件上传 / 粘贴 / 剪贴板按钮三条路径共用：接收一个 File/Blob 直接上传，带进度条。
async function uploadCadFile(itemId, versionId, kind, file) {
  const fd = new FormData();
  fd.append('file', file, file.name || 'upload');
  // CAD 文件挂在条目上（跨版本共享）；截图/视频按版本记录存储。
  const url = kind === 'cad'
    ? `/api/cad/items/${itemId}/cad-file`
    : `/api/cad/records/attachment?item_id=${itemId}&version_id=${versionId}&kind=${kind}`;
  const label = file.name || (kind === 'cad' ? 'CAD 文件' : kind === 'video' ? '视频' : '截图');
  const prog = createProgressBar(label);
  try {
    await xhrUpload(url, fd, (r) => prog.update(r));
    prog.done();
    await loadBoard();
    _editRecord(itemId, versionId); // 重新打开刷新附件区
    toast('上传成功');
  } catch (e) { prog.fail(); toast(e.message, 'error'); }
}
// 把剪贴板里的图片 Blob 包成带扩展名的 File（后端按 content_type/后缀识别为截图）后上传。
function uploadPastedImage(itemId, versionId, blob) {
  const type = blob.type || 'image/png';
  const ext = (type.split('/')[1] || 'png').toLowerCase().replace('jpeg', 'jpg').replace('svg+xml', 'svg');
  const file = new File([blob], `粘贴截图-${Date.now()}.${ext}`, { type });
  return uploadCadFile(itemId, versionId, 'screenshot', file);
}
// 点击「📋 粘贴截图」：用 Async Clipboard API 主动读取剪贴板图片（需 HTTPS/localhost）。
async function _pasteShot(itemId, versionId) {
  try {
    if (!navigator.clipboard || !navigator.clipboard.read) {
      toast('当前浏览器不支持点击读取剪贴板，请在弹窗内直接按 Ctrl+V', 'error');
      return;
    }
    const items = await navigator.clipboard.read();
    for (const it of items) {
      const type = it.types.find((t) => t.startsWith('image/'));
      if (type) { await uploadPastedImage(itemId, versionId, await it.getType(type)); return; }
    }
    toast('剪贴板里没有图片，请先用截图工具复制图片', 'error');
  } catch (e) {
    toast('读取剪贴板失败，请改用 Ctrl+V 粘贴', 'error');
  }
}
// 全局监听一次 paste：仅当「填写记录」弹窗打开且剪贴板含图片时拦截并上传。
let _cadPasteBound = false;
function bindGlobalPaste() {
  if (_cadPasteBound) return;
  _cadPasteBound = true;
  document.addEventListener('paste', (e) => {
    const modal = document.getElementById('cadModal');
    const ctx = modal && modal._cadPasteCtx;
    if (!ctx) return;
    const data = e.clipboardData;
    if (!data) return;
    const imgItem = Array.from(data.items || []).find((it) => it.kind === 'file' && it.type.startsWith('image/'));
    if (!imgItem) return; // 纯文本粘贴不拦截，保持 textarea 正常粘贴
    e.preventDefault();
    const blob = imgItem.getAsFile();
    if (blob) uploadPastedImage(ctx.itemId, ctx.versionId, blob);
  });
}
async function _dlcad(fileId) {
  try {
    const r = await fetch(`/api/cad/cad-files/${fileId}/download`, { headers: authHeaders() });
    if (!r.ok) throw new Error('下载失败');
    const blob = await r.blob();
    const dispo = r.headers.get('Content-Disposition') || '';
    const m = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(dispo);
    const name = m ? decodeURIComponent(m[1]) : `cad_${fileId}`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } catch (e) { toast(e.message, 'error'); }
}
async function _delCad(fileId, itemId, versionId) {
  if (!(await confirmDialog({ danger: true, title: '删除共享 CAD 文件', message: '删除后该条目的所有版本都不再显示它。', okText: '删除' }))) return;
  try {
    await japi(`/api/cad/cad-files/${fileId}`, { method: 'DELETE' });
    await loadBoard();
    if (itemId != null && versionId != null) _editRecord(itemId, versionId);
    toast('已删除');
  } catch (e) { toast(e.message, 'error'); }
}
// 在 state 中按文件夹 id 找到序列化好的文件夹数据。
function findFolder(folderId) {
  for (const it of (state.board?.items || [])) {
    const fd = (it.cad_folders || []).find((x) => x.id === folderId);
    if (fd) return { folder: fd, item: it };
  }
  return null;
}
function fmtSize(bytes) {
  const b = Number(bytes || 0);
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}
// 查看文件夹内容：列出文件，支持单独下载与整包下载。editable 时可删除文件夹/单文件。
function _viewFolder(folderId, itemId, versionId) {
  const found = findFolder(folderId);
  if (!found) { toast('文件夹不存在', 'error'); return; }
  const { folder } = found;
  const editable = itemId != null && versionId != null;
  const fileRows = (folder.files || []).map((f) => `
    <div class="row" style="justify-content:space-between; align-items:center; gap:8px; padding:6px 8px; border:1px solid #e2e8f0; border-radius:8px; background:#fff;">
      <a class="qa-ext-link" href="javascript:void(0)" onclick="window.OmniQACadTab._dlcad(${f.id})" title="下载 ${esc(f.rel_path)}" style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">📄 ${esc(f.rel_path)}</a>
      <span class="row" style="gap:8px; align-items:center; flex-shrink:0;">
        <span class="muted" style="font-size:11px;">${fmtSize(f.file_size)}</span>
        <button class="secondary" style="padding:2px 8px; font-size:12px;" onclick="window.OmniQACadTab._dlcad(${f.id})">⬇</button>
        ${editable ? `<button title="删除该文件" style="border:none;background:none;color:#dc2626;cursor:pointer;" onclick="window.OmniQACadTab._delFolderFile(${f.id},${folderId},${itemId},${versionId})">×</button>` : ''}
      </span>
    </div>`).join('') || '<div class="muted" style="font-size:12px;">该文件夹暂无文件</div>';
  const body = `
    <div style="display:flex; flex-direction:column; gap:10px;">
      <div class="row" style="justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
        <div class="muted" style="font-size:12px;">📁 ${esc(folder.name)} · 共 ${folder.file_count} 个文件 · ${fmtSize(folder.total_size)}</div>
        <button onclick="window.OmniQACadTab._dlFolder(${folderId})">⬇ 下载整个文件夹(.zip)</button>
      </div>
      <div style="display:flex; flex-direction:column; gap:6px; max-height:50vh; overflow:auto;">${fileRows}</div>
    </div>`;
  openModal(`文件夹：${folder.name}`, body);
}
async function _dlFolder(folderId) {
  const prog = createProgressBar('打包下载中…');
  try {
    const r = await fetch(`/api/cad/cad-folders/${folderId}/download`, { headers: authHeaders() });
    if (!r.ok) { let m = '下载失败'; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
    const blob = await r.blob();
    const dispo = r.headers.get('Content-Disposition') || '';
    const m = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(dispo);
    const name = m ? decodeURIComponent(m[1]) : `cad_folder_${folderId}.zip`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    prog.done();
  } catch (e) { prog.fail(); toast(e.message, 'error'); }
}
async function _delFolder(folderId, itemId, versionId) {
  const found = findFolder(folderId);
  if (!(await confirmDialog({ danger: true, title: '删除文件夹', sub: found?.folder?.name || '', message: '文件夹内所有文件会一并删除，该条目各版本都不再显示。', okText: '删除文件夹' }))) return;
  try {
    await japi(`/api/cad/cad-folders/${folderId}`, { method: 'DELETE' });
    await loadBoard();
    if (itemId != null && versionId != null) _editRecord(itemId, versionId);
    else closeModal();
    toast('已删除文件夹');
  } catch (e) { toast(e.message, 'error'); }
}
async function _delFolderFile(fileId, folderId, itemId, versionId) {
  if (!(await confirmDialog({ danger: true, title: '删除文件', message: '从该文件夹中删除这个文件。', okText: '删除' }))) return;
  try {
    await japi(`/api/cad/cad-files/${fileId}`, { method: 'DELETE' });
    await loadBoard();
    // 刷新文件夹内容弹窗（若文件夹已空则回到记录弹窗）。
    const found = findFolder(folderId);
    if (found && found.folder.file_count > 0) _viewFolder(folderId, itemId, versionId);
    else if (itemId != null && versionId != null) _editRecord(itemId, versionId);
    else closeModal();
    toast('已删除');
  } catch (e) { toast(e.message, 'error'); }
}
async function _delAtt(attId, itemId, versionId) {
  if (!(await confirmDialog({ danger: true, title: '删除附件', message: '附件删除后无法恢复。', okText: '删除' }))) return;
  try {
    await japi(`/api/cad/attachments/${attId}`, { method: 'DELETE' });
    await loadBoard();
    if (itemId != null && versionId != null) _editRecord(itemId, versionId);
    else closeModal();
    toast('已删除');
  } catch (e) { toast(e.message, 'error'); }
}
async function _download(attId) {
  try {
    const r = await fetch(`/api/cad/attachments/${attId}/download`, { headers: authHeaders() });
    if (!r.ok) throw new Error('下载失败');
    const blob = await r.blob();
    const dispo = r.headers.get('Content-Disposition') || '';
    const m = /filename\*?=(?:UTF-8'')?\"?([^\";]+)/i.exec(dispo);
    const name = m ? decodeURIComponent(m[1]) : `cad_${attId}`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } catch (e) { toast(e.message, 'error'); }
}
async function _viewShot(attId) {
  try {
    const r = await fetch(`/api/cad/attachments/${attId}/download`, { headers: authHeaders() });
    if (!r.ok) return;
    const url = URL.createObjectURL(await r.blob());
    const ov = document.createElement('div');
    ov.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,.8); z-index:11000; display:flex; align-items:center; justify-content:center; cursor:zoom-out;';
    ov.onclick = () => { ov.remove(); URL.revokeObjectURL(url); };
    ov.innerHTML = `<img src="${url}" style="max-width:94vw; max-height:94vh; border-radius:8px;">`;
    document.body.appendChild(ov);
  } catch {}
}
function _viewVideo(attId) {
  // 视频走流式端点（Range），不预下整段；点击空白处关闭，点击播放器本身不关闭。
  const token = encodeURIComponent(localStorage.getItem('token') || window.token || '');
  const url = `/api/cad/attachments/${attId}/stream?token=${token}`;
  const ov = document.createElement('div');
  ov.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,.85); z-index:11000; display:flex; align-items:center; justify-content:center; cursor:zoom-out;';
  ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  ov.innerHTML = `<video src="${url}" controls autoplay playsinline style="max-width:94vw; max-height:94vh; border-radius:8px; background:#000;"></video>`;
  document.body.appendChild(ov);
}

// ------------------------------------------------------------------- modal
let cadEscBound = false;
function openModal(title, bodyHtml, { wide = false } = {}) {
  closeModal();
  const m = document.createElement('div');
  m.id = 'cadModal';
  m.className = 'cad-modal';
  m.setAttribute('role', 'dialog');
  m.setAttribute('aria-modal', 'true');
  m.setAttribute('aria-labelledby', 'cadModalTitle');
  m.onclick = (e) => { if (e.target === m) closeModal(); };
  m.innerHTML = `
    <div class="cad-modal-panel${wide ? ' cad-modal-panel--wide' : ''}">
      <header class="cad-modal-h">
        <h3 id="cadModalTitle">${esc(title)}</h3>
        <button type="button" class="cad-x" onclick="window.OmniQACadTab._close()" aria-label="关闭">×</button>
      </header>
      <div id="cadModalBody" class="cad-modal-body">${bodyHtml}</div>
    </div>`;
  document.body.appendChild(m);
  if (!cadEscBound) {
    cadEscBound = true;
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape' || !document.getElementById('cadModal')) return;
      // 自建确认框 / 图片预览浮层在上面时先关它们
      const dlg = document.getElementById('taskOpDialog');
      if (dlg && !dlg.classList.contains('hidden')) return;
      closeModal();
    });
  }
}
function closeModal() { const m = document.getElementById('cadModal'); if (m) m.remove(); }

window.OmniQACadTab = {
  activate,
  _newBoard, _renameBoard, _delBoard,
  _newVersion, _renameVersion, _delVersion,
  _manageColumns, _addColumn, _renameColumn, _delColumn,
  _newItem, _editItem, _saveItem, _delItem,
  _editRecord, _saveRecord, _upload, _pasteShot, _delAtt, _download, _viewShot, _viewVideo,
  _pickVersion, _setFilter, _setView,
  _dlcad, _delCad,
  _uploadFolder, _viewFolder, _dlFolder, _delFolder, _delFolderFile,
  _close: closeModal,
};
