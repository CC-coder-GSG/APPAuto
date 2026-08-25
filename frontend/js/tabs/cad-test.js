// CAD 测试统计 —— 版本分组卡片视图。
// 数据：统计表(board) → 版本(version) + 自定义列(column) + 条目(item) → 记录(item×version) → 附件(CAD/截图)。
// 依赖全局：api()、token、window.OmniQAUtils、window.OmniQAPreview、window.showMessage。

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
    root.innerHTML = `<div class="muted" style="padding:20px;">加载失败：${esc(e.message)}</div>`;
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

function render() {
  const root = document.getElementById('cadTestRoot');
  if (!root) return;
  root.innerHTML = `
    <div class="row" style="justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
      <div class="row" style="gap:10px; align-items:center; flex-wrap:wrap;">
        <h2 style="margin:0;">CAD测试统计</h2>
        <select id="cadBoardSelect" style="min-width:200px;">
          ${state.boards.map((b) => `<option value="${b.id}" ${b.id === state.boardId ? 'selected' : ''}>${esc(b.name)}</option>`).join('')}
        </select>
        <button class="secondary" onclick="window.OmniQACadTab._newBoard()">+ 新建统计表</button>
        ${state.board ? `<button class="secondary" onclick="window.OmniQACadTab._renameBoard()">重命名</button>
        <button class="secondary" onclick="window.OmniQACadTab._delBoard()" style="color:#dc2626;">删除表</button>` : ''}
      </div>
    </div>
    ${state.board ? renderBoardBody() : `<div class="muted" style="padding:30px; text-align:center;">还没有统计表，点击「+ 新建统计表」开始。</div>`}
  `;
  const sel = document.getElementById('cadBoardSelect');
  if (sel) sel.onchange = async () => { state.boardId = Number(sel.value); state.versionId = null; await loadBoard(); };
  const vsel = document.getElementById('cadVersionSelect');
  if (vsel) vsel.onchange = () => { state.versionId = Number(vsel.value); render(); };
  hydrateScreenshots(root);
}

function renderBoardBody() {
  const b = state.board;
  if (!b.versions.length) {
    return `<div class="card" style="margin-top:14px; background:#f8fafc;">
      <div class="muted">该统计表还没有版本。</div>
      <button style="margin-top:10px;" onclick="window.OmniQACadTab._newVersion()">+ 新增版本</button>
    </div>`;
  }
  const ver = b.versions.find((v) => v.id === state.versionId) || b.versions[0];
  const items = b.items;
  return `
    <div class="row" style="margin-top:14px; gap:10px; align-items:center; flex-wrap:wrap; background:#f8fafc; padding:12px; border-radius:10px; border:1px solid #e2e8f0;">
      <label style="color:#475569; font-size:13px;">版本</label>
      <select id="cadVersionSelect" style="min-width:240px;">
        ${b.versions.map((v) => `<option value="${v.id}" ${v.id === ver.id ? 'selected' : ''}>${esc(v.name)}</option>`).join('')}
      </select>
      <button class="secondary" onclick="window.OmniQACadTab._newVersion()">+ 版本</button>
      <button class="secondary" onclick="window.OmniQACadTab._renameVersion(${ver.id})">重命名版本</button>
      <button class="secondary" onclick="window.OmniQACadTab._delVersion(${ver.id})" style="color:#dc2626;">删版本</button>
      <span style="flex:1;"></span>
      <button class="secondary" onclick="window.OmniQACadTab._manageColumns()">自定义列</button>
      <button onclick="window.OmniQACadTab._newItem()">+ 新增条目</button>
    </div>
    <div style="margin-top:14px; display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:14px;">
      ${items.length ? items.map((it) => renderCard(it, ver.id)).join('') : '<div class="muted" style="padding:20px;">该统计表还没有条目，点击「+ 新增条目」。</div>'}
    </div>`;
}

function renderCard(item, versionId) {
  const rec = state.board.records[`${item.id}:${versionId}`];
  const bug = item.bug;
  const hasAbnormal = rec && rec.abnormal_count > 0;
  const headBug = item.zentao_bug_id
    ? `<span class="row" style="gap:4px; align-items:center;">
         <span class="badge" style="background:#eff6ff; color:#1d4ed8;">Bug ${item.zentao_bug_id}</span>
         ${U().renderPreviewBtn ? U().renderPreviewBtn('bug', item.zentao_bug_id) : ''}
         ${bug && bug.zentao_bug_url ? `<a class="qa-ext-link" href="${esc(bug.zentao_bug_url)}" target="_blank" rel="noopener" title="${esc(bug.zentao_bug_title || '')}">跳转</a>` : ''}
       </span>`
    : '';
  const { cad: cadFiles, video: videos, shot: shots } = splitAtts(rec);
  const customs = (state.board.columns || [])
    .map((c) => {
      const val = rec && rec.custom_values ? rec.custom_values[String(c.id)] : '';
      if (!val) return '';
      return `<div style="font-size:12px; color:#475569;"><b>${esc(c.name)}：</b>${esc(val)}</div>`;
    })
    .join('');

  const isNormal = rec && rec.normal_count > 0;
  const statusBadge = hasAbnormal
    ? `<span class="badge" style="background:#fef2f2; color:#dc2626; border:1px solid #fca5a5;">⚠ 异常</span>`
    : (isNormal
        ? `<span class="badge" style="background:#f0fdf4; color:#16a34a; border:1px solid #bbf7d0;">✓ 正常</span>`
        : `<span class="badge" style="background:#f1f5f9; color:#94a3b8; border:1px solid #e2e8f0;">未测</span>`);
  // 异常时整卡柔和红底 + 左侧红条，醒目但不刺眼。
  const cardStyle = hasAbnormal
    ? 'border:1px solid #fecaca; border-left:4px solid #f87171; background:#fef2f2;'
    : 'border:1px solid #e2e8f0; background:#fff;';

  return `
    <div class="card" style="padding:14px; ${cardStyle} display:flex; flex-direction:column; gap:8px;">
      <div class="row" style="justify-content:space-between; align-items:center;">
        <div class="row" style="gap:6px; align-items:center;">
          <span style="font-weight:700; color:#0f172a;">#${item.seq ?? '-'}</span>
          ${item.title ? `<span style="color:#334155;">${esc(item.title)}</span>` : ''}
        </div>
        ${headBug}
      </div>
      <div class="row" style="gap:14px; align-items:center;">
        ${statusBadge}
      </div>
      ${((item.cad_files && item.cad_files.length) || (item.cad_folders && item.cad_folders.length)) ? `<div style="border:1px dashed #2563eb; background:#eff6ff; border-radius:8px; padding:6px 9px;">
        <div style="font-size:11px; color:#1d4ed8; display:flex; align-items:center; gap:4px;">📎 共享 CAD 文件 · 各测试版本通用</div>
        <div class="row" style="flex-wrap:wrap; gap:6px; margin-top:5px;">
          ${(item.cad_folders || []).map((fd) => `<button class="secondary" style="padding:4px 10px; font-size:12px;" onclick="window.OmniQACadTab._viewFolder(${fd.id},${item.id},${versionId})" title="查看文件夹：${esc(fd.name)}（${fd.file_count} 个文件）">📁 ${esc(fd.name.length > 14 ? fd.name.slice(0, 14) + '…' : fd.name)} (${fd.file_count})</button>`).join('')}
          ${(item.cad_files || []).map((f) => `<button class="secondary" style="padding:4px 10px; font-size:12px;" onclick="window.OmniQACadTab._dlcad(${f.id})" title="下载：${esc(f.original_name)}">⬇ ${esc(f.original_name.length > 16 ? f.original_name.slice(0, 16) + '…' : f.original_name)}</button>`).join('')}
        </div>
      </div>` : ''}
      ${rec && rec.description ? `<div style="font-size:13px; color:#334155; white-space:pre-wrap; line-height:1.55; background:#f8fafc; border-left:3px solid ${hasAbnormal ? '#f87171' : '#94a3b8'}; border-radius:6px; padding:8px 10px;"><span style="color:#64748b; font-size:11px;">问题说明</span><br>${esc(rec.description)}</div>` : ''}
      ${(shots.length || videos.length) ? `<div class="row" style="flex-wrap:wrap; gap:8px; align-items:center;">
        ${shots.map((a) => `<img data-cad-src="${a.download_url}" style="width:56px;height:56px;object-fit:cover;border-radius:6px;border:1px solid #e2e8f0;cursor:pointer;" onclick="window.OmniQACadTab._viewShot(${a.id})" title="查看图片：${esc(a.original_name)}">`).join('')}
        ${videos.map((a) => `<button class="secondary" style="padding:4px 10px; font-size:12px;" onclick="window.OmniQACadTab._viewVideo(${a.id})" title="${esc(a.original_name)}">▶ 预览视频</button>`).join('')}
      </div>` : ''}
      ${customs}
      <div class="row" style="justify-content:flex-end; gap:6px; margin-top:2px;">
        <button class="secondary" style="padding:3px 8px; font-size:12px;" onclick="window.OmniQACadTab._editItem(${item.id})">编辑条目</button>
        <button style="padding:3px 8px; font-size:12px;" onclick="window.OmniQACadTab._editRecord(${item.id}, ${versionId})">填写/编辑</button>
      </div>
    </div>`;
}

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
  const name = prompt('统计表名称（如：40311版本测试统计表）');
  if (!name || !name.trim()) return;
  try {
    const res = await japi('/api/cad/boards', { method: 'POST', body: { name: name.trim() } });
    state.boardId = res.id; state.versionId = null;
    state.boards = await japi('/api/cad/boards');
    await loadBoard();
    toast('已创建统计表');
  } catch (e) { toast(e.message, 'error'); }
}
async function _renameBoard() {
  const name = prompt('重命名统计表', state.board?.name || '');
  if (!name || !name.trim()) return;
  try { await japi(`/api/cad/boards/${state.boardId}`, { method: 'PUT', body: { name: name.trim() } });
    state.boards = await japi('/api/cad/boards'); await loadBoard(); toast('已重命名'); } catch (e) { toast(e.message, 'error'); }
}
async function _delBoard() {
  if (!confirm(`确定删除统计表「${state.board?.name}」？该表下所有版本/条目/记录/附件将一并删除。`)) return;
  try { await japi(`/api/cad/boards/${state.boardId}`, { method: 'DELETE' });
    state.boardId = null; state.board = null; state.versionId = null;
    state.boards = await japi('/api/cad/boards');
    if (state.boards.length) state.boardId = state.boards[0].id;
    await loadBoard(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// -------------------------------------------------------------- version ops
async function _newVersion() {
  const name = prompt('版本名称（如：4.0.3.11.260603(40311022)版本测试）');
  if (!name || !name.trim()) return;
  try { const res = await japi(`/api/cad/boards/${state.boardId}/versions`, { method: 'POST', body: { name: name.trim() } });
    state.versionId = res.id; await loadBoard(); toast('已新增版本'); } catch (e) { toast(e.message, 'error'); }
}
async function _renameVersion(id) {
  const cur = state.board.versions.find((v) => v.id === id);
  const name = prompt('重命名版本', cur?.name || '');
  if (!name || !name.trim()) return;
  try { await japi(`/api/cad/versions/${id}`, { method: 'PUT', body: { name: name.trim() } }); await loadBoard(); toast('已重命名'); } catch (e) { toast(e.message, 'error'); }
}
async function _delVersion(id) {
  if (!confirm('删除该版本？该版本下的记录与附件将一并删除。')) return;
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
  if (!confirm('删除该自定义列？已填写的对应值将不再显示。')) return;
  try { await japi(`/api/cad/columns/${id}`, { method: 'DELETE' }); await loadBoard(); _manageColumns(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// ----------------------------------------------------------------- item ops
function _newItem() { openItemModal(null); }
function _editItem(id) { openItemModal(state.board.items.find((i) => i.id === id)); }
function openItemModal(item) {
  const body = `
    <div style="display:flex; flex-direction:column; gap:10px;">
      <label>序号<input id="cadItemSeq" type="number" value="${item?.seq ?? ''}" style="width:100%;" placeholder="留空自动顺延"></label>
      <label>标题/图纸主名（可选）<input id="cadItemTitle" value="${esc(item?.title || '')}" style="width:100%;"></label>
      <label>关联禅道 Bug ID（可选，填数字）<input id="cadItemBug" type="number" value="${item?.zentao_bug_id ?? ''}" style="width:100%;" placeholder="如 29614"></label>
    </div>
    <div class="row" style="justify-content:space-between; margin-top:14px;">
      ${item ? `<button class="secondary" style="color:#dc2626;" onclick="window.OmniQACadTab._delItem(${item.id})">删除条目</button>` : '<span></span>'}
      <button onclick="window.OmniQACadTab._saveItem(${item ? item.id : 'null'})">保存</button>
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
  if (!confirm('删除该条目？其所有版本下的记录与附件将一并删除。')) return;
  try { await japi(`/api/cad/items/${id}`, { method: 'DELETE' }); closeModal(); await loadBoard(); toast('已删除'); } catch (e) { toast(e.message, 'error'); }
}

// --------------------------------------------------------------- record edit
function _editRecord(itemId, versionId) {
  const item = state.board.items.find((i) => i.id === itemId);
  const rec = state.board.records[`${itemId}:${versionId}`];
  const cols = state.board.columns || [];
  const { cad: cadFiles, video: videos, shot: shots } = splitAtts(rec);
  const body = `
    <div style="display:flex; flex-direction:column; gap:12px;">
      <div class="muted">条目 #${item?.seq ?? '-'} ${item?.zentao_bug_id ? `· Bug ${item.zentao_bug_id}` : ''}</div>
      <div class="row" style="gap:20px; align-items:center;">
        <label style="display:flex; align-items:center; gap:6px; cursor:pointer; color:#16a34a; font-weight:600;">
          <input type="checkbox" id="cadRecNormal" ${rec && rec.normal_count > 0 ? 'checked' : ''} onchange="if(this.checked)document.getElementById('cadRecAbnormal').checked=false">✓ 正常
        </label>
        <label style="display:flex; align-items:center; gap:6px; cursor:pointer; color:#dc2626; font-weight:600;">
          <input type="checkbox" id="cadRecAbnormal" ${rec && rec.abnormal_count > 0 ? 'checked' : ''} onchange="if(this.checked)document.getElementById('cadRecNormal').checked=false">⚠ 异常
        </label>
        <span class="muted" style="font-size:12px;">勾选异常后，该卡片会以红色醒目标记</span>
      </div>
      <label>问题说明 / 异常说明
        <textarea id="cadRecDesc" rows="4" style="width:100%;">${esc(rec ? rec.description || '' : '')}</textarea>
      </label>
      ${cols.length ? `<div style="display:flex; flex-direction:column; gap:8px;">
        <div style="font-size:12px; color:#64748b;">自定义列</div>
        ${cols.map((c) => `<label>${esc(c.name)}<input data-cv="${c.id}" value="${esc(rec && rec.custom_values ? (rec.custom_values[String(c.id)] || '') : '')}" style="width:100%;"></label>`).join('')}
      </div>` : ''}
      <div style="border-top:1px dashed #e2e8f0; padding-top:10px; border:1px dashed #2563eb; border-radius:8px; padding:10px; background:#eff6ff;">
        <div class="row" style="justify-content:space-between; align-items:center;">
          <b style="font-size:13px; color:#1d4ed8;">📎 CAD 文件 / 文件夹（上传后该条目各版本共享）</b>
          <div class="row" style="gap:6px;">
            <label class="secondary" style="cursor:pointer; padding:4px 10px; border:1px solid #bfdbfe; border-radius:8px; background:#fff;">+ 上传CAD
              <input type="file" accept=".dwg,.dxf,.dwf,.dgn,.dwt" style="display:none;" onchange="window.OmniQACadTab._upload(${itemId},${versionId},'cad',this)">
            </label>
            <label class="secondary" style="cursor:pointer; padding:4px 10px; border:1px solid #bfdbfe; border-radius:8px; background:#fff;" title="上传一整个文件夹（含外部参照/整组图纸）">📁 上传文件夹
              <input type="file" webkitdirectory directory multiple style="display:none;" onchange="window.OmniQACadTab._uploadFolder(${itemId},${versionId},this)">
            </label>
          </div>
        </div>
        <div style="font-size:11px; color:#1d4ed8; margin-top:4px;">同一条目（同一张图纸 / 同一组图纸）的 CAD 在所有版本通用，无需每个版本重复上传。带外部参照或整组图纸时，可直接上传整个文件夹。</div>
        ${(item && item.cad_folders && item.cad_folders.length) ? `<div id="cadRecFolderList" style="display:flex; flex-wrap:wrap; gap:6px; margin-top:8px;">
          ${item.cad_folders.map((fd) => `<span class="row" style="gap:6px; align-items:center; background:#fff; border:1px solid #bfdbfe; border-radius:8px; padding:3px 8px;">
            <a class="qa-ext-link" href="javascript:void(0)" onclick="window.OmniQACadTab._viewFolder(${fd.id},${itemId},${versionId})" title="查看文件夹内容">📁 ${esc(fd.name)} <span style="color:#64748b;">(${fd.file_count})</span></a>
            <button onclick="window.OmniQACadTab._dlFolder(${fd.id})" title="打包下载整个文件夹" style="border:none;background:none;color:#2563eb;cursor:pointer;">⬇</button>
            <button onclick="window.OmniQACadTab._delFolder(${fd.id},${itemId},${versionId})" title="删除整个文件夹" style="border:none;background:none;color:#dc2626;cursor:pointer;">×</button>
          </span>`).join('')}
        </div>` : ''}
        <div id="cadRecCadList" style="display:flex; flex-wrap:wrap; gap:6px; margin-top:6px;">
          ${(item && item.cad_files && item.cad_files.length) ? item.cad_files.map((f) => `<span class="row" style="gap:4px; align-items:center; background:#fff; border:1px solid #bfdbfe; border-radius:8px; padding:3px 8px;">
            <a class="qa-ext-link" href="javascript:void(0)" onclick="window.OmniQACadTab._dlcad(${f.id})">📐 ${esc(f.original_name)}</a>
            <button onclick="window.OmniQACadTab._delCad(${f.id},${itemId},${versionId})" title="删除" style="border:none;background:none;color:#dc2626;cursor:pointer;">×</button>
          </span>`).join('') : ((item && item.cad_folders && item.cad_folders.length) ? '' : '<span class="muted" style="font-size:12px;">无</span>')}
        </div>
      </div>
      <div>
        <div class="row" style="justify-content:space-between; align-items:center;">
          <b style="font-size:13px;">截图</b>
          <div class="row" style="gap:6px;">
            <button class="secondary" style="cursor:pointer; padding:4px 10px; border:1px solid #c7d2fe; border-radius:8px; background:#eef2ff; color:#4338ca;" onclick="window.OmniQACadTab._pasteShot(${itemId},${versionId})">📋 粘贴截图</button>
            <label class="secondary" style="cursor:pointer; padding:4px 10px; border:1px solid #e2e8f0; border-radius:8px;">+ 上传截图
              <input type="file" accept="image/*" style="display:none;" onchange="window.OmniQACadTab._upload(${itemId},${versionId},'screenshot',this)">
            </label>
          </div>
        </div>
        <div style="margin-top:6px; font-size:12px; color:#6366f1; background:#eef2ff; border:1px dashed #c7d2fe; border-radius:8px; padding:6px 10px;">💡 用截图工具复制图片后，在此弹窗内直接按 <b>Ctrl+V</b> 即可粘贴上传，或点「📋 粘贴截图」。</div>
        <div id="cadRecShotList" style="display:flex; flex-wrap:wrap; gap:6px; margin-top:6px;">
          ${shots.map((a) => `<span style="position:relative; display:inline-block;">
            <img data-cad-src="${a.download_url}" style="width:64px;height:64px;object-fit:cover;border-radius:6px;border:1px solid #e2e8f0;">
            <button onclick="window.OmniQACadTab._delAtt(${a.id},${itemId},${versionId})" title="删除" style="position:absolute;top:-6px;right:-6px;width:18px;height:18px;border-radius:50%;border:none;background:#dc2626;color:#fff;cursor:pointer;line-height:1;">×</button>
          </span>`).join('') || '<span class="muted" style="font-size:12px;">无</span>'}
        </div>
      </div>
      <div>
        <div class="row" style="justify-content:space-between; align-items:center;">
          <b style="font-size:13px;">视频（支持在线播放）</b>
          <label class="secondary" style="cursor:pointer; padding:4px 10px; border:1px solid #e2e8f0; border-radius:8px;">+ 上传视频
            <input type="file" accept="video/*" style="display:none;" onchange="window.OmniQACadTab._upload(${itemId},${versionId},'video',this)">
          </label>
        </div>
        <div id="cadRecVideoList" style="display:flex; flex-wrap:wrap; gap:10px; margin-top:6px;">
          ${videos.map((a) => `<span style="position:relative; display:inline-block;">
            <video src="${streamSrc(a)}" controls preload="metadata" playsinline style="width:240px; max-height:160px; border-radius:8px; border:1px solid #e2e8f0; background:#000;"></video>
            <button onclick="window.OmniQACadTab._delAtt(${a.id},${itemId},${versionId})" title="删除" style="position:absolute;top:-6px;right:-6px;width:18px;height:18px;border-radius:50%;border:none;background:#dc2626;color:#fff;cursor:pointer;line-height:1;">×</button>
          </span>`).join('') || '<span class="muted" style="font-size:12px;">无</span>'}
        </div>
      </div>
    </div>
    <div class="row" style="justify-content:flex-end; margin-top:14px;">
      <button onclick="window.OmniQACadTab._saveRecord(${itemId},${versionId})">保存记录</button>
    </div>`;
  openModal('填写记录', body);
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
  if (!confirm(`将把文件夹「${folderName}」中的 ${files.length} 个文件作为整组归档上传，确定继续？`)) return;

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
  if (!confirm('删除该共享 CAD 文件？该条目所有版本都将不再显示它。')) return;
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
  if (!confirm(`删除整个文件夹「${found?.folder?.name || ''}」？其中所有文件将一并删除，该条目各版本都将不再显示。`)) return;
  try {
    await japi(`/api/cad/cad-folders/${folderId}`, { method: 'DELETE' });
    await loadBoard();
    if (itemId != null && versionId != null) _editRecord(itemId, versionId);
    else closeModal();
    toast('已删除文件夹');
  } catch (e) { toast(e.message, 'error'); }
}
async function _delFolderFile(fileId, folderId, itemId, versionId) {
  if (!confirm('从该文件夹中删除这个文件？')) return;
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
  if (!confirm('删除该附件？')) return;
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
function openModal(title, bodyHtml) {
  closeModal();
  const m = document.createElement('div');
  m.id = 'cadModal';
  m.style.cssText = 'position:fixed; inset:0; background:rgba(15,23,42,.42); z-index:10000; display:flex; align-items:center; justify-content:center;';
  m.onclick = (e) => { if (e.target === m) closeModal(); };
  m.innerHTML = `
    <div style="width:min(560px,94vw); max-height:90vh; overflow:auto; background:#fff; border-radius:14px; box-shadow:0 16px 40px rgba(0,0,0,.22); padding:20px;">
      <div class="row" style="justify-content:space-between; align-items:center; margin-bottom:12px;">
        <h3 style="margin:0; color:#0f172a;">${esc(title)}</h3>
        <button class="secondary" onclick="window.OmniQACadTab._close()">关闭</button>
      </div>
      <div id="cadModalBody">${bodyHtml}</div>
    </div>`;
  document.body.appendChild(m);
}
function closeModal() { const m = document.getElementById('cadModal'); if (m) m.remove(); }

window.OmniQACadTab = {
  activate,
  _newBoard, _renameBoard, _delBoard,
  _newVersion, _renameVersion, _delVersion,
  _manageColumns, _addColumn, _renameColumn, _delColumn,
  _newItem, _editItem, _saveItem, _delItem,
  _editRecord, _saveRecord, _upload, _pasteShot, _delAtt, _download, _viewShot, _viewVideo,
  _dlcad, _delCad,
  _uploadFolder, _viewFolder, _dlFolder, _delFolder, _delFolderFile,
  _close: closeModal,
};
